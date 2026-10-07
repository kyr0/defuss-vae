"""Host event adapters (Claude Code, Codex): SessionStart context, Stop blocking, PreToolUse commit denial."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from vae_gate import gate, validate_docs, validate_review
from vae_repo import (
    PLUGIN_ROOT,
    changed_since,
    code_fingerprint,
    git_root,
    is_gated,
    listed_files,
    stacks,
)
from vae_state import (
    STATE_BUDGET,
    attestation_path,
    episode_entries,
    init_session,
    is_lead,
    load_session,
    memory_entries,
    state_path,
)
from vae_swarm import SWARM_FILE, summary, swarm_root
from vae_verify import PROBE_TAG, stack_defaults

COMMIT_RE = re.compile(r"(?<![\w-])git(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*\s+commit\b")


def stop_gate(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    g = gate(repo, sid, plugin_root)
    if g.done:
        warns = [ln[len("WARNS "):] for ln in g.text.splitlines() if ln.startswith("WARNS ")]
        # Non-blocking: a systemMessage ends the turn normally; the agent already saw these in the review|docs text.
        return {"systemMessage": "defuss-vae: gate green; warnings (CONFIG['strict']=True blocks them): " + "; ".join(warns)} if warns else None
    # VERIFIED: (hooks reference, checked 2026-10-05) Stop additionalContext continues the turn like a block, so
    # answering stop_hook_active with it re-invoked the model until the 8-continuation cap. Block once per turn; later
    # stops only tell the human (systemMessage) that the gate is open. The commit gate stays closed either way.
    if event.get("stop_hook_active"):
        head = g.text.splitlines()[0]
        remains = next((ln for ln in g.text.splitlines() if ln.startswith("REMAINS:")), "")
        return {"systemMessage": (
            f"defuss-vae: turn ended with the gate open ({head}{'; ' + remains if remains else ''}). git commit stays denied until "
            f"`python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {sid}` reports "
            "VERIFIED[gate]=true. Agent: fix the failing check yourself and rerun the gate; do not bypass it. "
            "Human: reply \"continue\" to let the agent fix it, or answer the one decision it asked you for.")}
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
RULES_TEXT = f"""Outside skills write plain concise prose.
Evidence > assumption: IF a runtime fact is unknown or contested THEN observe before editing (read → existing test/command → smallest discriminating probe → ask). Temporary probe lines carry `{PROBE_TAG}` and the gate rejects leftovers; read logs bounded (`make log`, tail, grep); no log spraying.
Layout: `.agents/` agent state; `Makefile` verbs setup start stop status log metrics bench test coverage lint e2e verify (`make` lists them); services only via `make start` → `var/log/<svc>.stdout|.stderr`, `tmp/<svc>.pid` (gitignored); programs read `input/`, write `output/` (both gitignored; commit e2e fixtures via `!input/<file>`).
test=real subsystems in isolation (throwaway db|queue|filesystem|server process, no mocks), never live|production data or services; tests assert VERIFIED requirements only (spec|human|observed contract): HYPOTHESIS → probe, UNKNOWN → ask, neither gets a test; coverage is Pareto: test the untested public behaviors + main error paths, not lines (gate floor 60%); a regression test only for a VERIFIED code defect, never pinning env|config values that worked once (fix + validate at startup instead); e2e=build the publishable artifact and consume it like a user, covering EVERY page|route|screen|component of a UI (real Playwright browser) and EVERY CLI command|API endpoint at least once. The gate fails closed without `.agents/VERIFY.py`, any verb, or `verify` running lint test coverage e2e, and when e2e leaves no fresh file in `output/`. CI runs async: after a push report the run URL and finish, NOT wait for it (`gh run watch`), since the local gate is the proof (`init` writes `.github/workflows/verify.yml`; commands in `CONFIG["ci"]`).
Toolchain: tool versions pinned in `mise.toml` (`make setup` runs `mise install`) and the ecosystem's own pin; new projects and subprojects start on `bun` (JS/TS, `bun init`) or `uv` (Python, `uv init`), never npm/yarn/pnpm/pip/poetry (their new lockfiles fail the gate); other stacks: the plugin's `references/STACKS.md`. An existing toolchain stays unless the human approves migrating; propose it.
Habits: separate concerns (pure core logic; I/O, config and framework glue at the edges) in small single-purpose modules testable with real inputs; split by responsibility, never speculatively. Logs: one line per event, ISO-8601 UTC timestamp first (`2026-10-01T12:00:00.123Z`), then level, message, key=value; never secrets. Config: env vars from a gitignored `.env`, exported by the Makefile to every recipe and the app; every key the code reads stays in `.env.example` without secret values (gate-checked); validate config once at startup, fail fast. Services exit cleanly on SIGTERM.
Long runs: check free disk before big writes, CPU|RAM|GPU before heavy jobs (`vae.py swarm status`); run them detached from the shell (an SSH drop cannot kill them) with a pid file and a log appended per line with an ISO-8601 timestamp: services `make start`, jobs `vae.py swarm spawn`. Local HTTPS|reverse proxy: Caddy's internal CA (`caddy reverse-proxy --from localhost:8443 --to :3000`).
Sub-agents (hard rule): split only into units with disjoint target paths AND explicit contracts, else stay sequential; one git worktree per unit outside the repo (`../<repo>.wt/<name>`; without git, a directory no other agent claims), small chunks, results on disk early; register|update|remove only via `vae.py swarm` (`.agents/SWARM_STATUS.yaml`: one entry per live agent, owner-written, re-checked after 3 s); the orchestrator runs `vae.py swarm status` on a timer (harness scheduler|cron) every ~ETA/4, 5 to 30 min, and never trusts silence.
Epistemics: `VERIFIED`=direct evidence; `HYPOTHESIS`=testable inference + falsifier; `UNKNOWN`=not established. Never promote or widen by rhetoric|repetition|recency|detail.
Ponytail: understand → YAGNI → reuse → stdlib → native → installed dependency → minimum code; bug fix=root cause + sibling callers.
Docs: why this design beats a plausible alternative; prefix material claims `VERIFIED:`|`HYPOTHESIS:`|`UNKNOWN:`. Pages (`*.md|*.mdx`) are gated: `vae.py prose --fix`, rewrite the rest by meaning against the plugin's `references/PROSE.md`; schematic content → a rendered Mermaid diagram; `README.md` per package with a CLI|API, `ARCH.md` (why + how, operations, security, privacy) per package with production code, both VERIFIED facts only.
Lessons: test|`.agents/VERIFY.py` rule > MEMORY line > EPISODES line; an entry binds only in its evidenced `[scope]`, below the current request; narrow|rewrite|drop disproved ones."""


OPEN_EPISODES = 3


def open_episodes(entries: list[str]) -> list[str]:
    """The newest distinct open episodes, newest first: leads (`LESSON` lines, `FINDING`s learned nowhere else) and
    `FAIL`s that no later `DONE` of the same session resolved.

    WHY filter instead of the last lines: those are mostly `DONE` records and findings a test already enforces, relevant
    by recency only; an open item is what a new session can act on. VERIFIED: in this repo 12 of 96 entries were
    open, 4 of them distinct."""
    rows = [e.split(" ", 2) for e in entries if e.count(" ") >= 2]
    last_done = {sid: i for i, (_, sid, body) in enumerate(rows) if body.startswith("DONE ")}
    out: list[str] = []
    seen: set[str] = set()
    for i in range(len(rows) - 1, -1, -1):
        _, sid, body = rows[i]
        if (is_lead(" ".join(rows[i])) or (body.startswith("FAIL ") and last_done.get(sid, -1) < i)) and body not in seen:
            seen.add(body)
            out.append(" ".join(rows[i]))
            if len(out) == OPEN_EPISODES:
                break
    return out


# Below Claude Code's 10,000-character hook output limit, beyond which it shows a file path and a preview instead.
CONTEXT_MAX = 9000
CUT_RESERVE = 200  # room for the line naming what did not fit


def fit(head: str, sections: list[tuple[str, list[str], str]], cap: int = CONTEXT_MAX) -> str:
    """`head` plus (title, lines, source) sections in priority order, cut only between lines; whatever does not fit is
    named in a last line, so the agent knows to read it.

    WHY: a plain cut of the joined text at 9000 chars silently dropped CLI_GIST and the episode leads and split the last
    MEMORY entry once the rules had grown (VERIFIED: 4,632-char rules + a 4,053-byte MEMORY.md, test_gate)."""
    out, used, missing = [head], len(head), []
    for title, lines, source in sections:
        kept: list[str] = []
        if not missing:
            room = cap - CUT_RESERVE - used - len(title) - 1
            for ln in lines:
                if len(ln) + 1 > room:
                    break
                kept.append(ln)
                room -= len(ln) + 1
        if kept:
            out += [title, *kept]
            used += len(title) + 1 + sum(len(ln) + 1 for ln in kept)
        if len(kept) < len(lines):
            missing.append(f"{source} (+{len(lines) - len(kept)})")
    if missing:
        out.append(f"… context full; read the rest: {', '.join(missing)}")
    return "\n".join(out)[:cap]


def session_context(repo: Path, session_id: str, plugin_root: Path = PLUGIN_ROOT) -> str:
    head = "\n".join((
        "defuss-vae (verified agentic engineering):\n" + RULES_TEXT,
        (f"GATE before finishing code changes: python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {session_id} "
         "(repeat until VERIFIED[gate]=true; git commit is denied until then)."),
    ))
    # WHY inject instead of "go read": memory that is not loaded is not used; the budgets keep this cheap.
    # WHY only open episodes, labeled as leads: retrieval gives an entry relevance, never authority; plan|implement|verify
    # grep EPISODES.md for the rest by touched path|symptom, wrap reads it all.
    sections = []
    # WHY first after the rules: an orchestrator resuming after a crash or an SSH drop must see its live agents.
    root = swarm_root(repo) or repo
    if (root / SWARM_FILE).exists():
        live = summary(root)
        if live:
            sections.append((f"Swarm {SWARM_FILE} (`vae.py swarm status` reconciles; act on non-RUNNING):", live, SWARM_FILE))
    for name, budget in STATE_BUDGET.items():
        entries, size = [], 0
        for e in memory_entries(repo / ".agents" / name):
            size += len(e) + 1
            if size > budget:
                break
            entries.append(e)
        if entries:
            sections.append((f".agents/{name}:", entries, f".agents/{name}"))
    # WHY only the stacks present: a Go service needs Go verbs, not the bun|uv|Playwright lines every session paid for.
    defaults = stack_defaults(stacks(listed_files(repo)))
    if defaults:
        sections.append((f"Stack defaults ({plugin_root}/references/STACKS.md):",
                         [f"{s} {ln[2:]}" for s, lines in sorted(defaults.items()) for ln in lines], "references/STACKS.md"))
    tail = open_episodes(episode_entries(repo))
    if tail:
        # WHY an equal share of 1024 chars per lead: one long LESSON must not erase the other leads chosen beside it.
        share = (1024 - len(tail) + 1) // len(tail)
        sections.append(("Open .agents/EPISODES.md (leads to re-check, not rules):",
                         [e if len(e) <= share else e[:share - 1] + "…" for e in tail], ".agents/EPISODES.md"))
    return fit(head, sections)


def session_start(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    # VERIFIED: resume/compact can fire SessionStart for the same session; preserving the original baseline prevents gate bypass.
    if not state_path(repo, sid).exists():
        init_session(repo, sid)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": session_context(repo, sid, plugin_root)}}
