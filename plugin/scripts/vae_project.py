"""Project scaffolding and hygiene: `init` (layout, CI, managed AGENTS.md block) and `doctor --repo`."""
from __future__ import annotations

import re
from pathlib import Path

from vae_hooks import RULES_TEXT
from vae_repo import PLUGIN_ROOT, run, walk_files
from vae_state import STATE_BUDGET, ensure_from_template, memory_entries
from vae_verify import (
    BASE_IGNORES,
    GITIGNORE,
    Check,
    check_gitignore,
    check_layout,
    check_verifier,
    check_wiring,
    ignored_probes,
    load_project_verifier,
    stack_ignores,
)

MANAGED_START = "<!-- defuss-vae:start -->"
MANAGED_END = "<!-- defuss-vae:end -->"


def managed_block() -> str:
    read = "Read `.agents/MEMORY.md` + `.agents/CLI_GIST.md` before engineering work; `grep` `.agents/EPISODES.md` for recurring failures."
    return f"{MANAGED_START}\n## defuss-vae\n{read}\n{RULES_TEXT}\n{MANAGED_END}\n"


def ensure_managed_agents(repo: Path) -> None:
    block = managed_block()
    p = repo / "AGENTS.md"
    old = p.read_text("utf-8") if p.exists() else ""
    if MANAGED_START in old and MANAGED_END in old:
        a = old.index(MANAGED_START)
        b = old.index(MANAGED_END, a) + len(MANAGED_END)
        new = old[:a].rstrip() + "\n\n" + block.rstrip() + old[b:]
    else:
        new = old.rstrip() + ("\n\n" if old.strip() else "") + block
    p.write_text(new.lstrip("\n").rstrip() + "\n", "utf-8")


def github_ci(repo: Path, template_root: Path = PLUGIN_ROOT) -> bool:
    """CI only where it can run (a GitHub remote) and only if no workflow already runs `make verify`."""
    if "github.com" not in run(["git", "remote", "-v"], repo, timeout=5, shell=False).stdout:
        return False
    if any("make verify" in p.read_text("utf-8", errors="replace") for p in (repo / ".github/workflows").glob("*.y*ml")):
        return False
    return ensure_from_template(repo, ".github/workflows/verify.yml", template_root)


def init_project(repo: Path, template_root: Path = PLUGIN_ROOT) -> list[str]:
    """Scaffold the layout; never overwrites anything the project already owns."""
    rels = (".agents/VERIFY.py", ".agents/EPISODES.md", ".agents/MEMORY.md", ".agents/CLI_GIST.md", "Makefile")
    changed = [r for r in rels if ensure_from_template(repo, r, template_root)]
    ignored = ignored_probes(repo)
    gi = repo / ".gitignore"
    text = gi.read_text("utf-8") if gi.exists() else ""
    config = load_project_verifier(repo)[0]
    # WHY detected stacks only. VERIFIED: (test_project) a Python repo gets no node_modules/ line, and an exempt dist/
    # (a committed build) is never appended.
    add = [GITIGNORE[probe] for probe in stack_ignores(walk_files(repo), config, BASE_IGNORES) if probe not in ignored]
    if add:
        gi.write_text(text + ("\n" if text and not text.endswith("\n") else "") + "\n".join(add) + "\n", "utf-8")
        changed.append(".gitignore")
    if github_ci(repo, template_root):
        changed.append(".github/workflows/verify.yml")
    before = (repo / "AGENTS.md").read_text("utf-8") if (repo / "AGENTS.md").exists() else None
    ensure_managed_agents(repo)
    if (repo / "AGENTS.md").read_text("utf-8") != before:
        changed.append("AGENTS.md")
    return changed


def doctor_repo(repo: Path) -> list[Check]:
    """Deterministic memory hygiene: loadable policy, bounded + epistemically tagged state, layout."""
    init_cmd = f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo}"
    verifier, config, _ = check_verifier(repo, "state.verifier")
    checks = [verifier]
    for name, budget in STATE_BUDGET.items():
        p = repo / ".agents" / name
        if not p.exists():
            checks.append(Check(f"state.{name}", "exists", "VERIFIED", False, "missing", next=init_cmd))
            continue
        size = len(p.read_bytes())
        untagged = [ln[:60] for ln in memory_entries(p) if not re.match(r"- (?:VERIFIED|HYPOTHESIS|UNKNOWN)\b", ln)]
        ok = size <= budget and not untagged
        checks.append(Check(
            f"state.{name}", f"≤{budget} B and every entry tagged VERIFIED|HYPOTHESIS|UNKNOWN", "VERIFIED", ok,
            f"bytes={size}" + (f"; untagged={untagged[:5]}" if untagged else ""),
            next=None if ok else f"CONSOLIDATE .agents/{name}: merge, delete stale|derivable lines, tag every entry",
        ))
    checks += [check_layout(repo), check_gitignore(repo, config), check_wiring(repo, config)]
    return checks
