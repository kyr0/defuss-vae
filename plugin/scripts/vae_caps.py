"""Capabilities: what research can do (search the web, read a page, find papers, read an image), each served by an
ordered list of providers, the next one tried when one fails.

WHY a capability layer over raw MCP tools: prompts and callers name `websearch`, not `zai-web-search.web_search_prime`,
so swapping or adding a vendor is a config edit and one vendor's outage falls through to the next. WHY a fixed
vocabulary in code: the research prompts describe each capability and its inputs, so a capability exists only where
its description and input names are written down; providers stay free. VERIFIED: (2026-10-11, live) `repo_file` fell
through from zread, which did not know the repository, to CloakBrowser on raw.githubusercontent.com.

Config, the `capabilities` object in `.vae/.env.mcp.json` (no secrets here; `$name` fills a capability input):

    "capabilities": {
      "webscrape": [{"builtin": "cloak"}, {"server": "zai-web-reader", "tool": "webReader", "args": {"url": "$url"}}],
      "ocr": [{"server": "zai-vision", "tool": "extract_text_from_screenshot",
               "args": {"image_source": "$image", "prompt": "Extract all text verbatim."}}]}

A provider without `args` receives the capability inputs unchanged. The builtin `cloak` is CloakBrowser (input `url`).
`{"model": "<role>"}` is a model of `.vae/.env.openai.json` reading the input as an attachment. A provider fails on an
exception, a result with `isError`, or an empty text.

WHY models declare their capabilities (`models.<role>.capabilities` in `.vae/.env.openai.json`): what a model can read
(images, PDFs, Office files, videos) is a fact about the model, so it sits with the model; each declared capability gets
that model as a provider after the map's providers, unless the map places it explicitly. VERIFIED: (2026-10-12) z.ai's
`glm-5.3` rejected images (text only) while `glm-5.3-flash` read images and PDFs, so a role name alone says nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import vae_cloak
import vae_oai
from vae_mcp import McpClient, from_config, result_text, server_specs
from vae_net import ConfigError, fill

# name: ({input: required}, what it returns, for the prompts)
CAPABILITIES: dict[str, tuple[dict[str, bool], str]] = {
    "websearch": ({"query": True}, "searches the web; returns titles, links and snippets"),
    "webscrape": ({"url": True}, "reads one web page as text"),
    "pdf": ({"url": True}, "reads a PDF document (URL or local path) as text"),
    "document": ({"url": True}, "reads an Office document (DOCX, XLSX, PPTX; URL or local path) as text"),
    "paper_search": ({"query": True}, ("searches peer-reviewed papers (the first source for an academic question); returns ranked "
                                       "papers with authors, year, journal and links")),
    "ocr": ({"image": True}, "extracts the text in an image (local path or URL)"),
    "vision": ({"image": True, "prompt": True}, "answers a question about an image (local path or URL)"),
    "video": ({"video": True, "prompt": True}, "answers a question about a video (local path or URL)"),
    "repo_search": ({"repo": True, "query": True}, "searches a GitHub repository's docs, issues and commits (repo = owner/name)"),
    "repo_file": ({"repo": True, "path": True}, "reads one file of a GitHub repository (repo = owner/name)"),
    "repo_tree": ({"repo": True, "path": False}, "lists a GitHub repository's folders and files (repo = owner/name)"),
}
BUILTINS = {"cloak": {"url"}}
READ = "Return the full text of this document verbatim, in reading order; render tables as Markdown."
# capability a model can serve: (Llm.chat attachment argument, the input holding the attachment, default prompt)
MODEL_CAPABILITIES: dict[str, tuple[str, str, str | None]] = {
    "vision": ("images", "image", None),
    "ocr": ("images", "image", "Extract all text in the image verbatim, in reading order."),
    "video": ("videos", "video", None),
    "pdf": ("files", "url", READ),
    "document": ("files", "url", READ),
}


class CapabilityError(RuntimeError):
    """Every provider failed; `attempts` holds each provider's error."""

    def __init__(self, capability: str, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        tried = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts) or "no provider configured"
        super().__init__(f"{capability}: every provider failed ({tried})")


def label(provider: dict[str, Any]) -> str:
    if "model" in provider:
        return f"model:{provider['model']}"
    return provider["builtin"] if "builtin" in provider else f"{provider['server']}.{provider['tool']}"


def parse(raw: Any, servers: set[str], disabled: set[str]) -> dict[str, list[dict[str, Any]]]:
    """The validated capability map; providers on disabled servers are dropped, unknown names are a ConfigError."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(".vae/.env.mcp.json: `capabilities` must map capability names to provider lists")
    out = {}
    for name, providers in raw.items():
        where = f".vae/.env.mcp.json capabilities.{name}"
        if name not in CAPABILITIES:
            raise ConfigError(f"{where}: unknown capability (known: {', '.join(CAPABILITIES)})")
        if not isinstance(providers, list):
            raise ConfigError(f"{where}: must be a list of providers")
        kept = []
        for p in providers:
            if not isinstance(p, dict) or sum(k in p for k in ("builtin", "server", "model")) != 1:
                raise ConfigError(f"{where}: each provider is {{\"builtin\": ...}}, {{\"server\": ..., \"tool\": ...}} or {{\"model\": \"<role>\"}}")
            if "model" in p and (name not in MODEL_CAPABILITIES or not isinstance(p["model"], str) or not p["model"]):
                raise ConfigError(f"{where}: a model provider names a role, for a capability a model serves "
                                  f"({', '.join(MODEL_CAPABILITIES)})")
            if "builtin" in p and p["builtin"] not in BUILTINS:
                raise ConfigError(f"{where}: unknown builtin {p['builtin']!r} (known: {', '.join(BUILTINS)})")
            if "server" in p:
                if p["server"] in disabled:
                    continue
                if p["server"] not in servers:
                    raise ConfigError(f"{where}: server {p['server']!r} is not in `servers`")
                if not isinstance(p.get("tool"), str) or not p["tool"]:
                    raise ConfigError(f"{where}: provider on {p['server']!r} needs `tool`")
            if "args" in p and not isinstance(p["args"], dict):
                raise ConfigError(f"{where}: `args` must be an object")
            kept.append(p)
        out[name] = kept
    return out


def with_models(caps: dict[str, list[dict[str, Any]]], llm: vae_oai.Llm | None) -> dict[str, list[dict[str, Any]]]:
    """`caps` plus each model's declared capabilities: the role joins the end of the chain unless the map places it;
    an unknown declared name, or a placed role that does not declare the capability, is a ConfigError."""
    out = {name: list(providers) for name, providers in caps.items()}
    for role, spec in (llm.models if llm else {}).items():
        declared = spec.get("capabilities", [])
        for name in declared:
            if name not in MODEL_CAPABILITIES:
                raise ConfigError(f".vae/.env.openai.json models.{role}.capabilities: {name!r} is not a capability a model "
                                  f"serves ({', '.join(MODEL_CAPABILITIES)})")
            chain = out.setdefault(name, [])
            if not any(p.get("model") == role for p in chain):
                chain.append({"model": role})
        for name, providers in out.items():
            if any(p.get("model") == role for p in providers) and name not in declared:
                raise ConfigError(f".vae/.env.mcp.json capabilities.{name}: model {role!r} does not declare {name!r} "
                                  f"in .vae/.env.openai.json models.{role}.capabilities")
    return out


class Router:
    """Calls capabilities with fallback; owns the MCP clients it starts and closes them in `close()`."""

    def __init__(self, capabilities: dict[str, list[dict[str, Any]]], clients: dict[str, McpClient],
                 llm: vae_oai.Llm | None = None, cloak_timeout: float = 30.0):
        self.capabilities, self.clients, self.llm, self.cloak_timeout = capabilities, clients, llm, cloak_timeout

    def available(self) -> dict[str, list[str]]:
        """{capability: provider labels} for every capability with at least one usable provider."""
        out = {}
        for name, providers in self.capabilities.items():
            usable = [label(p) for p in providers if "model" not in p or (self.llm and p["model"] in self.llm.models)]
            if usable:
                out[name] = usable
        return out

    def call(self, capability: str, **inputs: Any) -> dict[str, Any]:
        """{capability, provider, text, attempts}: the first provider that answers; CapabilityError when none does."""
        if capability not in CAPABILITIES:
            raise CapabilityError(capability, [{"provider": "-", "error": "unknown capability"}])
        spec, _ = CAPABILITIES[capability]
        missing = [k for k, required in spec.items() if required and not inputs.get(k)]
        if missing:
            raise CapabilityError(capability, [{"provider": "-", "error": f"missing input {', '.join(missing)}"}])
        attempts: list[dict[str, Any]] = []
        for p in self.capabilities.get(capability, []):
            args = fill(p["args"], inputs) if "args" in p else {k: v for k, v in inputs.items() if v is not None}
            try:
                text = self._run(capability, p, args)
                if not text.strip():
                    raise RuntimeError("empty result")
                return {"capability": capability, "provider": label(p), "text": text, "attempts": attempts}
            except Exception as e:  # noqa: BLE001 - any provider failure falls through to the next
                attempts.append({"provider": label(p), "error": f"{type(e).__name__}: {e}"[:500]})
        raise CapabilityError(capability, attempts)

    def _run(self, capability: str, p: dict[str, Any], args: dict[str, Any]) -> str:
        if p.get("builtin") == "cloak":
            page = vae_cloak.fetch(str(args["url"]), timeout=self.cloak_timeout)
            if page.get("status") and page["status"] >= 400:
                raise RuntimeError(f"HTTP {page['status']}")
            return f"# {page.get('title') or ''}\n{page.get('url') or args['url']}\n\n{page.get('text') or ''}"
        if "model" in p:
            if self.llm is None or p["model"] not in self.llm.models:
                raise RuntimeError(f"no `{p['model']}` model in .vae/.env.openai.json")
            attach, source, default = MODEL_CAPABILITIES[capability]
            prompt = str(args.get("prompt") or default)
            return self.llm.chat([{"role": "user", "content": prompt}], role=p["model"], **{attach: [str(args[source])]})["text"]
        result = self.clients[p["server"]].call(p["tool"], args)
        if result.get("isError"):
            raise RuntimeError(result_text(result)[:300] or "the tool reported an error")
        return result_text(result)

    def close(self) -> None:
        for c in self.clients.values():
            c.close()


def from_repo(repo: Path) -> Router:
    """The router for this project: capabilities from `.vae/.env.mcp.json` plus the ones the models of
    `.vae/.env.openai.json` declare; either file may be missing."""
    specs, settings, _ = server_specs(repo)
    raw_servers = settings.get("servers") or {}
    disabled = {n for n, s in raw_servers.items() if isinstance(s, dict) and s.get("disabled")}
    try:
        llm = vae_oai.from_config(repo)
    except ConfigError:  # reported by its own doctor check; here it only means no model provider is available
        llm = None
    caps = with_models(parse(settings.get("capabilities"), set(specs), disabled), llm)
    return Router(caps, from_config(repo), llm, float(settings.get("cloak_timeout_s", 30)))
