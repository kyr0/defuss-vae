"""Config pairs and HTTP for the research clients: `.vae/.env.<name>` secrets, `.vae/.env.<name>.json` settings,
JSON and server-sent events over urllib.

WHY stdlib only: the scripts run on plain python3 >= 3.9 in any target project, with nothing to install, and the
MCP, OpenAI-compatible and typed-decision protocols each need only JSON over HTTP (plus SSE for streamed replies).
VERIFIED: (2026-10-11) Consensus's and z.ai's MCP servers and a vLLM endpoint answered through these helpers, JSON
and SSE replies alike.
WHY a pair per system: the JSON twin names endpoints, models and limits and holds no secret, so it can be reviewed and
shared; the plain twin holds only `KEY=value` secrets and stays gitignored.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from vae_repo import STATE_DIR

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class ConfigError(ValueError):
    """A config pair that cannot be used; the message names the file and key, never a secret value."""


def load_env(path: Path) -> dict[str, str]:
    """`KEY=value` lines: `#` comments, blank lines, an optional `export `, one pair of matching quotes stripped."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def load_pair(repo: Path, name: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """(settings, secrets) of `.vae/.env.<name>[.json]`; settings is None when the system is not configured.

    WHY the file before the process environment: the pair is specific to this project's endpoint, so a shell-wide key
    meant for another endpoint must not reach it; the process environment only fills keys the file lacks."""
    base = repo / STATE_DIR / f".env.{name}"
    path = base.with_name(base.name + ".json")
    if not path.is_file():
        return None, {}
    try:
        settings = json.loads(path.read_text("utf-8"))
    except ValueError as e:
        raise ConfigError(f"{path.relative_to(repo)}: invalid JSON ({e})") from None
    if not isinstance(settings, dict):
        raise ConfigError(f"{path.relative_to(repo)}: the top level must be an object")
    return settings, {**os.environ, **load_env(base)}


def expand(value: Any, env: dict[str, str], where: str) -> Any:
    """`${NAME}` and `$NAME` in strings, lists and dict values; a missing or empty variable is a ConfigError."""
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            name = m.group(1) or m.group(2)
            if not env.get(name):
                raise ConfigError(f"{where}: ${{{name}}} is not set in the secret file or the environment")
            return env[name]
        return VAR.sub(sub, value)
    if isinstance(value, list):
        return [expand(v, env, where) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, env, where) for k, v in value.items()}
    return value


def fill(template: Any, values: dict[str, Any]) -> Any:
    """A request template with `$name` placeholders filled from `values`: a string that is exactly one placeholder
    takes the value with its type (a list stays a list) and is dropped from a dict when the value is None; a
    placeholder inside a longer string is replaced by the value's text. WHY separate from `expand`: templates take
    call inputs, never secrets, so a question can never pull a key out of the secret file."""
    if isinstance(template, str):
        m = VAR.fullmatch(template)
        if m:
            return values.get(m.group(1) or m.group(2))
        return VAR.sub(lambda m: str(values.get(m.group(1) or m.group(2)) or ""), template)
    if isinstance(template, list):
        return [fill(v, values) for v in template]
    if isinstance(template, dict):
        out = {k: fill(v, values) for k, v in template.items()}
        return {k: v for k, v in out.items() if v is not None}
    return template


def secret(settings: dict[str, Any], env: dict[str, str], key: str = "auth_env") -> str | None:
    """The secret named by `settings[key]`, or None when the setting or its value is absent."""
    name = settings.get(key)
    return env.get(name) or None if isinstance(name, str) and name else None


def redact(text: str, secrets: list[str]) -> str:
    """Replace every secret value (8+ chars, so short common words survive) in an error text by `***`."""
    for s in sorted((s for s in secrets if s and len(s) >= 8), key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def http_error(e: BaseException) -> str:
    """One line for a failed request: status and the start of the body for HTTP errors, else the exception."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read(300).decode("utf-8", "replace")
        except OSError:
            body = ""
        e.close()  # an HTTP error carries the open response
        return f"HTTP {e.code} {e.reason}: {body}".strip()
    if isinstance(e, urllib.error.URLError):
        return f"URLError: {e.reason}"
    return f"{type(e).__name__}: {e}"


def open_request(url: str, body: Any = None, headers: dict[str, str] | None = None, method: str | None = None,
                 timeout: float = 30.0, attempts: int = 1) -> Any:
    """The open response of one HTTP request; retries connection failures, timeouts and 408/425/429/5xx with a
    0.5 s, 1 s, 2 s ... backoff. The caller closes the response (it is a context manager)."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Accept": "application/json", **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})}
    for n in range(max(1, attempts)):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method or ("POST" if data is not None else "GET"))
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or n + 1 >= attempts:
                raise
            e.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if n + 1 >= attempts:
                raise
        time.sleep(0.5 * 2 ** n)
    raise AssertionError("unreachable")


def request_json(url: str, body: Any = None, headers: dict[str, str] | None = None, timeout: float = 30.0,
                 attempts: int = 1) -> Any:
    """The decoded JSON reply of one request (see open_request for retries)."""
    with open_request(url, body, headers, timeout=timeout, attempts=attempts) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iter_sse(resp: Any) -> Iterator[str]:
    """The `data` of each server-sent event; multi-line data joined by newlines, comments and other fields skipped."""
    data: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield "\n".join(data)
