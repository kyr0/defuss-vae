"""Session state and agent memory files. Both live in the repo (tmp/vae/, .agents/), so the CLI and hooks agree."""
from __future__ import annotations

import contextlib
import re
import time
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
# Open leads past this many mean wrap has not settled them; doctor fails so it does.
EPISODE_LEADS = 30
# Injected into every session start, so the budget is a per-session token cost, not just disk.
STATE_BUDGET = {"MEMORY.md": 4096, "CLI_GIST.md": 2048}
MEMORY_LINE = 240  # one concise lesson: about 16 fit the MEMORY budget
ENTRY_RE = re.compile(r"\d{4}-\d\d-\d\dT")
SESSIONS = "sessions.tsv"  # one `<folder>\t<session id>` line per session; the first line for an id wins
FOLDER_RE = re.compile(r"\d{4}-\d\d-\d\d_\d\d_\d\d_\d\d_\d+")


def session_dir(repo: Path, session_id: str) -> Path:
    """`tmp/vae/<yyyy-mm-dd>_<hh_mm_ss>_<n>/`: UTC start time, n=1 or the next free number in that second.

    WHY not the session id as the name: harness ids are UUIDs, and a folder of them reads like leaked keys to people and
    to path scanners (the maintainer's call); a start time sorts and reads at a glance. `sessions.tsv` maps each id to
    its folder. VERIFIED: test_session_folders_are_named_by_start_time_and_keep_their_session covers the naming, the
    next number in an occupied second, the mapping across processes and a damaged index line."""
    root = runtime_dir(repo, "tmp/vae")
    key = safe_name(session_id)
    folder = session_folder(root, key)
    if folder is None:
        stamp, n = time.strftime("%Y-%m-%d_%H_%M_%S", time.gmtime()), 1
        while True:
            try:
                (root / f"{stamp}_{n}").mkdir()  # atomic: two sessions in one second get different n
                break
            except FileExistsError:
                n += 1
        mine = f"{stamp}_{n}"
        with open(root / SESSIONS, "a", encoding="utf-8") as fh:
            fh.write(f"{mine}\t{key}\n")
        # Another process of the same session may have registered first; its folder is the session's.
        folder = session_folder(root, key) or mine
        if folder != mine:
            with contextlib.suppress(OSError):
                (root / mine).rmdir()
    d = root / folder
    d.mkdir(exist_ok=True)
    return d


def session_folder(root: Path, key: str) -> str | None:
    """The folder `sessions.tsv` records for a session key; lines that name no dated folder are ignored, so a damaged
    index cannot point a write outside tmp/vae/."""
    with contextlib.suppress(OSError):
        for line in (root / SESSIONS).read_text("utf-8").splitlines():
            folder, _, sid = line.partition("\t")
            if sid == key and FOLDER_RE.fullmatch(folder):
                return folder
    return None


def state_path(repo: Path, session_id: str) -> Path:
    return session_dir(repo, session_id) / "state.json"


def attestation_path(repo: Path, session_id: str, kind: str) -> Path:
    return session_dir(repo, session_id) / f"{kind}.json"


def latest_session(repo: Path) -> str:
    """The id of the session whose state changed last: `vae.py gate` without `--session` continues that session."""
    states = sorted((repo / "tmp" / "vae").glob("*/state.json"), key=lambda p: p.stat().st_mtime)
    for p in reversed(states):
        sid = (read_json(p) or {}).get("session_id")
        if sid:
            return str(sid)
    return "manual"


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


def is_lead(entry: str) -> bool:
    """For a full `<UTC ISO> s=<session> <KIND> ...` entry: a lead is what only wrap may settle: a `LESSON`, a `FINDING` learned nowhere else (`learn=none`), or any line
    of unknown kind. Gate noise is `DONE`, `FAIL` and a `FINDING` already encoded in a test, rule or MEMORY line."""
    body = entry.split(" ", 2)[2] if entry.count(" ") >= 2 else entry
    kind = body.split(" ", 1)[0]
    learn = re.search(r"\blearn=(\w+)", body)
    if kind == "FINDING":
        return not learn or learn.group(1).lower() == "none"
    return kind not in ("DONE", "FAIL")


def append_episodes(repo: Path, session_id: str, entries: list[str], template_root: Path = PLUGIN_ROOT) -> None:
    ensure_from_template(repo, ".agents/EPISODES.md", template_root)
    p = repo / ".agents" / "EPISODES.md"
    lines = p.read_text("utf-8", errors="replace").splitlines()
    first = next((i for i, ln in enumerate(lines) if ENTRY_RE.match(ln)), len(lines))
    head = lines[:first] + ([""] if first == len(lines) and lines and lines[-1].strip() else [])
    stamp = f"{now_iso()} s={safe_name(session_id)[:8]}"
    body = lines[first:] + [f"{stamp} {e}" for e in entries]
    # WHY trim only gate noise, oldest first: a plain window dropped unsettled lessons by age alone; git history keeps
    # the noise. Leads stay until wrap promotes or deletes them, and doctor caps them (EPISODE_LEADS).
    # VERIFIED: (test_gate) 100 later FAILs drop the oldest DONE and learned FINDING but keep every lead.
    drop = sum(1 for ln in body if ENTRY_RE.match(ln)) - EPISODE_KEEP
    if drop > 0:
        cut = {i for i, ln in enumerate(body) if ENTRY_RE.match(ln) and not is_lead(ln)}
        cut = set(sorted(cut)[:drop])
        body = [ln for i, ln in enumerate(body) if i not in cut]
    p.write_text("\n".join(head + body) + "\n", "utf-8")


def memory_entries(path: Path) -> list[str]:
    text = path.read_text("utf-8", errors="replace") if path.exists() else ""
    # Comments hold format examples; only real `- ` lines outside them are memory.
    return [ln for ln in re.sub(r"(?s)<!--.*?-->", "", text).splitlines() if ln.startswith("- ")]
