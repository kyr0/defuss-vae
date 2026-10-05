"""Host event adapters (Claude Code, Codex): SessionStart context, Stop blocking, PreToolUse commit denial."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from vae_gate import gate, validate_docs, validate_review
from vae_repo import PLUGIN_ROOT, changed_since, code_fingerprint, git_root, is_gated
from vae_state import (
    STATE_BUDGET,
    attestation_path,
    episode_entries,
    init_session,
    load_session,
    memory_entries,
    state_path,
)
from vae_verify import PROBE_TAG

COMMIT_RE = re.compile(r"(?<![\w-])git(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*\s+commit\b")


def stop_gate(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    g = gate(repo, str(event.get("session_id") or "unknown"), plugin_root)
    if g.done:
        return None
    # VERIFIED: (hooks reference, checked 2026-10-05) Stop additionalContext continues the turn like a block, so
    # answering stop_hook_active with it re-invoked the model until the 8-continuation cap. Block once per turn; later
    # stops only tell the human (systemMessage) that the gate is open. The commit gate stays closed either way.
    if event.get("stop_hook_active"):
        head = g.text.splitlines()[0]
        return {"systemMessage": f"defuss-vae: turn ended with the gate open ({head}); git commit stays denied until vae.py gate reports VERIFIED[gate]=true."}
    return {"decision": "block", "reason": g.text}


def is_commit_command(command: str) -> bool:
    return COMMIT_RE.search(command) is not None


def commit_gate(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("tool_name") or "") not in {"Bash", "PowerShell"}:
        return None
    if not is_commit_command(str((event.get("tool_input") or {}).get("command") or "")):
        return None
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    state = load_session(repo, sid)
    changed = changed_since(repo, state["baseline"])
    code_changed = [p for p in changed if is_gated(p)]
    if not code_changed:
        return None
    fp = code_fingerprint(repo, code_changed)
    review_ok, review_reason = validate_review(attestation_path(repo, sid, "review"), fp, changed)
    docs_ok, docs_reason = validate_docs(attestation_path(repo, sid, "docs"), fp, changed, repo)
    verified = state.get("verified_fp") == fp
    if verified and review_ok and docs_ok:
        return None
    return deny(
        f"defuss-vae commit gate: VERIFIED[verifier]={str(verified).lower()}; review={review_reason}; docs={docs_reason}. "
        f"Run python3 {PLUGIN_ROOT}/scripts/vae.py gate --repo {repo} --session {sid} until VERIFIED[gate]=true."
    )


def deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


# One rule text for both channels: SessionStart context (Claude Code) and the AGENTS.md block (hosts without hooks).
RULES_TEXT = f"""Skills `plan` `implement` `review` `finalize` are human-triggered only; never auto-invoke them. Outside skills write plain concise prose.
Evidence > assumption: IF a runtime fact is unknown THEN observe before editing (read → existing test/command → smallest discriminating probe → ask). Temporary probe lines carry `{PROBE_TAG}` and the gate rejects leftovers; read logs bounded (`make log`, tail, grep); no log spraying.
Layout: `.agents/` agent state; `Makefile` verbs setup start stop status log metrics bench test coverage lint e2e verify; services only via `make start` → `var/log/<svc>.stdout|.stderr`, `tmp/<svc>.pid` (gitignored); programs read `input/`, write `output/` (both gitignored; commit e2e fixtures via `!input/<file>`).
test = real subsystems in isolation, no mocks; e2e = build the publishable artifact and consume it like a user; a web frontend's e2e drives the built app, served via `make start`, in a real Playwright browser (`bun add -d playwright` + `bunx playwright install --with-deps chromium` | `uv add --dev playwright` + `uv run playwright install --with-deps chromium`) with what the app needs enabled: WebGL2 (GPU-less CI: launch args `--use-angle=swiftshader --enable-unsafe-swiftshader`), real network, permissions via `context.grantPermissions([...])` (Python `grant_permissions`); assert rendered output, fail on console errors and failed requests, and write the report (`outputDir`) to `output/`. The gate fails closed without `.agents/VERIFY.py`, any verb, or `verify` running lint test coverage e2e, and when e2e leaves no fresh file in `output/`. A library without a service keeps the layout: `init` adds the ignores and one Makefile line `start stop restart status log: ; @echo "∅ $@: no service"` covers the service verbs; never disable `layout` for that. lint = `uv run ruff check .` (Python) | `bunx oxlint --deny-warnings` (JS/TS; plain oxlint exits 0 on findings). verify = lint + test + coverage + e2e; CI on a GitHub remote is `.github/workflows/verify.yml` running `make setup` then `make verify`.
Toolchain: new projects and subprojects start on `bun` (JS/TS, `bun init`) or `uv` (Python, `uv init`), never npm/yarn/pnpm/pip/poetry; the gate rejects newly added foreign lockfiles. In uv projects use `uv run`/`uv add`, not venv activation, which agent shells do not keep. A repo already on another toolchain keeps it unless the human approves migrating; propose it. Missing uv/bun: `make setup` installs them with the official installers (brand-new project: `curl -LsSf https://astral.sh/uv/install.sh | sh`, `curl -fsSL https://bun.sh/install | bash`).
Habits: separate concerns (pure core logic; I/O, config and framework glue at the edges) in small single-purpose modules testable with real inputs; split by responsibility, never speculatively. Logs: one line per event, ISO-8601 UTC timestamp first (`2026-10-01T12:00:00.123Z`), then level, message, key=value; never secrets. Config: env vars from a gitignored `.env` (bun loads it itself; Python `uv run --env-file .env`); every key the code reads stays in `.env.example` without secret values, updated in the same change (gate-checked); validate config once at startup and fail fast. Services exit cleanly on SIGTERM (`make stop`).
Epistemics: `VERIFIED` = direct evidence; `HYPOTHESIS` = testable inference + falsifier; `UNKNOWN` = not established. Never promote by rhetoric.
Ponytail: understand → YAGNI → reuse → stdlib → native → installed dependency → minimum code; bug fix = root cause + sibling callers.
Docs: why this design beats a plausible alternative; prefix material claims `VERIFIED:`, `HYPOTHESIS:` or `UNKNOWN:`.
Doc pages (`*.md|*.mdx`) are gated too: `vae.py prose --fix` + rewrite by meaning (em dashes, invisible|look-alike characters, broken links|fences|Mermaid), then review against the plugin's `references/PROSE.md`; schematic content (ordered steps, branches, states, components) → a rendered Mermaid diagram.
Lessons: test | `.agents/VERIFY.py` rule > MEMORY line > EPISODES line."""


def session_context(repo: Path, session_id: str, plugin_root: Path = PLUGIN_ROOT) -> str:
    parts = [
        "defuss-vae (verified agentic engineering):\n" + RULES_TEXT,
        (f"GATE before finishing code changes: python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {session_id} "
         "(repeat until VERIFIED[gate]=true; git commit is denied until then)."),
    ]
    # WHY inject instead of "go read": memory that is not loaded is not used; the budgets keep this cheap.
    for name, budget in STATE_BUDGET.items():
        entries = memory_entries(repo / ".agents" / name)
        if entries:
            parts.append(f".agents/{name}:\n" + "\n".join(entries)[:budget])
    recent = episode_entries(repo)[-3:]
    if recent:
        parts.append("Recent .agents/EPISODES.md:\n" + "\n".join(recent))
    return "\n".join(parts)[:9000]


def session_start(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    # VERIFIED: resume/compact can fire SessionStart for the same session; preserving the original baseline prevents gate bypass.
    if not state_path(repo, sid).exists():
        init_session(repo, sid)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": session_context(repo, sid, plugin_root)}}
