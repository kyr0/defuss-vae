"""Session state and agent memory files. Both live in the repo (tmp/vae/, .agents/), so the CLI and hooks agree."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vae_repo import (
    PLUGIN_ROOT,
    git_head,
    now_iso,
    read_json,
    runtime_dir,
    safe_name,
    snapshot_dirty,
    write_json,
)

EPISODE_KEEP = 100
# Injected into every session start, so the budget is a per-session token cost, not just disk.
STATE_BUDGET = {"MEMORY.md": 4096, "CLI_GIST.md": 2048}
ENTRY_RE = re.compile(r"\d{4}-\d\d-\d\dT")


def session_dir(repo: Path, session_id: str) -> Path:
    d = runtime_dir(repo, "tmp/vae") / safe_name(session_id)
    d.mkdir(exist_ok=True)
    return d


def state_path(repo: Path, session_id: str) -> Path:
    return session_dir(repo, session_id) / "state.json"


def attestation_path(repo: Path, session_id: str, kind: str) -> Path:
    return session_dir(repo, session_id) / f"{kind}.json"


def latest_session(repo: Path) -> str:
    states = sorted((repo / "tmp" / "vae").glob("*/state.json"), key=lambda p: p.stat().st_mtime)
    return states[-1].parent.name if states else "manual"


def baseline(repo: Path) -> dict[str, Any]:
    return {"head": git_head(repo), "dirty": snapshot_dirty(repo), "created_at": now_iso()}


def init_session(repo: Path, session_id: str) -> dict[str, Any]:
    s = {"schema": 1, "repo": str(repo), "session_id": session_id, "baseline": baseline(repo), "verified_fp": None, "gate_runs": 0}
    write_json(state_path(repo, session_id), s)
    return s


def load_session(repo: Path, session_id: str) -> dict[str, Any]:
    p = state_path(repo, session_id)
    s = read_json(p)
    if s:
        return s
    # Fail-closed fallback: existing dirty files are treated as this session's changes.
    s = {"schema": 1, "repo": str(repo), "session_id": session_id, "baseline": {"head": git_head(repo), "dirty": {}}, "verified_fp": None, "gate_runs": 0, "baseline_status": "HYPOTHESIS"}
    write_json(p, s)
    return s


def ensure_from_template(repo: Path, rel: str, template_root: Path = PLUGIN_ROOT) -> bool:
    dst = repo / rel
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text((template_root / "templates" / Path(rel).name).read_text("utf-8"), "utf-8")
    return True


def bootstrap(repo: Path, template_root: Path = PLUGIN_ROOT) -> list[str]:
    """Gate-owned agent state only; the Makefile and .gitignore stay explicit `init` decisions."""
    return [r for r in (".agents/VERIFY.py", ".agents/EPISODES.md") if ensure_from_template(repo, r, template_root)]


def episode_entries(repo: Path) -> list[str]:
    p = repo / ".agents" / "EPISODES.md"
    lines = p.read_text("utf-8", errors="replace").splitlines() if p.exists() else []
    return [ln for ln in lines if ENTRY_RE.match(ln)]


def append_episodes(repo: Path, session_id: str, entries: list[str], template_root: Path = PLUGIN_ROOT) -> None:
    ensure_from_template(repo, ".agents/EPISODES.md", template_root)
    p = repo / ".agents" / "EPISODES.md"
    lines = p.read_text("utf-8", errors="replace").splitlines()
    first = next((i for i, ln in enumerate(lines) if ENTRY_RE.match(ln)), len(lines))
    head = lines[:first] + ([""] if first == len(lines) and lines and lines[-1].strip() else [])
    stamp = f"{now_iso()} s={safe_name(session_id)[:8]}"
    body = lines[first:] + [f"{stamp} {e}" for e in entries]
    # WHY trim on write: bounded by construction; git history keeps older entries, so pruning costs no agent tokens.
    p.write_text("\n".join(head + body[-EPISODE_KEEP:]) + "\n", "utf-8")


def memory_entries(path: Path) -> list[str]:
    text = path.read_text("utf-8", errors="replace") if path.exists() else ""
    # Comments hold format examples; only real `- ` lines outside them are memory.
    return [ln for ln in re.sub(r"(?s)<!--.*?-->", "", text).splitlines() if ln.startswith("- ")]
