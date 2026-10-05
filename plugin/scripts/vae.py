#!/usr/bin/env python3
"""CLI for defuss-vae: deterministic verifier, session gate, prose check, layout scaffold, doctor."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vae_gate import gate
from vae_project import doctor_repo, init_project
from vae_repo import git_root, is_doc, walk_files
from vae_state import latest_session
from vae_verify import (
    load_project_verifier,
    prose_findings,
    render_checks,
    render_report,
    verify,
)

SKILLS = ("plan", "implement", "review", "docs", "finalize")
SKILL_MAX = 5500  # runtime prompts stay lean: each SKILL.md loads whole on invocation.


def repo_from(raw: str) -> Path:
    p = Path(raw).resolve()
    return git_root(p) or p


def doctor_plugin() -> list[str]:
    required = [ROOT / p for p in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                                   "hooks/hooks.json", "hooks/lifecycle.py", "references/SIGNAN.md", "references/PROSE.md")]
    required += [ROOT / "templates" / n for n in ("VERIFY.py", "MEMORY.md", "CLI_GIST.md", "EPISODES.md", "Makefile", "verify.yml")]
    required += [ROOT / "skills" / n / "SKILL.md" for n in SKILLS]
    gaps = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
    for p in required:
        if p.suffix == ".json" and p.exists():
            try:
                json.loads(p.read_text("utf-8"))
            except (OSError, ValueError) as e:
                gaps.append(f"invalid-json:{p.relative_to(ROOT)}:{e}")
    for n in SKILLS:
        p = ROOT / "skills" / n / "SKILL.md"
        if p.exists():
            if "disable-model-invocation: true" not in p.read_text("utf-8"):
                gaps.append(f"skill-not-human-only:{n}")
            if p.stat().st_size >= SKILL_MAX:
                gaps.append(f"skill-over-budget:{n}:{p.stat().st_size}B")
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(prog="defuss-vae")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("verify", help="run the deterministic verifier on current changes")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--changed", action="append", default=None)
    p = sp.add_parser("gate", help="session gate verify → review → docs; exit 0 when done")
    p.add_argument("--repo", default=".")
    p.add_argument("--session", nargs="?", default=None, help="session id (default: latest under tmp/vae/)")
    p = sp.add_parser("prose", help="static prose check of doc pages (default: every Markdown page in the repo)")
    p.add_argument("--repo", default=".")
    p.add_argument("--fix", action="store_true", help="apply meaning-preserving replacements first")
    p.add_argument("pages", nargs="*")
    p = sp.add_parser("init", help="scaffold layout + .agents state; never overwrites")
    p.add_argument("--repo", default=".")
    p = sp.add_parser("doctor", help="validate plugin files, or project agent state with --repo")
    p.add_argument("--repo", default=None)

    a = ap.parse_args()
    if a.cmd == "verify":
        report = verify(repo_from(a.repo), a.changed)
        print(json.dumps(report.as_dict(), indent=2) if a.json else render_report(report))
        return 0 if report.verified else 2
    if a.cmd == "gate":
        repo = repo_from(a.repo)
        g = gate(repo, a.session or latest_session(repo), ROOT)
        print(g.text)
        return 0 if g.done else 2
    if a.cmd == "prose":
        repo = repo_from(a.repo)
        config = load_project_verifier(repo)[0]
        # WHY every page by default: the CLI doubles as the cleanup tool for legacy pages.
        # VERIFIED: the gate itself scans only changed pages (vae_verify.verify), so legacy pages never block new work.
        pages = a.pages or [p for p in walk_files(repo) if is_doc(p)]
        try:
            found = prose_findings(repo, pages, config, apply_fix=a.fix)
        except re.error as e:
            print(f"UNKNOWN[prose] BC invalid CONFIG['prose']['phrases'] regex: {e}")
            return 2
        print("\n".join(str(f) for f in found))
        print(f"VERIFIED[prose]={str(not found).lower()} BC pages={len(pages)} findings={len(found)}")
        return 2 if found else 0
    if a.cmd == "init":
        changed = init_project(repo_from(a.repo), ROOT)
        print("VERIFIED[init]=true")
        print("CHANGED: " + (", ".join(changed) or "∅"))
        return 0
    if a.repo:
        checks = doctor_repo(repo_from(a.repo))
        print("\n".join(render_checks(checks)))
        return 0 if all(c.passes() for c in checks) else 2
    gaps = doctor_plugin()
    print("VERIFIED[plugin.files]=" + ("false" if gaps else "true"))
    print("REMAINS: " + (", ".join(gaps) or "∅"))
    return 2 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
