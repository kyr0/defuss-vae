#!/usr/bin/env python3
"""CLI for defuss-vae: deterministic verifier, session gate, prose check, page edit runs, review comments, layout scaffold,
doctor, swarm registry, update, research."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vae_annotate
import vae_decide
import vae_edit
from vae_annotate import read_back
from vae_gate import gate
from vae_net import ConfigError
from vae_project import (
    MANAGED_START,
    doctor_repo,
    ensure_managed_agents,
    init_project,
    tidy,
)
from vae_repo import git_root, walk_files
from vae_research import call_capability, describe, run_research
from vae_research import init as research_init
from vae_state import latest_session
from vae_swarm import SETTLE_S, remove, run_job, spawn, status, stop, swarm_root, upsert
from vae_update import check, installed_version, migrate, update_notice
from vae_verify import (
    load_project_verifier,
    prose_findings,
    prose_pages,
    render_checks,
    render_report,
    verify,
)

SKILLS = ("plan", "implement", "verify", "doc", "doc-edit", "wrap", "status", "optimize", "research")
SKILL_MAX = 6000  # runtime prompts stay lean: each SKILL.md loads whole on invocation.
# wrap commits, so only the human starts it; every other skill's description says when the agent may.
HUMAN_ONLY = ("wrap",)


def repo_from(raw: str) -> Path:
    p = Path(raw).resolve()
    return git_root(p) or p


def doctor_plugin() -> list[str]:
    required = [ROOT / p for p in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                                   "hooks/hooks.json", "hooks/lifecycle.py", "references/VAE-DIALECT.md", "references/PROSE.md",
                                   "references/CONSOLIDATION.md", "references/STACKS.md", "references/RESEARCH.md",
                                   "references/EDIT.md", "templates/annotate.html")]
    required += [ROOT / "references" / "edit" / n for n in ("review.json", "summary.json", "audit.json", "review.md", "summary.md",
                                                              "audit.md", "CONTRACT.md")]
    required += [ROOT / "templates" / n for n in ("VERIFY.py", "MEMORY.md", "CLI_GIST.md", "EPISODES.md", "Makefile", "verify.yml",
                                                   "README.md.tmpl", "ARCH.md.tmpl", "package.json.tmpl")]
    required += [ROOT / "templates" / "research" / f"env.{n}{ext}" for n in ("openai", "mcp", "typed-decisions") for ext in ("", ".json")]
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


def edit(a: argparse.Namespace) -> int:
    """The anchored review of one page (vae_edit): start | submit | show | apply | resume | serve | review."""
    repo = repo_from(a.repo)
    config = load_project_verifier(repo)[0]
    cli = f"python3 {shlex.quote(str(ROOT / 'scripts/vae.py'))} edit --repo {shlex.quote(str(repo))}"
    # every path the CLI prints is relative to --repo, so the next command works from any directory
    at = lambda raw: (Path(raw).expanduser() if Path(raw).expanduser().is_absolute() else repo / raw).resolve()
    run = at(a.run) if a.run else None
    try:
        if a.action in ("submit", "show", "apply", "resume") and run is None:
            raise vae_edit.EditError(f"{a.action} needs --run <run folder>")
        if a.action == "serve":
            if a.stop:
                print("STOPPED the review server" if vae_annotate.stop_server(repo) else "∅: no review server was running")
                return 0
            print(f"SERVING {vae_annotate.ensure_server(repo)} ({vae_annotate.REVIEWS_DIR}/; stop: {cli} serve --stop)")
            return 0
        if a.action == "review":
            if len(a.words) != 1 or not a.notes:
                raise vae_edit.EditError("review takes the response and notes: `vae.py edit --repo . review <review folder> --notes <file>`")
            code, lines = vae_edit.next_revision(repo, at(a.words[0]), at(a.notes), a.check)
            print("\n".join(lines))
            return code
        if a.action == "start":
            if len(a.words) != 1:
                raise vae_edit.EditError("start takes one page: `vae.py edit --repo . start <page>`")
            if not a.engine:
                code, text = vae_edit.start(repo, a.words[0], config, cli, prose_findings(repo, a.words, config), a.model)
                print(text)
                return code
            cfg, guidelines = vae_edit.settings(config)
            engine = vae_edit.engine_for(repo, cfg)
            if engine is None:
                raise vae_edit.EditError("--engine needs .vae/.env.openai.json (vae.py research init --repo .)")
            run = vae_edit.create_run(repo, a.words[0], cfg, "engine", engine.description["model"])
            print(f"RUN {run.relative_to(repo)}: engine {engine.description['model']}, parts={len(vae_edit.load_run(run)['parts'])}", flush=True)
            return edit_engine(repo, run, engine, guidelines, cli)
        if a.action == "resume":
            state = vae_edit.harness_state(repo, run)
            _, guidelines = vae_edit.settings(config)
            engine = vae_edit.engine_for(repo, state["settings"])
            if engine is None or state.get("mode") != "engine":
                raise vae_edit.EditError("resume continues an engine run and needs .vae/.env.openai.json")
            return edit_engine(repo, run, engine, guidelines, cli)
        if a.action == "submit":
            if a.part is None or not a.findings:
                raise vae_edit.EditError("submit needs --part N and --findings <file>")
            page = vae_edit.load_run(run)["page"]
            code, text = vae_edit.submit(repo, run, a.part, at(a.findings), a.confidence, at(a.decisions) if a.decisions else None,
                                         cli, prose_findings(repo, [page], config), config)
        elif a.action == "show":
            code, text = 0, vae_edit.show(repo, run, a.part)
        else:
            code, text = vae_edit.apply(repo, run, vae_decide.from_config(repo))
    except (vae_edit.EditError, ConfigError, re.error, OSError) as e:
        print(f"UNKNOWN[edit] BC {e}")
        return 2
    print(text)
    return code


def edit_engine(repo: Path, run: Path, engine: vae_edit.Engine, guidelines: dict, cli: str) -> int:
    state = vae_edit.harness_state(repo, run)
    try:
        page = vae_edit.run_engine(repo, state, engine, guidelines)
    except Exception as e:  # noqa: BLE001 - failure.json holds it; the run resumes from its last step
        print(f"UNKNOWN[edit.engine] BC {type(e).__name__}: {e}"[:600])
        print(f"NEXT: {cli} resume --run {state['rel']}")
        return 2
    total = vae_edit.findings(vae_edit.harness_state(repo, run))
    open_ = sum(1 for x in total if x["finding"]["umformulierung"] is None)
    print(f"REVIEW {state['rel']}: parts={len(state['parts'])}, findings={len(total)}, replacements={len(total) - open_}, open={open_}")
    print("\n".join(vae_annotate.link(repo, page)))
    print(f"VERIFIED[edit.review] BC {state['rel']}/final.json")
    print(f"NEXT: {cli} apply --run {state['rel']}")
    return 0


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
    p = sp.add_parser("prose", help="static prose check of doc pages (default: every Markdown page and CONFIG['prose']['html'] page)")
    p.add_argument("--repo", default=".")
    p.add_argument("--fix", action="store_true", help="apply meaning-preserving replacements first")
    p.add_argument("pages", nargs="*")
    p = sp.add_parser("edit", help="anchored page review: start <page> | submit | show | apply | resume | serve | review (references/EDIT.md)")
    p.add_argument("action", choices=("start", "submit", "show", "apply", "resume", "serve", "review"))
    p.add_argument("words", nargs="*", help="start: the page, relative to the repo; review: the review folder (or response file)")
    p.add_argument("--repo", default=".")
    p.add_argument("--run", help="the run folder (tmp/vae/edit/<page>-<stamp>-<hex>)")
    p.add_argument("--part", type=int, help="submit|show: the part")
    p.add_argument("--findings", help="submit: the JSON array of findings for the part")
    p.add_argument("--confidence", choices=("high", "medium", "low"), help="submit: the agent's confidence in the part")
    p.add_argument("--decisions", help="submit after a second opinion: one decision per unpaired finding")
    p.add_argument("--engine", action="store_true", help="start: the configured model reviews the whole page (summary, audit)")
    p.add_argument("--model", help="start: the reviewing agent's model, recorded in final.json")
    p.add_argument("--stop", action="store_true", help="serve: stop the repo's review server")
    p.add_argument("--notes", help="review: the agent's notes on the revision, a JSON array of {on, quote, body}")
    p.add_argument("--check", action="store_true", help="review: check the revision against the notes, write nothing")
    p = sp.add_parser("comments", help="read back the human's response: a review folder (its response.json) or a downloaded file")
    p.add_argument("file")
    p.add_argument("--repo", default=".")
    p = sp.add_parser("init", help="scaffold layout + .vae state; never overwrites")
    p.add_argument("--repo", default=".")
    p = sp.add_parser("doctor", help="validate plugin files, or project agent state with --repo")
    p.add_argument("--repo", default=None)
    p.add_argument("--tidy", action="store_true", help="with --repo: list deletable defuss-vae leftovers in tmp/ and logs to bundle")
    p.add_argument("--apply", action="store_true", help="with --tidy: delete the listed leftovers and bundle week-old log archives")
    p = sp.add_parser("update", help="version | check (released version, cached per user) | migrate (.agents/ state → .vae/)")
    p.add_argument("action", choices=("version", "check", "migrate"))
    p.add_argument("--repo", default=".")
    p.add_argument("--quiet", action="store_true", help="check: print nothing (the session-start hook runs it detached)")
    p = sp.add_parser("research", help="deep research on the configured LLM + capabilities: run <query> | init | tools | call <capability> key=value...")
    p.add_argument("action", choices=("run", "init", "tools", "call"))
    p.add_argument("words", nargs="*", help="run: the query; call: the capability, then its inputs as key=value")
    p.add_argument("--repo", default=".")
    p.add_argument("--model", default="reasoner", help="run: the model role in .vae/.env.openai.json (default reasoner)")
    p.add_argument("--max-rounds", type=int, default=2, dest="max_rounds", help="run: tool rounds per chain (default 2)")
    p.add_argument("--max-tool-calls", type=int, default=24, dest="max_tool_calls", help="run: tool calls per run (default 24)")
    p.add_argument("--no-experiments", action="store_true", dest="no_experiments", help="run: no python3 experiments")
    p = sp.add_parser("swarm", help="sub-agent registry .vae/SWARM_STATUS.yaml: spawn|set|rm|stop|status (run: internal)")
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
    # WHY parse_known_args: on Python 3.9 argparse rejects positionals after options in a subcommand
    # (`research run --repo . what is x`), so the leftovers join the research words; any other command rejects them.
    a, extra = ap.parse_known_args(argv[:cut])
    if a.cmd in ("research", "edit") and not any(x.startswith("-") for x in extra):
        a.words += extra
    elif extra:
        ap.error("unrecognized arguments: " + " ".join(extra))
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
        pages = a.pages or prose_pages(walk_files(repo), config)
        try:
            found = prose_findings(repo, pages, config, apply_fix=a.fix)
        except re.error as e:
            print(f"UNKNOWN[prose] BC invalid CONFIG['prose']['phrases'] regex: {e}")
            return 2
        print("\n".join(str(f) for f in found))
        print(f"VERIFIED[prose]={str(not found).lower()} BC pages={len(pages)} findings={len(found)}")
        return 2 if found else 0
    if a.cmd == "update":
        return update(a)
    if a.cmd == "research":
        return research(a)
    if a.cmd == "edit":
        return edit(a)
    if a.cmd == "comments":
        code, lines = read_back(repo_from(a.repo), Path(a.file).expanduser().resolve())
        print("\n".join(lines))
        return code
    if a.cmd == "init":
        try:
            changed = init_project(repo_from(a.repo), ROOT)
        except ValueError as e:  # an invalid CONFIG["ci"]; everything before the workflow is already scaffolded
            print(f"UNKNOWN[init] BC {e}")
            return 2
        print("VERIFIED[init]=true")
        print("CHANGED: " + (", ".join(changed) or "∅"))
        return 0
    if a.repo and a.tidy:
        checks = tidy(repo_from(a.repo), a.apply)
        print("\n".join(render_checks(checks)))
        return 0 if all(c.passes() for c in checks) else 2
    if a.repo:
        checks = doctor_repo(repo_from(a.repo))
        print("\n".join(render_checks(checks)))
        return 0 if all(c.passes() for c in checks) else 2
    gaps = doctor_plugin()
    print("VERIFIED[plugin.files]=" + ("false" if gaps else "true"))
    print("REMAINS: " + (", ".join(gaps) or "∅"))
    return 2 if gaps else 0


def update(a: argparse.Namespace) -> int:
    installed = installed_version(ROOT)
    if a.action == "version":
        print(installed)
        return 0
    if a.action == "check":
        latest = check()
        if not a.quiet:
            print(update_notice(installed) or f"VERIFIED[update.check]=true BC installed={installed} released={latest or 'UNKNOWN (offline?)'}")
        return 0
    repo = repo_from(a.repo)
    code, lines = migrate(repo)
    agents = repo / "AGENTS.md"
    # WHY refresh only an existing block: the managed rules name the state folder; a project without one keeps none.
    if code == 0 and any(ln.startswith("moved ") for ln in lines) and agents.exists() and MANAGED_START in agents.read_text("utf-8"):
        ensure_managed_agents(repo)
        lines.append("refreshed the defuss-vae block in AGENTS.md")
    print("\n".join(lines))
    print(f"VERIFIED[update.migrate]={str(code == 0).lower()}")
    return code


def research(a: argparse.Namespace) -> int:
    repo = repo_from(a.repo)
    if a.action == "run":
        if not a.words:
            print("UNKNOWN[research] BC no query: `vae.py research run --repo . \"<question>\"`")
            return 2
        return run_research(repo, " ".join(a.words), a.model, a.max_rounds, a.max_tool_calls, not a.no_experiments)[0]
    if a.action == "init":
        print("CHANGED: " + (", ".join(research_init(repo, ROOT)) or "∅"))
        print("VERIFIED[research.init]=true BC never overwrites; fill the secret files, then `vae.py doctor --repo .`")
        return 0
    code, lines = describe(repo) if a.action == "tools" else \
        call_capability(repo, a.words[0], a.words[1:]) if a.words else (2, ["UNKNOWN[research.call] BC name a capability"])
    print("\n".join(lines))
    return code


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
