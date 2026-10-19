"""A minimal MCP client: stdio and streamable HTTP transports, `initialize`, `tools/list`, `tools/call`.

WHY only tools: research needs to discover and call tools; resources, prompts and sampling add protocol surface no
caller uses. WHY both transports: the spec says clients SHOULD support stdio, and hosted servers (z.ai, Consensus)
speak streamable HTTP (VERIFIED: modelcontextprotocol.io spec 2025-11-25, basic/transports, checked 2026-10-11).

Config, `.vae/.env.mcp.json` (the server entries copy-paste from Claude Code, Cline or Roo `mcpServers` configs; keys
this client does not use, such as `alwaysAllow`, are ignored):

    {"servers": {"<name>": {"type": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {"KEY": "${SECRET}"}},
                 "<name>": {"type": "http", "url": "https://...", "headers": {"Authorization": "Bearer ${SECRET}"}}},
     "timeout_s": 30, "start_timeout_s": 120}

`${NAME}` resolves from `.vae/.env.mcp`, then the environment; a server with `"disabled": true` is skipped.
"""
from __future__ import annotations

import collections
import itertools
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from vae_net import (
    VAR,
    ConfigError,
    expand,
    http_error,
    iter_sse,
    load_pair,
    open_request,
    redact,
)

PROTOCOL = "2025-06-18"
HTTP_TYPES = {"http", "streamable-http", "streamableHttp", "streamable_http"}
CLIENT = {"name": "defuss-vae", "version": "1"}


class McpError(RuntimeError):
    """A transport, protocol or JSON-RPC failure; the message never holds a secret value."""


def server_specs(repo: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    """({name: expanded spec}, settings, secret values) for every enabled server; ({}, {}, []) when unconfigured."""
    settings, env = load_pair(repo, "mcp")
    if settings is None:
        return {}, {}, []
    servers = settings.get("servers")
    if not isinstance(servers, dict):
        raise ConfigError(".vae/.env.mcp.json: `servers` must be an object of named servers")
    out = {}
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            raise ConfigError(f".vae/.env.mcp.json: servers.{name} must be an object")
        if raw.get("disabled"):
            continue
        kind = raw.get("type") or ("stdio" if raw.get("command") else "http")
        if kind != "stdio" and kind not in HTTP_TYPES:
            raise ConfigError(f".vae/.env.mcp.json: servers.{name}: transport {kind!r} is not stdio or streamable HTTP")
        if kind == "stdio" and not raw.get("command"):
            raise ConfigError(f".vae/.env.mcp.json: servers.{name}: a stdio server needs `command`")
        if kind != "stdio" and not raw.get("url"):
            raise ConfigError(f".vae/.env.mcp.json: servers.{name}: an HTTP server needs `url`")
        spec = {k: expand(raw[k], env, f".vae/.env.mcp.json servers.{name}.{k}")
                for k in ("command", "args", "env", "url", "headers") if raw.get(k)}
        spec["type"] = "stdio" if kind == "stdio" else "http"
        for k in ("timeout_s", "start_timeout_s"):
            spec[k] = float(raw.get(k) or settings.get(k) or (30 if k == "timeout_s" else 120))
        out[name] = spec
    # Every value a server entry received from a variable, so errors from any server can be redacted.
    used = {m for s in servers.values() if isinstance(s, dict) for m in _vars(s)}
    return out, settings, [env[v] for v in used if env.get(v)]


def _vars(value: Any) -> set[str]:
    if isinstance(value, str):
        return {a or b for a, b in VAR.findall(value)}
    if isinstance(value, (list, tuple)):
        return set().union(*(_vars(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return _vars(list(value.values()))
    return set()


class _Stdio:
    """A server subprocess speaking newline-delimited JSON-RPC on stdin/stdout; stderr is its log (last 20 lines kept)."""

    def __init__(self, spec: dict[str, Any], cwd: Path):
        cmd = spec["command"] if isinstance(spec["command"], list) else [spec["command"]]
        try:
            self.proc = subprocess.Popen(
                [*cmd, *spec.get("args", [])], cwd=str(cwd), env={**os.environ, **spec.get("env", {})},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            raise McpError(f"cannot start {cmd[0]}: {e}") from None
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.log: collections.deque[str] = collections.deque(maxlen=20)
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=f, daemon=True) for f in (self._read, self._drain)]
        for th in self.threads:
            th.start()

    def _read(self) -> None:
        for raw in self.proc.stdout:  # type: ignore[union-attr]
            try:
                msg = json.loads(raw)
            except ValueError:
                continue  # a banner a server printed to stdout by mistake
            if isinstance(msg, dict):
                self.inbox.put(msg)
        self.inbox.put(None)

    def _drain(self) -> None:
        for raw in self.proc.stderr:  # type: ignore[union-attr]
            self.log.append(raw.decode("utf-8", "replace").rstrip())

    def send(self, msg: dict[str, Any]) -> None:
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except OSError as e:
                raise McpError(f"server closed its input: {e}; stderr: {' | '.join(self.log)}") from None

    def request(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.send(msg)
        while True:
            try:
                reply = self.inbox.get(timeout=timeout)
            except queue.Empty:
                raise McpError(f"no reply to {msg['method']} within {timeout:g} s") from None
            if reply is None:
                raise McpError(f"server exited (code {self.proc.poll()}); stderr: {' | '.join(self.log)}")
            if reply.get("id") == msg["id"] and "method" not in reply:
                return reply
            if "method" in reply and "id" in reply:  # a request from the server: answer ping, refuse the rest
                ok = reply["method"] == "ping"
                self.send({"jsonrpc": "2.0", "id": reply["id"], **({"result": {}} if ok else
                           {"error": {"code": -32601, "message": "not supported by this client"}})})

    def notify(self, msg: dict[str, Any], timeout: float) -> None:
        self.send(msg)

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        for th in self.threads:  # both readers end at EOF once the process is gone
            th.join(timeout=2)
        self.proc.stdout.close()  # type: ignore[union-attr]
        self.proc.stderr.close()  # type: ignore[union-attr]


class _Http:
    """Streamable HTTP: every message is one POST; the reply is JSON or an SSE stream holding the response."""

    def __init__(self, spec: dict[str, Any]):
        self.url, self.headers = spec["url"], dict(spec.get("headers", {}))

    def _post(self, msg: dict[str, Any], timeout: float) -> Any:
        hdrs = {**self.headers, "Accept": "application/json, text/event-stream"}
        try:
            return open_request(self.url, msg, hdrs, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - every transport failure becomes one McpError
            raise McpError(f"{msg.get('method')}: {http_error(e)}") from None

    def request(self, msg: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._post(msg, timeout) as resp:
            if resp.headers.get("Mcp-Session-Id"):
                self.headers["Mcp-Session-Id"] = resp.headers["Mcp-Session-Id"]
            kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            try:
                if kind == "text/event-stream":
                    for data in iter_sse(resp):
                        reply = json.loads(data)
                        if isinstance(reply, dict) and reply.get("id") == msg["id"] and "method" not in reply:
                            return reply
                    raise McpError(f"{msg['method']}: the event stream ended without a response")
                reply = json.loads(resp.read().decode("utf-8"))
            except (ValueError, OSError) as e:
                raise McpError(f"{msg['method']}: unreadable reply ({type(e).__name__}: {e})") from None
        if isinstance(reply, list):
            reply = next((r for r in reply if isinstance(r, dict) and r.get("id") == msg["id"]), None)
        if not isinstance(reply, dict):
            raise McpError(f"{msg['method']}: no JSON-RPC response in the reply")
        return reply

    def notify(self, msg: dict[str, Any], timeout: float) -> None:
        with self._post(msg, timeout):
            pass  # 202 Accepted, no body

    def close(self) -> None:
        pass


class McpClient:
    """One server; `start()` runs the handshake once, later calls reuse the session. Calls from several threads take
    turns (one lock per server): a stdio server answers on one pipe, so concurrent waiters would take each other's replies."""

    def __init__(self, name: str, spec: dict[str, Any], cwd: Path, secrets: list[str] | None = None):
        self.name, self.spec, self.cwd, self.secrets = name, spec, cwd, secrets or []
        self.transport: _Stdio | _Http | None = None
        self.server_info: dict[str, Any] = {}
        self.ids = itertools.count(1)
        self.lock = threading.RLock()

    def _err(self, text: str) -> McpError:
        return McpError(redact(f"{self.name}: {text}", self.secrets))

    def start(self) -> McpClient:
        with self.lock:
            return self._start()

    def _start(self) -> McpClient:
        if self.transport is not None:
            return self
        try:
            self.transport = _Stdio(self.spec, self.cwd) if self.spec["type"] == "stdio" else _Http(self.spec)
            result = self._call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT},
                                self.spec["start_timeout_s"])
            version = result.get("protocolVersion")
            if isinstance(self.transport, _Http) and isinstance(version, str):
                self.transport.headers["MCP-Protocol-Version"] = version
            self.server_info = {"protocolVersion": version, **(result.get("serverInfo") or {})}
            self.transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.spec["timeout_s"])
        except McpError as e:
            self.close()
            raise self._err(str(e)) from None
        return self

    def _call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        assert self.transport is not None
        reply = self.transport.request({"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params},
                                       timeout or self.spec["timeout_s"])
        if "error" in reply:
            err = reply["error"] if isinstance(reply["error"], dict) else {"message": str(reply["error"])}
            raise McpError(f"{method}: JSON-RPC error {err.get('code')}: {err.get('message')}")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise McpError(f"{method}: the response has no result object")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool (`name`, `description`, `inputSchema`), following `nextCursor` pages."""
        tools: list[dict[str, Any]] = []
        cursor = None
        with self.lock:
            self._start()
            try:
                for _ in range(50):
                    result = self._call("tools/list", {"cursor": cursor} if cursor else {})
                    tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
                    cursor = result.get("nextCursor")
                    if not cursor:
                        break
            except McpError as e:
                raise self._err(str(e)) from None
        return tools

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """The raw `tools/call` result: `content` blocks, optional `isError` and `structuredContent`."""
        with self.lock:
            self._start()
            try:
                return self._call("tools/call", {"name": tool, "arguments": args})
            except McpError as e:
                raise self._err(str(e)) from None

    def close(self) -> None:
        with self.lock:
            if self.transport is not None:
                self.transport.close()
                self.transport = None


def result_text(result: dict[str, Any]) -> str:
    """The text of a tool result: text blocks joined, other blocks named by type, structured content as JSON."""
    parts = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "resource" and isinstance(block.get("resource"), dict):
            parts.append(str(block["resource"].get("text") or f"[resource {block['resource'].get('uri')}]"))
        else:
            parts.append(f"[{block.get('type')} block]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(parts)


def from_config(repo: Path) -> dict[str, McpClient]:
    """An unstarted client per enabled server in `.vae/.env.mcp.json`; {} when MCP is not configured."""
    specs, _, secrets = server_specs(repo)
    return {name: McpClient(name, spec, repo, secrets) for name, spec in specs.items()}
