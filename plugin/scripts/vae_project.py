"""Project scaffolding and hygiene: `init` (layout, CI, managed AGENTS.md block) and `doctor --repo`."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from vae_hooks import RULES_TEXT
from vae_repo import PLUGIN_ROOT, listed_files, run, stacks, walk_files
from vae_state import (
    EPISODE_LEADS,
    MEMORY_LINE,
    STATE_BUDGET,
    ensure_from_template,
    episode_entries,
    is_lead,
    memory_entries,
)
from vae_verify import (
    BASE_IGNORES,
    GITIGNORE,
    VERB_CONFIG,
    VERIFY_VERBS,
    Check,
    check_gitignore,
    check_layout,
    check_verifier,
    check_wiring,
    ignored_probes,
    load_project_verifier,
    stack_defaults,
    stack_ignores,
)

MANAGED_START = "<!-- defuss-vae:start -->"
MANAGED_END = "<!-- defuss-vae:end -->"


def managed_block(repo: Path) -> str:
    """The session rules for hosts without hooks, plus the defaults of the stacks this repo uses (as at session start)."""
    read = "Read `.agents/MEMORY.md` + `.agents/CLI_GIST.md` before engineering work; `grep` `.agents/EPISODES.md` for recurring failures."
    defaults = stack_defaults(stacks(listed_files(repo)))
    lines = [f"{s} {ln[2:]}" for s, group in sorted(defaults.items()) for ln in group]
    extra = ("\nStack defaults (plugin `references/STACKS.md`):\n" + "\n".join(lines)) if lines else ""
    return f"{MANAGED_START}\n## defuss-vae\n{read}\n{RULES_TEXT}{extra}\n{MANAGED_END}\n"


def ensure_managed_agents(repo: Path) -> None:
    block = managed_block(repo)
    p = repo / "AGENTS.md"
    old = p.read_text("utf-8") if p.exists() else ""
    if MANAGED_START in old and MANAGED_END in old:
        a = old.index(MANAGED_START)
        b = old.index(MANAGED_END, a) + len(MANAGED_END)
        new = old[:a].rstrip() + "\n\n" + block.rstrip() + old[b:]
    else:
        new = old.rstrip() + ("\n\n" if old.strip() else "") + block
    p.write_text(new.lstrip("\n").rstrip() + "\n", "utf-8")


CI_DEFAULT = ["make setup", "make verify"]
# The template's last comment line; one `run` step per CONFIG["ci"] command replaces everything after it.
CI_MARK = 'CONFIG["ci"] commands'
# make options whose value is the next word, so `make -C web verify` yields the target `verify`, not `web`.
MAKE_ARG_OPTS = {"-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile"}


def ci_commands(config: dict[str, Any]) -> list[str] | None:
    """CONFIG["ci"]: None → `make setup` + `make verify`; False → no CI; a non-empty list → those commands."""
    ci = config.get("ci")
    if ci is None:
        return list(CI_DEFAULT)
    if ci is False:
        return None
    if isinstance(ci, list) and ci and all(isinstance(c, str) and c.strip() for c in ci):
        return ci
    raise ValueError(f"CONFIG['ci'] must be None (make setup, make verify), False (no CI) or a list of commands, not {ci!r}")


def shell_text(workflow: str) -> str:
    """Workflow text with comment lines dropped, `\\` continuations joined and blanks collapsed."""
    lines = [ln for ln in workflow.replace("\\\n", " ").splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(" ".join(ln.split()) for ln in lines)


def make_targets(text: str) -> set[str]:
    """Targets of every `make` invocation in `text`, also inside YAML flow style (`{run: make verify}`); options and
    VAR=value words are skipped."""
    targets: set[str] = set()
    for m in re.finditer(r"(?:^|[\s;&|(`\"'{\[])make((?: [^\s;&|)#`,\]}]+)*)", text, re.MULTILINE):
        words = [w.strip("\"'") for w in m.group(1).split()]
        skip = False
        for i, w in enumerate(words):
            if skip:
                skip = False
            elif w in MAKE_ARG_OPTS or (w in ("-j", "-l") and i + 1 < len(words) and words[i + 1].isdigit()):
                skip = True
            elif w and not w.startswith("-") and "=" not in w:
                targets.add(w)
    return targets


def runs_verify(workflow: str, config: dict[str, Any]) -> bool:
    """A workflow runs the project's verification when it runs `make verify`, the CONFIG["ci"] commands, or every
    verify verb (lint test coverage e2e) as `make <verb>` or as the command CONFIG declares for it.

    WHY not the literal `make verify`: `make -j4 verify`, `make lint test coverage e2e` or a project's own
    `uv run pytest` step verify just as much, and a second, duplicate workflow would double every CI run."""
    text = shell_text(workflow)
    targets = make_targets(text)
    has = lambda cmds: bool(cmds) and all(" ".join(c.split()) in text for c in cmds)
    if "verify" in targets or (isinstance(config.get("ci"), list) and has(config["ci"])):
        return True
    for verb in VERIFY_VERBS:
        own = config.get(VERB_CONFIG[verb])
        cmds = [own] if isinstance(own, str) else own if isinstance(own, list) else []
        if verb not in targets and not has([c for c in cmds if isinstance(c, str)]):
            return False
    return True


def render_ci(commands: list[str], template_root: Path = PLUGIN_ROOT) -> str:
    """The workflow template with one `run` step per command; plain YAML where safe, else a JSON (= YAML) string."""
    tpl = (template_root / "templates" / "verify.yml").read_text("utf-8")
    head = tpl[:tpl.index("\n", tpl.index(CI_MARK)) + 1]
    plain = re.compile(r"[A-Za-z0-9_./][\w ./=+@-]*")
    return head + "".join(f"      - run: {c if plain.fullmatch(c) else json.dumps(c)}\n" for c in commands)


def github_ci(repo: Path, config: dict[str, Any], template_root: Path = PLUGIN_ROOT) -> bool:
    """CI only where it can run (a GitHub remote), only if CONFIG["ci"] is not False, and only if no workflow
    already runs the project's verification."""
    commands = ci_commands(config)
    if commands is None or "github.com" not in run(["git", "remote", "-v"], repo, timeout=5, shell=False).stdout:
        return False
    dst = repo / ".github/workflows/verify.yml"
    workflows = (repo / ".github/workflows").glob("*.y*ml")
    if dst.exists() or any(runs_verify(p.read_text("utf-8", errors="replace"), config) for p in workflows):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_ci(commands, template_root), "utf-8")
    return True


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
    if github_ci(repo, config, template_root):
        changed.append(".github/workflows/verify.yml")
    before = (repo / "AGENTS.md").read_text("utf-8") if (repo / "AGENTS.md").exists() else None
    ensure_managed_agents(repo)
    if (repo / "AGENTS.md").read_text("utf-8") != before:
        changed.append("AGENTS.md")
    return changed


# A repo-relative path with a directory part, optionally followed by `:line|symbol`.
PATH_REF = re.compile(r"(?<![\w./<-])((?:[\w.-]+/)+[\w.-]+)(?=[:,;\s)`'\"]|$)")


def memory_sources(repo: Path) -> list[tuple[str, str]]:
    """(file, entry) for every agent memory entry, and every AGENTS.md bullet outside the managed block."""
    out = [(f".agents/{n}", e) for n in STATE_BUDGET for e in memory_entries(repo / ".agents" / n)]
    out += [(".agents/EPISODES.md", e) for e in episode_entries(repo)]
    agents = repo / "AGENTS.md"
    if agents.exists():
        text = agents.read_text("utf-8", errors="replace")
        if MANAGED_START in text and MANAGED_END in text:
            text = text[:text.index(MANAGED_START)] + text[text.index(MANAGED_END) + len(MANAGED_END):]
        out += [("AGENTS.md", ln) for ln in text.splitlines() if ln.lstrip().startswith("- ")]
    return out


def check_stale(repo: Path) -> Check:
    """Entries citing repo paths that no longer exist: candidates for the wrap audit, never a verdict.

    WHY conservative: only paths whose first segment exists at the repo root count, so prose like "agent/human" and
    paths relative to another folder are not flagged; a missing path proves the entry needs a look, not that it is wrong."""
    roots = {p.name for p in repo.iterdir()}
    stale = []
    for src, entry in memory_sources(repo):
        for ref in PATH_REF.findall(entry):
            if "*" in ref or ref.split("/", 1)[0] not in roots or (repo / ref).exists():
                continue
            stale.append(f"{src}: {ref} ({entry[:70]})")
    stale = list(dict.fromkeys(stale))
    nxt = None
    if stale:
        nxt = (f"AUDIT in wrap per {PLUGIN_ROOT}/references/CONSOLIDATION.md: rewrite to the new location, delete only "
               "with evidence, keep and retag UNKNOWN when unsure")
    return Check("state.stale", "agent memory cites only paths that exist", "VERIFIED", not stale,
                 f"stale={stale[:10]}" + (f" (+{len(stale) - 10})" if len(stale) > 10 else "") if stale else "every cited path exists",
                 required=False, next=nxt)


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
        entries = memory_entries(p)
        bad = {"untagged": [ln[:60] for ln in entries if not re.match(r"- (?:VERIFIED|HYPOTHESIS|UNKNOWN)\b", ln)]}
        want = f"≤{budget} B and every entry tagged VERIFIED|HYPOTHESIS|UNKNOWN"
        if name == "MEMORY.md":
            # WHY: a promoted lesson without its reason cannot be re-checked against the repo, and a long one taxes
            # every session; the CLI gist holds commands, whose evidence is the observed run.
            bad["no BC"] = [ln[:60] for ln in entries if not re.search(r"\bBC\b", ln)]
            bad[f"over {MEMORY_LINE} chars"] = [ln[:60] for ln in entries if len(ln) > MEMORY_LINE]
            want += f", with `BC` evidence, ≤{MEMORY_LINE} chars"
        ok = size <= budget and not any(bad.values())
        checks.append(Check(
            f"state.{name}", want, "VERIFIED", ok,
            f"bytes={size}" + "".join(f"; {k}={v[:5]}" for k, v in bad.items() if v),
            next=None if ok else f"CONSOLIDATE .agents/{name}: merge, delete stale|derivable lines, tag every entry"
            + (", one concise line each with its BC evidence" if name == "MEMORY.md" else ""),
        ))
    leads = [e for e in episode_entries(repo) if is_lead(e)]
    checks.append(Check(
        "state.EPISODES.md", f"≤{EPISODE_LEADS} open leads (LESSON, FINDING learn=none)", "VERIFIED",
        len(leads) <= EPISODE_LEADS, f"leads={len(leads)}",
        next=None if len(leads) <= EPISODE_LEADS else
        f"SETTLE in wrap per {PLUGIN_ROOT}/references/CONSOLIDATION.md: promote each lead or delete it with evidence",
    ))
    checks += [check_stale(repo), check_layout(repo), check_gitignore(repo, config), check_wiring(repo, config)]
    return checks
