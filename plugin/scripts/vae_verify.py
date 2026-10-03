"""Deterministic verifier: runs the commands and rules a project declares and reports evidence as Checks."""
from __future__ import annotations

import dataclasses
import fnmatch
import importlib.util
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vae_repo import (
    PLUGIN_ROOT,
    SOURCE_EXT,
    code_fingerprint,
    dirty_paths,
    find_test_files,
    is_code,
    make_graph,
    make_reach,
    now_iso,
    run,
    runtime_dir,
    safe_name,
)

# Markers of toolchains new projects must not start on (bun for JS/TS, uv for Python instead).
FOREIGN_LOCKS = {"package-lock.json": "npm", "npm-shrinkwrap.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
                 "poetry.lock": "poetry", "Pipfile.lock": "pipenv", "pdm.lock": "pdm", "requirements.txt": "pip"}
MAKE_TARGETS = ("setup", "start", "stop", "status", "log", "metrics", "bench", "test", "coverage", "lint", "e2e", "verify")
# What `make verify` (= CI) must run; the gate runs them one by one, so a hollow `verify` would pass locally only.
VERIFY_VERBS = ("lint", "test", "coverage", "e2e")
# How `make`, sh, bash and dash report a missing uv/bun (VERIFIED: `make: uv: No such file or directory`).
MISSING_TOOL = re.compile(r"\b(uv|bun)\b:? (?:command not found|No such file or directory|not found)")
# Split literal: this file must not contain the tag itself, or the probe rule would flag the plugin's own source.
PROBE_TAG = "vae" + ":probe"
BUILTIN_RULES = [{
    "id": "hygiene.probes", "kind": "not_regex", "glob": "*", "pattern": re.escape(PROBE_TAG),
    "claim": "no temporary debug probe left in changed code",
}]
# Default .gitignore written by `init` (probe path → line); a line is added only while git doesn't ignore its probe yet.
# `dir/*` (not `dir/`) ignores contents but not the dir, so `!input/<file>` can still commit an e2e fixture.
GITIGNORE = {".env": ".env", ".venv/x": ".venv/", "__pycache__/x": "__pycache__/", ".pytest_cache/x": ".pytest_cache/",
             ".ruff_cache/x": ".ruff_cache/", "node_modules/x": "node_modules/", "x.pyc": "*.pyc",
             "var/x": "var/*", "tmp/x": "tmp/*", "output/x": "output/*", "input/x": "input/*", ".DS_Store": ".DS_Store"}
# The layout check requires only what protects runtime state and secrets; the rest are defaults.
LAYOUT_IGNORES = ("var/x", "tmp/x", ".env")
# Direct env var reads (JS/TS, Python, Go, Rust); escapes keep this source from matching itself. OS-provided names are exempt.
ENV_REF = re.compile(r"""(?:process\.env\.|Bun\.env\.|import\.meta\.env\.|process\.env\[["']|os\.environ\[["']|"""
                     r"""os\.environ\.get\(\s*["']|os\.(?:getenv|Getenv)\(\s*["']|env::var\(\s*")([A-Z][A-Z0-9_]*)""")
ENV_KEY = re.compile(r"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
OS_ENV = {"HOME", "PATH", "PWD", "USER", "SHELL", "TERM", "LANG", "TMPDIR", "TZ", "CI"}


def load_project_verifier(repo: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    path = repo / ".agents" / "VERIFY.py"
    if not path.exists():
        return {}, [], None
    try:
        spec = importlib.util.spec_from_file_location("defuss_vae_project_verify", path)
        if not spec or not spec.loader:
            raise RuntimeError("cannot load module spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        config = getattr(mod, "CONFIG", {})
        rules = getattr(mod, "RULES", [])
        if not isinstance(config, dict) or not isinstance(rules, list):
            raise TypeError("CONFIG must be dict; RULES must be list")
        return config, rules, None
    except Exception as e:  # noqa: BLE001 - VERIFY.py is project code; any error means "does not load"
        return {}, [], f"{type(e).__name__}: {e}"


def check_verifier(repo: Path, check_id: str) -> tuple[Check, dict[str, Any], list[dict[str, Any]]]:
    """WHY missing fails: without VERIFY.py the template rules (e.g. no-mocks) silently never run."""
    exists = (repo / ".agents" / "VERIFY.py").exists()
    config, rules, err = load_project_verifier(repo)
    ok = exists and err is None
    init_cmd = f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo}"
    return Check(check_id, ".agents/VERIFY.py exists and loads", "VERIFIED", ok, err or ("CONFIG/RULES valid" if exists else "missing"),
                 next=None if ok else (f"FIX .agents/VERIFY.py: {err}" if err else init_cmd)), config, rules


@dataclasses.dataclass
class Check:
    id: str
    claim: str
    status: str
    value: bool | None
    evidence: str
    required: bool = True
    command: str | None = None
    next: str | None = None
    metric: float | None = None

    def passes(self) -> bool:
        return (not self.required) or (self.status == "VERIFIED" and self.value is True)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class VerifyReport:
    code_fingerprint: str
    changed_paths: list[str]
    checks: list[Check]
    created_at: str = dataclasses.field(default_factory=now_iso)

    @property
    def verified(self) -> bool:
        return bool(self.checks) and all(c.passes() for c in self.checks)

    @property
    def overall_status(self) -> str:
        required = [c for c in self.checks if c.required]
        return "VERIFIED" if required and all(c.status == "VERIFIED" for c in required) else "UNKNOWN"

    @property
    def overall_value(self) -> bool | None:
        return self.verified if self.overall_status == "VERIFIED" else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "status": self.overall_status,
            "value": self.overall_value,
            "code_fingerprint": self.code_fingerprint,
            "changed_paths": self.changed_paths,
            "checks": [c.as_dict() for c in self.checks],
            "created_at": self.created_at,
        }


def trim_output(s: str, n: int = 4000) -> str:
    s = s.strip()
    return s if len(s) <= n else "…" + s[-n:]


def parse_coverage(output: str) -> tuple[float | None, str]:
    """Total line coverage from the coverage command's output (formats VERIFIED against real runs, see tests)."""
    m = re.search(r"(?mi)^\s*all files\s*\|(.*)$", output)
    nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", m.group(1)) if m else []
    if nums:
        # The last numeric column is % Lines in bun (Funcs|Lines) and istanbul/vitest (Stmts|Branch|Funcs|Lines) tables.
        return float(nums[-1]), "All files table, % Lines"
    m = re.search(r"(?mi)^\s*(?:total|coverage)\b[^\n%]*?([0-9]+(?:\.[0-9]+)?)%", output)
    if m:
        return float(m.group(1)), "TOTAL line"
    return None, "no `TOTAL <n>%` line or `All files |…|` table"


def run_logged(check_id: str, command: str, repo: Path, timeout: int) -> tuple[subprocess.CompletedProcess[str], str]:
    p = run(command, repo, timeout=timeout)
    log = runtime_dir(repo, "var/log/vae") / f"{safe_name(check_id)}.log"
    log.write_text(f"$ {command}\n{p.stdout}", "utf-8")
    return p, str(log.relative_to(repo))


def remedy(command: str, output: str, log: str) -> str:
    m = MISSING_TOOL.search(output)
    if m:
        return f"RUN: make setup  (installs the missing `{m.group(1)}` with its official installer), then rerun the gate"
    return f"RUN: {command}  (full output: tail -n 80 {log})"


def check_command(check_id: str, claim: str, command: str, repo: Path, timeout: int) -> Check:
    p, log = run_logged(check_id, command, repo, timeout)
    if p.returncode == 0:
        # WHY last line only: passing output is noise in the agent's context; the full run stays in the log.
        last = next((ln.strip() for ln in reversed(p.stdout.splitlines()) if ln.strip()), "")
        return Check(check_id, claim, "VERIFIED", True, f"exit=0; {last[:200]}", command=command)
    return Check(
        check_id, claim, "VERIFIED", False, f"exit={p.returncode}; log={log}; {trim_output(p.stdout, 1200)}",
        command=command, next=remedy(command, p.stdout, log),
    )


def run_custom_rule(rule: dict[str, Any], repo: Path, timeout: int, changed: Iterable[str] = ()) -> Check:
    rid = str(rule.get("id") or "custom")
    kind = str(rule.get("kind") or "")
    required = bool(rule.get("required", True))
    claim = str(rule.get("claim") or rid)
    if kind == "command":
        cmd = str(rule.get("command") or "")
        if not cmd:
            return Check(rid, claim, "UNKNOWN", None, "missing command", required, next=f"EDIT .agents/VERIFY.py rule {rid}")
        c = check_command(rid, claim, cmd, repo, int(rule.get("timeout_s", timeout)))
        c.required = required
        return c
    if kind == "file_exists":
        path = repo / str(rule.get("path") or "")
        value = path.exists()
        return Check(rid, claim, "VERIFIED", value, f"path={path}", required, next=None if value else f"CREATE: {path}")
    if kind not in {"contains", "regex", "not_regex"}:
        return Check(rid, claim, "UNKNOWN", None, f"unsupported rule kind={kind!r}", required, next=f"FIX .agents/VERIFY.py rule {rid}")
    rx = None
    if kind != "contains":
        try:
            rx = re.compile(str(rule.get("pattern") or ""), re.MULTILINE)
        except re.error as e:
            return Check(rid, claim, "UNKNOWN", None, f"invalid regex: {e}", required, next=f"FIX regex in .agents/VERIFY.py rule {rid}")
    needle = str(rule.get("text") or "")
    if "glob" in rule:
        # WHY changed files only: policy applies to new work without blocking on untouched legacy code.
        glob = str(rule["glob"])
        paths = [p for p in changed if fnmatch.fnmatchcase(p, glob)]
        scope = f"glob={glob!r} files={len(paths)}"
    else:
        paths = [str(rule.get("path") or "")]
        scope = f"path={paths[0]}"
    hits: list[str] = []
    for rel in paths:
        f = repo / rel
        if not f.is_file():
            if "glob" not in rule:
                hits.append(f"{rel}:missing")
            continue
        text = f.read_text("utf-8", errors="replace")
        if kind == "not_regex":
            hits += [f"{rel}:{text.count(chr(10), 0, m.start()) + 1}" for m in rx.finditer(text)]
        elif (needle not in text) if kind == "contains" else not rx.search(text):
            hits.append(rel)
    value = not hits
    return Check(
        rid, claim, "VERIFIED", value, scope + (f"; hits={hits[:20]}" if hits else ""), required,
        next=None if value else f"FIX {rid}: {', '.join(hits[:20])}",
    )


def ignored_probes(repo: Path) -> set[str]:
    """Layout runtime dirs git ignores; literal .gitignore lines also count so non-git dirs judge the same file."""
    ignored = set(run(["git", "check-ignore", "--no-index", *GITIGNORE], repo, timeout=5, shell=False).stdout.split())
    gi = repo / ".gitignore"
    lines = gi.read_text("utf-8", errors="replace").splitlines() if gi.exists() else []
    return ignored | {probe for probe, line in GITIGNORE.items() if line in lines}


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    # (mtime_ns, size) per file: a rewrite changes mtime even when the content is identical.
    return {p.relative_to(root.parent).as_posix(): (s.st_mtime_ns, s.st_size)
            for p in (root.rglob("*") if root.is_dir() else ()) if p.is_file() for s in (p.stat(),)}


def check_layout(repo: Path) -> Check:
    graph = make_graph(repo)
    gaps = [f"Makefile:{t}" for t in MAKE_TARGETS if t not in graph]
    reach = make_reach(graph, "verify")
    unwired = [v for v in VERIFY_VERBS if "verify" in graph and v not in reach]
    gaps += [f"Makefile:verify→{','.join(unwired)}"] if unwired else []
    ignored = ignored_probes(repo)
    gaps += [f"gitignore:{GITIGNORE[probe]}" for probe in LAYOUT_IGNORES if probe not in ignored]
    return Check(
        "layout", "Makefile verbs + gitignored var/, tmp/, .env", "VERIFIED", not gaps,
        "gaps=" + ",".join(gaps) if gaps else "Makefile verbs + ignores present",
        next=None if not gaps else (
            f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo}  "
            f"(then define missing verbs; `verify: {' '.join(VERIFY_VERBS)}`; template {PLUGIN_ROOT}/templates/Makefile)"
        ),
    )


def check_toolchain(repo: Path, changed: Iterable[str]) -> Check:
    # WHY HEAD membership: a foreign lockfile HEAD already tracks is an existing toolchain (kept; migration is proposed),
    # one HEAD lacks is being introduced now, i.e. a new (sub)project that must start on bun/uv.
    new = []
    for rel in changed:
        f = repo / rel
        if f.name not in FOREIGN_LOCKS or not f.is_file():
            continue
        if f.name == "requirements.txt" and ((repo / "uv.lock").exists() or (f.parent / "uv.lock").exists()):
            continue  # `uv export` output beside a uv lock is fine
        if run(["git", "cat-file", "-e", f"HEAD:{rel}"], repo, timeout=5, shell=False).returncode != 0:
            new.append(f"{rel}({FOREIGN_LOCKS[f.name]})")
    return Check(
        "toolchain", "new (sub)projects start on bun (JS/TS) or uv (Python)", "VERIFIED", not new,
        f"newly introduced foreign lockfiles={new}" if new else "no npm/yarn/pnpm/poetry/pipenv/pdm/pip lockfile introduced",
        next=None if not new else (
            f"IF new (sub)project THEN delete {', '.join(new)} and start on `bun install` | `uv init` + `uv add` "
            "ELSE ask the human to commit the existing lockfile OR set .agents/VERIFY.py CONFIG['toolchain']=False"
        ),
    )


def env_keys(path: Path) -> set[str]:
    return set(ENV_KEY.findall(path.read_text("utf-8", errors="replace"))) if path.is_file() else set()


def nearest(repo: Path, start: Path, name: str) -> Path:
    for d in (start, *start.parents):
        if (d / name).is_file():
            return d / name
        if d == repo:
            break
    return repo / name


def check_env(repo: Path, changed: Iterable[str]) -> Check:
    # WHY: `.env.example` is the only committed record of required config, so a key that code reads or `.env` sets
    # but the example lacks breaks the next checkout. Each file answers to its nearest `.env.example` (monorepos).
    gaps: dict[str, set[str]] = {}
    for rel in changed:
        f = repo / rel
        if f.is_file() and f.suffix.lower() in SOURCE_EXT:
            names = set(ENV_REF.findall(f.read_text("utf-8", errors="replace"))) - OS_ENV
            example = nearest(repo, f.parent, ".env.example")
            if names - env_keys(example):
                gaps.setdefault(str(example.relative_to(repo)), set()).update(names - env_keys(example))
    if env_keys(repo / ".env") - env_keys(repo / ".env.example"):
        gaps.setdefault(".env.example", set()).update(env_keys(repo / ".env") - env_keys(repo / ".env.example"))
    found = [f"{ex}:{','.join(sorted(keys))}" for ex, keys in sorted(gaps.items())]
    if (repo / ".env.example").is_file() and run(["git", "check-ignore", "-q", "--no-index", ".env.example"], repo, timeout=5, shell=False).returncode == 0:
        found.append(".env.example is gitignored")
    return Check(
        "env.example", "`.env.example` declares every env var code reads or `.env` sets", "VERIFIED", not found,
        "missing=" + "; ".join(found) if found else "every read|set env var is declared",
        next=None if not found else "ADD the keys to .env.example (names + safe defaults, never secrets); commit it, keep .env gitignored",
    )


def verify(repo: Path, changed_paths: list[str] | None = None) -> VerifyReport:
    repo = repo.resolve()
    if changed_paths is None:
        changed_paths = sorted(dirty_paths(repo))
    code_paths = sorted(p for p in changed_paths if is_code(p))
    checks: list[Check] = []
    verifier, config, rules = check_verifier(repo, "verifier.config")
    checks.append(verifier)
    coverage_min = float(config.get("coverage_min", 60.0))
    timeout = int(config.get("timeout_s", 180))
    if config.get("layout", True):
        checks.append(check_layout(repo))
    if config.get("toolchain", True):
        checks.append(check_toolchain(repo, code_paths))
    checks.append(check_env(repo, code_paths))
    tests = find_test_files(repo)
    checks.append(Check(
        "tests.exist", "repository has tests", "VERIFIED", bool(tests),
        f"count={len(tests)}" + (f"; sample={tests[:5]}" if tests else ""),
        next=None if tests else "ADD: tests exercising real subsystems for the changed behavior",
    ))
    # WHY no ecosystem autodiscovery: the Makefile is the interface the project declares; guessing runners would
    # verify commands the project never committed to. CONFIG overrides remain the explicit escape hatch.
    verbs = set(make_graph(repo))
    lint_cmd = config.get("lint_command") or ("make lint" if "lint" in verbs else None)
    test_cmd = config.get("test_command") or ("make test" if "test" in verbs else None)
    coverage_cmd = config.get("coverage_command") or ("make coverage" if "coverage" in verbs else None)
    integration = list(config.get("integration_commands") or (["make integration"] if "integration" in verbs else []))
    e2e = list(config.get("e2e_commands") or (["make e2e"] if "e2e" in verbs else []))
    if lint_cmd:  # first: the cheapest deterministic failure
        checks.append(check_command("lint", "lint passes", str(lint_cmd), repo, timeout))
    else:
        checks.append(Check(
            "lint", "lint passes", "UNKNOWN", None, "no Makefile `lint` target or CONFIG['lint_command']",
            next="ADD Makefile target `lint` (uv run ruff check . | bunx oxlint --deny-warnings)",
        ))
    if test_cmd:
        checks.append(check_command("tests.unit", "test command passes", str(test_cmd), repo, timeout))
    else:
        checks.append(Check(
            "tests.unit", "test command passes", "UNKNOWN", None, "no Makefile `test` target or CONFIG['test_command']",
            next="ADD Makefile target `test` (bun test | uv run pytest)",
        ))
    for i, cmd in enumerate(integration):
        checks.append(check_command(f"tests.integration.{i+1}", "integration test command passes", str(cmd), repo, timeout))
    before = tree_state(repo / "output")
    for i, cmd in enumerate(e2e):
        checks.append(check_command(f"tests.e2e.{i+1}", "e2e (dogfood) command passes", str(cmd), repo, timeout))
    if not e2e:
        checks.append(Check(
            "tests.e2e", "e2e command passes", "UNKNOWN", None, "no Makefile `e2e` target or CONFIG['e2e_commands']",
            next="ADD Makefile target `e2e`: build the publishable artifact → clean consumer → input/ → output/",
        ))
    elif all(c.value for c in checks if c.id.startswith("tests.e2e.")):
        # HYPOTHESIS: an e2e that leaves no consumer output most likely never ran the artifact; it cannot prove
        # artifact consumption (review checks that), but it catches `@true` and source-tree-only stand-ins.
        fresh = sorted(p for p, st in tree_state(repo / "output").items() if before.get(p) != st)
        checks.append(Check(
            "tests.e2e.evidence", "e2e wrote fresh evidence to output/", "VERIFIED", bool(fresh),
            f"changed={fresh[:5]}" if fresh else "output/ unchanged by e2e",
            next=None if fresh else "EDIT e2e: the consumer of the built artifact writes its results (web: Playwright report) to output/",
        ))
    claim = f"coverage >= {coverage_min:g}%"
    if coverage_cmd:
        p, log = run_logged("coverage", str(coverage_cmd), repo, timeout)
        pct, evidence = parse_coverage(p.stdout) if p.returncode == 0 else (None, "")
        if p.returncode != 0:
            checks.append(Check(
                "coverage", claim, "VERIFIED", False, f"exit={p.returncode}; log={log}; {trim_output(p.stdout, 1200)}",
                command=str(coverage_cmd), next=remedy(str(coverage_cmd), p.stdout, log),
            ))
        elif pct is None:
            checks.append(Check(
                "coverage", claim, "UNKNOWN", None, f"command passed but metric UNKNOWN; {evidence}; log={log}",
                command=str(coverage_cmd), next="FIX coverage output: print `TOTAL <n>%` or an `All files |…|` table",
            ))
        else:
            checks.append(Check(
                "coverage", claim, "VERIFIED", pct >= coverage_min, f"coverage={pct:.2f}% via {evidence}",
                command=str(coverage_cmd), next=None if pct >= coverage_min else f"ADD tests until coverage >= {coverage_min:g}%", metric=pct,
            ))
    else:
        checks.append(Check(
            "coverage", claim, "UNKNOWN", None, "no Makefile `coverage` target or CONFIG['coverage_command']",
            next="ADD Makefile target `coverage` printing `TOTAL <n>%` (bun test --coverage | uv run pytest --cov)",
        ))
    for rule in BUILTIN_RULES + rules:
        if isinstance(rule, dict):
            checks.append(run_custom_rule(rule, repo, timeout, code_paths))
        else:
            checks.append(Check("custom.invalid", "custom rule valid", "UNKNOWN", None, f"rule is not dict: {rule!r}", next="FIX .agents/VERIFY.py RULES"))
    return VerifyReport(code_fingerprint(repo, code_paths), code_paths, checks)


def render_checks(checks: list[Check], evidence_max: int = 1200) -> list[str]:
    lines: list[str] = []
    proven: list[str] = []
    remains: list[str] = []
    nexts: list[str] = []
    for c in checks:
        val = "=?" if c.value is None else ("=true" if c.value else "=false")
        ev = c.evidence if len(c.evidence) <= evidence_max else "…" + c.evidence[-evidence_max:]
        lines.append(f"{c.status}[{c.id}]{val} BC {ev}")
        if c.status == "VERIFIED" and c.value is True:
            proven.append(c.id)
        elif c.required:
            remains.append(c.id)
        if c.next:
            nexts.append(c.next)
    lines.append("PROVEN: " + (", ".join(proven) or "∅"))
    lines.append("REMAINS: " + (", ".join(remains) or "∅"))
    lines += ["AGENT_CMD: " + n for n in dict.fromkeys(nexts)] or ["AGENT_CMD: ∅"]
    return lines


def render_report(report: VerifyReport) -> str:
    def build(evidence_max: int) -> str:
        lines = render_checks(report.checks, evidence_max)
        if report.overall_status == "VERIFIED":
            lines.append(f"VERIFIED[verifier.overall]={'true' if report.overall_value else 'false'}")
        else:
            lines.append("UNKNOWN[verifier.overall]=?")
        return "\n".join(lines)

    text = build(1200)
    # VERIFIED: Claude Code swaps hook strings >10,000 chars for a file path + 2,000-char preview; stay well below.
    return text if len(text) <= 7000 else build(300)
