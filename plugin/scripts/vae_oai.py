"""A client for OpenAI-compatible chat endpoints (OpenAI, vLLM, llama.cpp, LM Studio, Ollama): text, images, tools.

WHY one client for LLM and VLM: both are `POST {base_url}/chat/completions`; a vision call only adds `image_url` content
parts, so the model role decides which model answers. WHY streaming by default: a reasoning model can think for
minutes, and a streamed reply resets the read timeout with every chunk instead of failing the whole call at
`timeout_s`; `"stream": false` in `hyperparams` switches it off for servers without SSE. VERIFIED: (2026-10-11, a vLLM
endpoint serving Qwen3.8) a streamed call returned its text, its reasoning separately, its usage and a native tool call.

Config, `.vae/.env.openai.json` (the key named by `auth_env` lives in `.vae/.env.openai`; no key sends no header):

    {"base_url": "http://127.0.0.1:8000/v1", "auth_env": "OPENAI_API_KEY",
     "models": {"reasoner": {"name": "...", "reasoning_effort": "high"},
                "vision": {"name": "...", "capabilities": ["vision", "ocr", "pdf"]},
                "editor": {"name": "...", "context_window": 262144, "max_tokens": 65536}},
     "hyperparams": {"temperature": 0.7, "max_tokens": 4000}, "timeout_s": 120, "attempts": 3}

A role's keys besides `name`, `context_window` and `capabilities` are request parameters and win over `hyperparams`.
`capabilities` names what the model serves itself from an attachment (`vae_caps` routes them): images go as `image_url`
parts, documents as `file` parts (a URL as `file_url`, a local file as a `file_data` data URI), videos as `video_url`.
VERIFIED: (2026-10-12, z.ai `glm-5.3-flash`) an image and a PDF, as `file_data` and as `file_url`, were read correctly;
DOCX, XLSX and PPTX failed to parse (400) in both forms, and two mp4 URLs failed (a 400, a 120 s timeout).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any, Callable

from vae_net import (
    ConfigError,
    http_error,
    iter_sse,
    load_pair,
    open_request,
    redact,
    request_json,
    secret,
)

ROLE_ONLY = {"name", "context_window", "capabilities"}
THINK = re.compile(r"^\s*<think>(.*?)</think>\s*", re.DOTALL)


class LlmError(RuntimeError):
    """A failed call; the message never holds the key."""


def is_url(source: str) -> bool:
    return bool(re.match(r"(?i)^(https?:|data:)", source))


def local_data(source: str, default_mime: str) -> tuple[str, str]:
    """(data URI, file name) of a local file; LlmError when it does not exist."""
    path = Path(source)
    if not path.is_file():
        raise LlmError(f"file not found: {source}")
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}", path.name


def image_part(source: str) -> dict[str, Any]:
    """An `image_url` content part: URLs and data URIs pass through, a local file becomes a base64 data URI."""
    return {"type": "image_url", "image_url": {"url": source if is_url(source) else local_data(source, "image/png")[0]}}


def file_part(source: str) -> dict[str, Any]:
    """A `file` content part (PDF, Office, text): a URL as `file_url`, a data URI or a local file as `file_data`."""
    if source[:5].lower() == "data:":
        return {"type": "file", "file": {"file_data": source}}
    if is_url(source):
        return {"type": "file", "file": {"file_url": source}}
    data, name = local_data(source, "application/octet-stream")
    return {"type": "file", "file": {"file_data": data, "filename": name}}


def video_part(source: str) -> dict[str, Any]:
    """A `video_url` content part; URLs only, the one form z.ai documents."""
    if not is_url(source):
        raise LlmError(f"a video goes to a model as a URL, not a local file: {source}")
    return {"type": "video_url", "video_url": {"url": source}}


class Llm:
    def __init__(self, settings: dict[str, Any], env: dict[str, str]):
        base = settings.get("base_url")
        if not isinstance(base, str) or not base.startswith(("http://", "https://")):
            raise ConfigError(".vae/.env.openai.json: `base_url` must be an http(s) URL")
        models = settings.get("models")
        if not isinstance(models, dict) or not models:
            raise ConfigError(".vae/.env.openai.json: `models` must map roles (reasoner, vision, ...) to {\"name\": ...}")
        for role, spec in models.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"]:
                raise ConfigError(f".vae/.env.openai.json: models.{role} needs a non-empty `name`")
            caps = spec.get("capabilities", [])
            if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
                raise ConfigError(f".vae/.env.openai.json: models.{role}.capabilities must be a list of capability names")
        self.base, self.models = base.rstrip("/"), models
        self.hyperparams = dict(settings.get("hyperparams") or {})
        self.timeout, self.attempts = float(settings.get("timeout_s", 120)), int(settings.get("attempts", 3))
        self.key = secret(settings, env)
        self.headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _err(self, text: str) -> LlmError:
        return LlmError(redact(text, [self.key] if self.key else []))

    def models_available(self) -> list[str]:
        """The model ids the endpoint serves (`GET /models`)."""
        try:
            reply = request_json(f"{self.base}/models", headers=self.headers, timeout=self.timeout, attempts=self.attempts)
        except Exception as e:  # noqa: BLE001 - every failure becomes one LlmError
            raise self._err(f"GET /models: {http_error(e)}") from None
        return [m.get("id") for m in reply.get("data", []) if isinstance(m, dict)] if isinstance(reply, dict) else []

    def chat(self, messages: list[dict[str, Any]], role: str = "reasoner", images: list[str] | None = None,
             tools: list[dict[str, Any]] | None = None, files: list[str] | None = None, videos: list[str] | None = None,
             trace: Callable[[dict[str, Any]], Any] | None = None, **params: Any) -> dict[str, Any]:
        """{text, reasoning, tool_calls, usage, model, finish_reason} of one completion. `images`, `files` and `videos`
        (paths or URLs) attach to the last user message; `tools` are OpenAI function tools; `params` override the
        configured request parameters. `trace` receives {at, url, method, request, response|error} per call: the
        request body and the parsed reply, never a header, so the key cannot reach it."""
        if role not in self.models:
            raise ConfigError(f".vae/.env.openai.json: no model for role {role!r} (configured: {', '.join(self.models)})")
        spec = self.models[role]
        parts = [image_part(s) for s in images or []] + [file_part(s) for s in files or []] + [video_part(s) for s in videos or []]
        if parts:
            messages = [dict(m) for m in messages]
            last = next(m for m in reversed(messages) if m.get("role") == "user")
            text = last.get("content")
            last["content"] = ([{"type": "text", "text": text}] if isinstance(text, str) else list(text or [])) + parts
        body = {**self.hyperparams, **{k: v for k, v in spec.items() if k not in ROLE_ONLY}, **params,
                "model": spec["name"], "messages": messages}
        if tools:
            body["tools"] = tools
        body.setdefault("stream", True)
        if body["stream"]:
            body.setdefault("stream_options", {"include_usage": True})
        # a copy: the caller keeps appending to `messages`, and the trace must show this request as sent
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "url": f"{self.base}/chat/completions",
                 "method": "POST", "request": json.loads(json.dumps(body))} if trace else {}
        try:
            with open_request(f"{self.base}/chat/completions", body, self.headers, timeout=self.timeout,
                              attempts=self.attempts) as resp:
                kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                out = collect_stream(resp) if kind == "text/event-stream" else message_of(json.loads(resp.read().decode("utf-8")))
        except Exception as e:  # noqa: BLE001 - every failure becomes one LlmError
            err = e if isinstance(e, LlmError) else self._err(f"POST /chat/completions ({spec['name']}): {http_error(e)}")
            if trace is not None:
                trace({**entry, "error": str(err)})
            raise err from None
        m = THINK.match(out["text"])
        if m:  # a server without a reasoning parser leaves the model's thinking inline
            out["reasoning"] = (out["reasoning"] + m.group(1)).strip()
            out["text"] = out["text"][m.end():]
        if trace is not None:
            trace({**entry, "response": out})
        return out


def message_of(reply: dict[str, Any]) -> dict[str, Any]:
    """The result dict of a non-streamed completion."""
    if not isinstance(reply, dict) or not reply.get("choices"):
        raise LlmError(f"no choices in the reply: {json.dumps(reply)[:200]}")
    choice = reply["choices"][0]
    msg = choice.get("message") or {}
    return {"text": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
            "tool_calls": msg.get("tool_calls") or [], "usage": reply.get("usage") or {},
            "model": reply.get("model"), "finish_reason": choice.get("finish_reason")}


def collect_stream(resp: Any) -> dict[str, Any]:
    """The result dict of a streamed completion: content, reasoning and tool-call deltas joined in order."""
    out: dict[str, Any] = {"text": "", "reasoning": "", "tool_calls": [], "usage": {}, "model": None, "finish_reason": None}
    calls: dict[int, dict[str, Any]] = {}
    for data in iter_sse(resp):
        if data.strip() == "[DONE]":
            break
        chunk = json.loads(data)
        if isinstance(chunk, dict) and chunk.get("error"):
            raise LlmError(f"the stream reported an error: {json.dumps(chunk['error'])[:300]}")
        out["model"] = chunk.get("model") or out["model"]
        out["usage"] = chunk.get("usage") or out["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            out["text"] += delta.get("content") or ""
            out["reasoning"] += delta.get("reasoning_content") or delta.get("reasoning") or ""
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(int(tc.get("index", 0)), {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            out["finish_reason"] = choice.get("finish_reason") or out["finish_reason"]
    out["tool_calls"] = [calls[i] for i in sorted(calls)]
    return out


def from_config(repo: Path) -> Llm | None:
    """The configured client, or None when `.vae/.env.openai.json` does not exist."""
    settings, env = load_pair(repo, "openai")
    return None if settings is None else Llm(settings, env)
