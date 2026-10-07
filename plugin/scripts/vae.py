#!/usr/bin/env python3
"""CLI for defuss-vae: deterministic verifier, session gate, prose check, layout scaffold, doctor, swarm registry."""
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
from vae_swarm import SETTLE_S, remove, run_job, spawn, status, stop, swarm_root, upsert
from vae_verify import (
    load_project_verifier,
    prose_findings,
    render_checks,
    render_report,
    verify,
)

SKILLS = ("plan", "implement", "verify", "doc", "doc-edit", "wrap", "status")
SKILL_MAX = 6000  # runtime prompts stay lean: each SKILL.md loads whole on invocation.
# wrap commits, so only the human starts it; every other skill's description says when the agent may.
HUMAN_ONLY = ("wrap",)


def repo_from(raw: str) -> Path:
    p = Path(raw).resolve()
    return git_root(p) or p


def doctor_plugin() -> list[str]:
    required = [ROOT / p for p in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                                   "hooks/hooks.json", "hooks/lifecycle.py", "references/VAE-DIALECT.md", "references/PROSE.md",
                                   "references/CONSOLIDATION.md", "references/STACKS.md")]
    required += [ROOT / "templates" / n for n in ("VERIFY.py", "MEMORY.md", "CLI_GIST.md", "EPISODES.md", "Makefile", "verify.yml",
                                                   "README.md.tmpl", "ARCH.md.tmpl", "package.json.tmpl")]
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
            # WHY one policy for both hosts: Claude Code reads the frontmatter key, Codex only agents/openai.yaml, and a
            # skill the agent may start on one host but not the other behaves differently per host.
            policy = p.parent / "agents" / "openai.yaml"
            claude_human = "disable-model-invocation: true" in p.read_text("utf-8")
            codex_human = policy.exists() and "allow_implicit_invocation: false" in policy.read_text("utf-8")
            if not policy.exists() or claude_human != (n in HUMAN_ONLY) or codex_human != (n in HUMAN_ONLY):
                gaps.append(f"skill-invocation-policy:{n}")
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
    p = sp.add_parser("swarm", help="sub-agent registry .agents/SWARM_STATUS.yaml: spawn|set|rm|stop|status (run: internal)")
    p.add_argument("action", choices=("spawn", "set", "rm", "stop", "status", "run"))
    p.add_argument("--repo", default=".")
    p.add_argument("--name", help="unique agent name")
    p.add_argument("--goal", help="the final goal in one sentence")
    p.add_argument("--workdir", help="relative to the project root, e.g. ../<repo>.wt/<name>")
    p.add_argument("--targets", help="comma-separated paths the agent changes when its work merges back")
    p.add_argument("--eta", type=int, help="minutes until done")
    p.add_argument("--ram", help="estimated RAM, e.g. 2G")
    p.add_argument("--vram", help="estimated VRAM, e.g. 8G")
    p.add_argument("--disk", help="estimated disk space, e.g. 500M")
    p.add_argument("--gpu", help="GPU id when a GPU is used")
    p.add_argument("--container", help="container id when the agent runs in one")
    p.add_argument("--pid", type=int, help="set: the agent process you own (default for new entries: none, required)")
    p.add_argument("--exit-code", type=int, dest="exit_code")
    p.add_argument("--fix", action="store_true", help="status: record drift (LOST → exit_code lost, duplicates dropped)")
    p.add_argument("--settle", type=float, default=SETTLE_S, help="seconds before the re-read that checks a write")

    # WHY split at `--` first: argparse's REMAINDER after a positional swallows the options before it.
    argv = sys.argv[1:]
    cut = argv.index("--") if "--" in argv else len(argv)
    a = ap.parse_args(argv[:cut])
    if a.cmd == "swarm":
        return swarm(a, argv[cut + 1:])
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
        try:
            changed = init_project(repo_from(a.repo), ROOT)
        except ValueError as e:  # an invalid CONFIG["ci"]; everything before the workflow is already scaffolded
            print(f"UNKNOWN[init] BC {e}")
            return 2
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


def swarm(a: argparse.Namespace, cmd: list[str]) -> int:
    start = Path(a.repo).resolve()
    root = swarm_root(start) or start
    if a.action == "status":
        code, lines = status(root, a.fix, a.settle)
        print("\n".join(lines))
        return code
    if not a.name:
        print(f"UNKNOWN[swarm.{a.action}] BC --name is required")
        return 2
    if a.action == "run":
        return run_job(root, a.name, cmd)
    fields = {"goal": a.goal, "workdir": a.workdir, "eta_in_mins": a.eta, "estimated_ram_usage": a.ram,
              "estimated_vram_usage": a.vram, "estimated_disk_space_usage": a.disk, "gpu_id": a.gpu,
              "container_id": a.container, "pid": a.pid, "exit_code": a.exit_code,
              "target_focus_paths": [t.strip() for t in a.targets.split(",") if t.strip()] if a.targets else None}
    fields = {k: v for k, v in fields.items() if v is not None}
    if a.action == "spawn":
        if not cmd:
            print("UNKNOWN[swarm.spawn] BC no command: `vae.py swarm spawn --name … -- <command…>`")
            return 2
        code, why = spawn(root, {"name": a.name, **fields}, cmd, a.settle)
    elif a.action == "set":
        code, why = upsert(root, a.name, fields, a.settle)
    elif a.action == "rm":
        code, why = remove(root, a.name, a.settle)
    else:
        code, why = stop(root, a.name)
    print(f"VERIFIED[swarm.{a.action}]={str(code == 0).lower()} BC {a.name}: {why}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
