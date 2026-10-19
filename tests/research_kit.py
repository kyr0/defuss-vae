"""Real local servers for the research clients, no mocks: a scripted HTTP server on 127.0.0.1, an MCP server over
streamable HTTP, an OpenAI-compatible chat server, a TypeSafe System One server, and the stdio MCP server in
fixtures/mcp_fixture.py."""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable

from vae_testkit import PY  # also puts plugin/scripts on sys.path

MCP_STDIO = [PY, str(Path(__file__).resolve().parent / "fixtures" / "mcp_fixture.py")]
Handler = Callable[[str, str, Any, Any], "tuple[int, dict[str, str], bytes | list[bytes]]"]


class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # WHY: HTTPServer.server_bind resolves its own name (socket.getfqdn), and VERIFIED (tests/e2e.py) that reverse
        # lookup of 127.0.0.1 took more than 30 s on a CI macOS runner; the name is never used here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class HttpFixture:
    """`handler(method, path, headers, json_body)` → (status, headers, body); a list body is written chunk by chunk
    (server-sent events). Every request is kept in `requests` as (method, path, headers, json_body)."""

    def __init__(self, handler: Handler):
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        fixture = self

        class H(http.server.BaseHTTPRequestHandler):
            def handle_any(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                fixture.requests.append((self.command, self.path, dict(self.headers), body))
                status, headers, out = handler(self.command, self.path, self.headers, body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                for chunk in out if isinstance(out, list) else [out]:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            do_GET = do_POST = do_DELETE = handle_any

            def log_message(self, *args):
                pass

        self.httpd = Server(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def as_json(obj: Any, status: int = 200, **headers: str) -> tuple[int, dict[str, str], bytes]:
    return status, {"Content-Type": "application/json", **headers}, json.dumps(obj).encode()


def sse(*events: Any) -> tuple[int, dict[str, str], list[bytes]]:
    """An event stream: dicts as JSON data, strings verbatim (`[DONE]`)."""
    return 200, {"Content-Type": "text/event-stream"}, [
        f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n".encode() for e in events]


def mcp_http(tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]], session: str = "s-1") -> Handler:
    """A streamable-HTTP MCP server: a session id from initialize, required afterwards; tools/list in pages of one;
    tools/call answered as SSE after an unrelated notification event."""
    listing = [{"name": n, "description": f"fixture tool {n}", "inputSchema": {"type": "object"}} for n in tools]

    def handle(method: str, path: str, headers: Any, body: Any):
        if body.get("method") == "initialize":
            return as_json({"jsonrpc": "2.0", "id": body["id"], "result": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture-http", "version": "1"}}},
                **{"Mcp-Session-Id": session})
        if headers.get("Mcp-Session-Id") != session:
            return as_json({"error": "missing session"}, 400)
        if "id" not in body:
            return 202, {}, b""
        if body["method"] == "tools/list":
            i = int((body.get("params") or {}).get("cursor") or 0)
            page = {"tools": listing[i:i + 1], **({"nextCursor": str(i + 1)} if i + 1 < len(listing) else {})}
            return as_json({"jsonrpc": "2.0", "id": body["id"], "result": page})
        if body["method"] == "tools/call":
            p = body["params"]
            result = tools[p["name"]](p.get("arguments") or {}) if p["name"] in tools else None
            reply = {"jsonrpc": "2.0", "id": body["id"], **({"result": result} if result is not None else
                                                             {"error": {"code": -32602, "message": "unknown tool"}})}
            return sse({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}, reply)
        return as_json({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "unknown method"}})

    return handle


def llm(script: Callable[[dict[str, Any]], Any], models: tuple[str, ...] = ("m-reasoner", "m-vision"), key: str | None = None) -> Handler:
    """An OpenAI-compatible server: `script(request_body)` returns the answer text, or a dict with `text`,
    `reasoning`, `tool_calls` and `finish_reason`; streamed requests get it as SSE deltas plus a usage chunk."""

    def handle(method: str, path: str, headers: Any, body: Any):
        if key and headers.get("Authorization") != f"Bearer {key}":
            return as_json({"error": {"message": "bad key"}}, 401)
        if path.endswith("/models"):
            return as_json({"object": "list", "data": [{"id": m} for m in models]})
        answer = script(body)
        a = answer if isinstance(answer, dict) else {"text": answer}
        if isinstance(a.get("status"), int):
            return as_json(a.get("body", {}), a["status"])
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        finish = a.get("finish_reason") or ("tool_calls" if a.get("tool_calls") else "stop")
        if not body.get("stream"):
            msg = {"role": "assistant", "content": a.get("text", ""), "reasoning_content": a.get("reasoning", "")}
            if a.get("tool_calls"):
                msg["tool_calls"] = a["tool_calls"]
            return as_json({"model": body["model"], "choices": [{"message": msg, "finish_reason": finish}], "usage": usage})
        text = a.get("text", "")
        events: list[Any] = [{"model": body["model"], "choices": [{"delta": {"reasoning_content": a.get("reasoning", "")}}]},
                             {"choices": [{"delta": {"content": text[: len(text) // 2]}}]},
                             {"choices": [{"delta": {"content": text[len(text) // 2:]}}]}]
        for i, tc in enumerate(a.get("tool_calls") or []):
            args = tc["function"]["arguments"]
            events.append({"choices": [{"delta": {"tool_calls": [{"index": i, "id": tc["id"], "function": {"name": tc["function"]["name"], "arguments": args[:3]}}]}}]})
            events.append({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": args[3:]}}]}}]})
        events += [{"choices": [{"delta": {}, "finish_reason": finish}]}, {"choices": [], "usage": usage}, "[DONE]"]
        return sse(*events)

    return handle


def answer_of(question: dict[str, Any]) -> dict[str, Any]:
    """A well-formed answer of the question's type: the first label, P(yes)=0.9, or the top level."""
    if question["type"] == "choice":
        labels = list(question["criteria"])
        return {"type": "choice", "choice": labels[0], "confidence": 0.95,
                "probabilities": {k: 0.95 if i == 0 else 0.05 / max(1, len(labels) - 1) for i, k in enumerate(labels)}}
    if question["type"] == "noul":
        return {"type": "noul", "noul": 0.9}
    levels = {str(i): c for i, c in enumerate(question["criteria"])}
    return {"type": "score", "score": float(len(levels) - 1), "confidence": 0.9, "legend": levels,
            "probabilities": {k: 1.0 if int(k) == len(levels) - 1 else 0.0 for k in levels}}


def typesafe(answers: Callable[[dict[str, Any]], dict[str, Any]] | None = None, models: tuple[str, ...] = ("jev-latest", "jev-preview"),
             key: str | None = None) -> Handler:
    """A TypeSafe System One server (wire schema of typesafe-sdk-python 0.7): GET /v1/models; POST /v1/systemone answers
    every question with `answers(body)` (default: answer_of each), a 422 `detail` list without `state`, and the
    version `jev-1.0.0` for the alias."""

    def handle(method: str, path: str, headers: Any, body: Any):
        if key and headers.get("Authorization") != f"Bearer {key}":
            return as_json({"error": "Invalid API key"}, 401)
        if method == "GET" and path == "/v1/models":
            return as_json({"models": [{"name": m, "description": "fixture", "release_date": "2026-09-10"} for m in models]})
        if method != "POST" or path != "/v1/systemone":
            return as_json({"detail": "Not Found"}, 404)
        if "state" not in body:
            return as_json({"detail": [{"loc": ["body", "state"], "msg": "Field required", "type": "missing"}]}, 422)
        out = answers(body) if answers else {n: answer_of(q) for n, q in body["questions"].items()}
        return as_json({"model": "jev-1.0.0", "answers": out, "usage": {"input_tokens": 100, "output_tokens": 7}})

    return handle


def write_pairs(write: Callable[[str, str], Any], llm_url: str | None = None, mcp: dict[str, Any] | None = None,
                decide_url: str | None = None, secret: str = "fixture-secret-123456", vision_caps: list[str] | None = None) -> None:
    """Write the research config pairs into a test repo through RepoCase.write; every secret file holds `secret`;
    `vision_caps` become the vision model's declared capabilities."""
    if llm_url:
        vision = {"name": "m-vision", **({"capabilities": vision_caps} if vision_caps is not None else {})}
        write(".vae/.env.openai", f"OPENAI_API_KEY={secret}\n")
        write(".vae/.env.openai.json", json.dumps({"base_url": llm_url + "/v1", "auth_env": "OPENAI_API_KEY",
                                                   "models": {"reasoner": {"name": "m-reasoner"}, "vision": vision},
                                                   "hyperparams": {"max_tokens": 100}, "timeout_s": 10, "attempts": 1}))
    if mcp is not None:
        write(".vae/.env.mcp", f"FIX_TOKEN={secret}\n")
        write(".vae/.env.mcp.json", json.dumps(mcp))
    if decide_url:
        write(".vae/.env.typed-decisions", f"TYPED_DECISIONS_API_KEY={secret}\n")
        write(".vae/.env.typed-decisions.json", json.dumps({
            "base_url": decide_url, "model": "jev-latest", "auth_env": "TYPED_DECISIONS_API_KEY", "threshold": 0.8}))


def stdio_server(**extra: Any) -> dict[str, Any]:
    return {"type": "stdio", "command": MCP_STDIO[0], "args": MCP_STDIO[1:], "env": {"FIXTURE_TOKEN": "${FIX_TOKEN}"}, **extra}
