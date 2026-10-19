"""The plugin's own version, the `.agents/` → `.vae/` state move, and the cached check for a newer release.

WHY one module: all three answer "which defuss-vae is this and what must change to bring it current", and they sit
right above `vae_repo`, so the hooks (session start) and the CLI (`vae.py update`) ask the same code.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from vae_repo import (
    LEGACY_STATE_DIR,
    PLUGIN_ROOT,
    STATE_DIR,
    STATE_FILES,
    now_iso,
    read_json,
    run,
    write_json,
)

# What `claude plugin update` installs: the marketplace serves the repository's default branch, and there are no
# release tags. VERIFIED: (2026-10-11) the raw manifest answered 200 in 0.36 s with the version on main.
RELEASE_URL = "https://raw.githubusercontent.com/kyr0/defuss-vae/main/plugin/.claude-plugin/plugin.json"
CHECK_EVERY_S = 24 * 3600
CHECK_TIMEOUT_S = 2.0


def installed_version(plugin_root: Path = PLUGIN_ROOT) -> str:
    """The version this copy ships, read from its own manifest beside the scripts: nothing to search for."""
    return str(json.loads((plugin_root / "plugin.json").read_text("utf-8"))["version"])


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v)[:3])


# -- state folder move ---------------------------------------------------------------------------------------------


def state_names(folder: Path) -> list[str]:
    return [n for n in STATE_FILES if (folder / n).exists()]


def migrate_notice(repo: Path, plugin_root: Path = PLUGIN_ROOT) -> str | None:
    """One line for the session context when state still sits in `.agents/`, or None."""
    legacy, current = state_names(repo / LEGACY_STATE_DIR), state_names(repo / STATE_DIR)
    if not legacy:
        return None
    if current:
        return (f"defuss-vae state is in both {LEGACY_STATE_DIR}/ ({', '.join(legacy)}) and {STATE_DIR}/ ({', '.join(current)}): "
                "ask the human which to keep before any gate run; do not merge them yourself.")
    return (f"defuss-vae now keeps its state in {STATE_DIR}/; move it from {LEGACY_STATE_DIR}/ before other work: "
            f"python3 {plugin_root}/scripts/vae.py update migrate --repo {repo}")


def rewrite_ignores(repo: Path) -> list[str]:
    """The required swarm ignore follows the file; a whole-folder ignore gains a `.vae/` twin and stays, since other
    tools may still keep files in `.agents/`."""
    path = repo / ".gitignore"
    if not path.exists():
        return []
    lines, out, done = path.read_text("utf-8").splitlines(), [], []
    for ln in lines:
        bare = ln.strip().lstrip("/")
        if bare == f"{LEGACY_STATE_DIR}/SWARM_STATUS.yaml":
            out.append(ln.replace(LEGACY_STATE_DIR, STATE_DIR))
            done.append(f".gitignore: {ln.strip()} → {out[-1].strip()}")
            continue
        out.append(ln)
        if bare in (LEGACY_STATE_DIR, f"{LEGACY_STATE_DIR}/") and f"{STATE_DIR}/" not in (x.strip().lstrip("/") for x in lines):
            out.append(f"{STATE_DIR}/")
            done.append(f".gitignore: + {STATE_DIR}/")
    if done:
        path.write_text("\n".join(out) + "\n", "utf-8")
    return done


def migrate(repo: Path) -> tuple[int, list[str]]:
    """Move defuss-vae's own files from `.agents/` to `.vae/`; everything else in `.agents/` stays.

    WHY only the named files and not the folder: `.agents/` is a shared name, so another tool's files may live there.
    WHY `git mv` for tracked files: the move is staged as a rename and history follows. Exit 2 changes nothing when
    `.vae/` already holds state: which copy wins is the human's call."""
    old, new = repo / LEGACY_STATE_DIR, repo / STATE_DIR
    moving = state_names(old)
    if not moving:
        return 0, [f"∅ no defuss-vae state in {LEGACY_STATE_DIR}/"]
    present = state_names(new)
    if present:
        return 2, [(f"STOP: {LEGACY_STATE_DIR}/ ({', '.join(moving)}) and {STATE_DIR}/ ({', '.join(present)}) both hold "
                    "defuss-vae state; nothing moved. Ask the human which copy to keep.")]
    new.mkdir(exist_ok=True)
    rels = [f"{LEGACY_STATE_DIR}/{n}" for n in moving]
    tracked = set(run(["git", "ls-files", "--", *rels], repo, timeout=10, shell=False).stdout.split())
    done = []
    for name, rel in zip(moving, rels):
        if rel in tracked:
            p = run(["git", "mv", rel, f"{STATE_DIR}/{name}"], repo, timeout=10, shell=False)
            if p.returncode != 0:
                return 1, [*done, f"FAIL git mv {rel}: {p.stdout.strip()}"]
        else:
            os.replace(old / name, new / name)
        done.append(f"moved {rel} → {STATE_DIR}/{name}" + (" (git mv)" if rel in tracked else ""))
    # Python's bytecode cache of VERIFY.py is regenerated on the next import from its new place.
    cache = old / "__pycache__"
    for pyc in cache.glob("VERIFY.*.pyc") if cache.is_dir() else ():
        pyc.unlink()
    for d in (cache, old):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            done.append(f"removed empty {d.relative_to(repo)}/")
    done += rewrite_ignores(repo)
    if old.is_dir():
        done.append(f"kept {LEGACY_STATE_DIR}/ (other files: {', '.join(sorted(p.name for p in old.iterdir()))})")
    return 0, done


# -- release check -------------------------------------------------------------------------------------------------


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "defuss-vae" / "update.json"


def check_enabled() -> bool:
    return os.environ.get("VAE_UPDATE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def check(url: str | None = None, path: Path | None = None, timeout: float = CHECK_TIMEOUT_S) -> str | None:
    """Fetch the released version and record it with the time; on any failure keep the last known version but record
    the attempt, so an offline machine tries once a day, not once per session."""
    url = url or os.environ.get("VAE_UPDATE_URL") or RELEASE_URL
    path = path or cache_path()
    old = read_json(path) or {}
    latest, error = old.get("latest"), None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            v = json.loads(resp.read().decode("utf-8")).get("version")
        if not isinstance(v, str) or not version_key(v):
            raise ValueError(f"no version in {url}")
        latest = v
    except Exception as e:  # noqa: BLE001 - a failed check must never disturb a session
        error = f"{type(e).__name__}: {e}"[:200]
        if isinstance(e, urllib.error.HTTPError):
            e.close()  # an HTTP error carries the open response
    record = {"checked": now_iso(), "checked_at": time.time(), "latest": latest}
    if error:
        record["error"] = error
    try:
        write_json(path, record)
    except OSError:
        pass
    return latest if isinstance(latest, str) else None


def update_notice(installed: str, path: Path | None = None) -> str | None:
    """One line when the cached release is newer than this copy, else None."""
    latest = (read_json(path or cache_path()) or {}).get("latest")
    if not isinstance(latest, str) or version_key(latest) <= version_key(installed):
        return None
    return (f"defuss-vae {latest} is released (this session runs {installed}): tell the user. Update in Claude Code: "
            "`claude plugin update defuss-vae@defuss-vae`, then /reload-plugins; other hosts: the README's update table.")


def check_due(path: Path | None = None, now: float | None = None) -> bool:
    at = (read_json(path or cache_path()) or {}).get("checked_at")
    return not isinstance(at, (int, float)) or (now or time.time()) - at >= CHECK_EVERY_S


def start_check(plugin_root: Path = PLUGIN_ROOT) -> None:
    """Run `vae.py update check` detached and return at once: the session start never waits on the network."""
    try:
        subprocess.Popen(
            [sys.executable, str(plugin_root / "scripts" / "vae.py"), "update", "check", "--quiet"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except OSError:
        pass
