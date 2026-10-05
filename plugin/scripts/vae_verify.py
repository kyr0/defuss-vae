"""Deterministic verifier: runs the commands and rules a project declares and reports evidence as Checks."""
from __future__ import annotations

import dataclasses
import fnmatch
import importlib.util
import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vae_prose import Finding, fix, scan
from vae_repo import (
    MANIFESTS,
    PLUGIN_ROOT,
    SOURCE_EXT,
    code_fingerprint,
    dirty_paths,
    find_test_files,
    is_arch_source,
    is_code,
    is_doc,
    is_interface,
    is_library,
    make_graph,
    make_reach,
    now_iso,
    run,
    runtime_dir,
    safe_name,
    stacks,
    walk_files,
)

# Markers of toolchains new projects must not start on (bun for JS/TS, uv for Python instead).
FOREIGN_LOCKS = {"package-lock.json": "npm", "npm-shrinkwrap.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
                 "poetry.lock": "poetry", "Pipfile.lock": "pipenv", "pdm.lock": "pdm", "requirements.txt": "pip"}
# The manifest a lockfile belongs to: tracked in HEAD means an existing project on that toolchain, not a new one.
NATIVE_LOCKS = {"package.json": ("bun.lock", "bun.lockb"), "pyproject.toml": ("uv.lock",)}
LOCK_MANIFEST = {"package-lock.json": "package.json", "npm-shrinkwrap.json": "package.json", "yarn.lock": "package.json",
                 "pnpm-lock.yaml": "package.json", "poetry.lock": "pyproject.toml", "pdm.lock": "pyproject.toml", "Pipfile.lock": "Pipfile"}
MAKE_TARGETS = ("setup", "start", "stop", "status", "log", "metrics", "bench", "test", "coverage", "lint", "e2e", "verify")
# What `make verify` (= CI) must run; the gate runs them one by one, so a hollow `verify` would pass locally only.
VERIFY_VERBS = ("lint", "test", "coverage", "e2e")
VERB_CONFIG = {"lint": "lint_command", "test": "test_command", "coverage": "coverage_command", "e2e": "e2e_commands"}
SERVICE_VERBS = ("start", "stop", "status", "log")
# A library keeps the uniform interface with one line instead of disabling `layout`: `make status` then answers
# "no service" definitively, where a missing target leaves the agent guessing.
NO_SERVICE_STUB = '{verbs}: ; @echo "∅ $@: no service"'
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
             "var/x": "var/*", "tmp/x": "tmp/*", "output/x": "output/*", "input/x": "input/*", ".DS_Store": ".DS_Store",
             "dist/x": "dist/", "coverage/x": "coverage/", ".coverage": ".coverage", ".cache/x": ".cache/"}
# Required: secrets, runtime state, consumer output and build artifacts always; caches and package folders per toolchain.
LAYOUT_IGNORES = ("var/x", "tmp/x", ".env")
BUILD_IGNORES = ("output/x", "dist/x")
BASE_IGNORES = (".env", "var/x", "tmp/x", "output/x", "input/x", ".DS_Store", "dist/x")
STACK_IGNORES = {
    "js": ("node_modules/x", "coverage/x", ".cache/x"),
    "python": (".venv/x", "__pycache__/x", "x.pyc", ".pytest_cache/x", ".ruff_cache/x", ".coverage"),
}
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


def run_custom_rule(rule: dict[str, Any], repo: Path, timeout: int, changed: Iterable[str] = (), docs: Iterable[str] = ()) -> Check:
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
        # WHY opt-in `docs`: existing `glob: "*"` code rules (no-mocks, probes) must not fail on pages that cite them.
        glob = str(rule["glob"])
        paths = [p for p in (docs if rule.get("docs") else changed) if fnmatch.fnmatchcase(p, glob)]
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


def stack_ignores(files: Iterable[str], config: dict[str, Any], base: Iterable[str]) -> list[str]:
    """Ignore probes for `base` plus every detected toolchain, minus CONFIG["gitignore_exempt"] lines."""
    exempt = set(config.get("gitignore_exempt") or [])
    probes = list(base) + [p for s in sorted(stacks(files)) for p in STACK_IGNORES[s]]
    return [p for p in probes if GITIGNORE[p] not in exempt]


def strict(config: dict[str, Any]) -> bool:
    """Checks added in 0.5.0 warn until 0.6.0 unless CONFIG["strict"] is True.

    WHY: they fail existing repos on their next gate run; a warning release lets projects catch up (`init`, templates)
    instead of blocking them mid-task."""
    return bool(config.get("strict", False))


def check_gitignore(repo: Path, config: dict[str, Any], files: list[str] | None = None) -> Check:
    """Build output and each present toolchain's caches and package folders are gitignored."""
    ignored = ignored_probes(repo)
    required = stack_ignores(walk_files(repo) if files is None else files, config, BUILD_IGNORES)
    gaps = [GITIGNORE[probe] for probe in required if probe not in ignored]
    nxt = None
    if gaps:
        nxt = (f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo} (appends {', '.join(gaps)} to .gitignore); "
               "a project that must commit one (e.g. a built dist/ for a GitHub Action): CONFIG['gitignore_exempt']")
    return Check("gitignore", "build output, caches and package folders of every toolchain present are gitignored", "VERIFIED",
                 not gaps, "gaps=" + ",".join(gaps) if gaps else "build output, caches and package folders ignored",
                 required=strict(config), next=nxt)


def check_layout(repo: Path) -> Check:
    graph = make_graph(repo)
    verbs = [t for t in MAKE_TARGETS if t not in graph]
    ignored = ignored_probes(repo)
    ignores = [GITIGNORE[probe] for probe in LAYOUT_IGNORES if probe not in ignored]
    gaps = [f"Makefile:{t}" for t in verbs] + [f"gitignore:{line}" for line in ignores]
    steps = [f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo} (appends missing .gitignore lines; never edits an existing Makefile)"]
    if set(SERVICE_VERBS) & set(verbs):
        stub = NO_SERVICE_STUB.format(verbs=" ".join(v for v in ("start", "stop", "restart", "status", "log") if v not in graph))  # only missing: no recipe overrides
        steps.append(f"no service (library): add the Makefile line `{stub}`; a service: copy the service block from the template")
    if set(verbs) - set(SERVICE_VERBS):
        steps.append(f"copy `{' '.join(v for v in verbs if v not in SERVICE_VERBS)}` from {PLUGIN_ROOT}/templates/Makefile")
    steps.append("CONFIG['layout']=False only as a last resort: it also drops the var/ tmp/ .env ignore checks")
    return Check(
        "layout", "Makefile verbs + gitignored var/, tmp/, .env", "VERIFIED", not gaps,
        "gaps=" + ",".join(gaps) if gaps else "Makefile verbs + ignores present", next="; ".join(steps) if gaps else None,
    )


def check_wiring(repo: Path, config: dict[str, Any]) -> Check:
    """Always on, independent of `layout`: CI runs only `make verify`, so it must reach every verb the gate takes from
    the Makefile (CONFIG-overridden verbs are the project's own CI concern)."""
    graph = make_graph(repo)
    from_make = [v for v in VERIFY_VERBS if not config.get(VERB_CONFIG[v]) and v in graph]
    reach = make_reach(graph, "verify")
    unwired = [v for v in from_make if v not in reach]
    evidence = ("no `verify` target" if "verify" not in graph else "verify→" + ",".join(sorted(reach & set(VERIFY_VERBS)))) if from_make else "∅ gate verbs from Makefile"
    return Check(
        "wiring", "make verify runs every Makefile gate verb", "VERIFIED", not unwired, evidence + (f"; missing={','.join(unwired)}" if unwired else ""),
        next=f"EDIT Makefile: `verify: {' '.join(VERIFY_VERBS)}` (prerequisites or $(MAKE) calls; CI runs only make verify)" if unwired else None,
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
        manifest, folder = LOCK_MANIFEST.get(f.name), Path(rel).parent
        native = NATIVE_LOCKS.get(manifest or "", ())
        if manifest and tracked(repo, str(folder / manifest)) and not any(tracked(repo, str(folder / n)) for n in native):
            continue  # an existing project on that toolchain, only its lockfile was never committed; a bun|uv project stays one
        if not tracked(repo, rel):
            new.append(f"{rel}({FOREIGN_LOCKS[f.name]})")
    return Check(
        "toolchain", "new (sub)projects start on bun (JS/TS) or uv (Python)", "VERIFIED", not new,
        f"newly introduced foreign lockfiles={new}" if new else "no npm/yarn/pnpm/poetry/pipenv/pdm/pip lockfile introduced",
        next=None if not new else (
            f"new (sub)project: delete {', '.join(new)} and start it on `bun install` | `uv init` + `uv add` yourself; "
            "keeping the foreign toolchain for a new project is the human's decision (then CONFIG['toolchain']=False)"
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


def prose_findings(repo: Path, pages: Iterable[str], config: dict[str, Any], apply_fix: bool = False) -> list[Finding]:
    """Scan (and with apply_fix first repair) doc pages under CONFIG["prose"]: {"allow": {glob: chars}, "phrases": [regex]}."""
    cfg = config.get("prose")
    cfg = cfg if isinstance(cfg, dict) else {}
    allow_map = cfg.get("allow") or {}
    found: list[Finding] = []
    for rel in pages:
        f = repo / rel
        if not f.is_file():
            continue
        allow = "".join(str(chars) for glob, chars in allow_map.items() if fnmatch.fnmatchcase(rel, glob))
        text = f.read_text("utf-8", errors="replace")
        if apply_fix:
            fixed = fix(text, allow)
            if fixed != text:
                f.write_text(fixed, "utf-8")
                text = fixed
        found += scan(text, rel, repo, allow, cfg.get("phrases") or ())
    return found


def check_prose(repo: Path, pages: list[str], config: dict[str, Any]) -> Check:
    claim = "changed doc pages carry no machine-writing tells, invisible characters or broken Markdown"
    if config.get("prose") is False:
        return Check("prose", claim, "VERIFIED", True, "disabled by CONFIG['prose']=False", required=False)
    try:
        found = prose_findings(repo, pages, config)
    except re.error as e:
        return Check("prose", claim, "UNKNOWN", None, f"invalid CONFIG['prose']['phrases'] regex: {e}", next="FIX .agents/VERIFY.py CONFIG['prose']")
    hits = [str(f) for f in found]
    fixable = sorted({f.path for f in found if f.fixable})
    nxt = None
    if found:
        nxt = (f"RUN vae.py prose --repo . --fix {' '.join(fixable)} (meaning-preserving replacements); " if fixable else "")
        nxt += f"THEN rewrite the rest by meaning ({PLUGIN_ROOT}/references/PROSE.md); a page that needs a flagged character: CONFIG['prose']['allow']"
    return Check("prose", claim, "VERIFIED", not found, f"pages={len(pages)}" + (f"; hits={hits[:20]}" if hits else ""), next=nxt)


def tracked(repo: Path, rel: str) -> bool:
    return run(["git", "cat-file", "-e", f"HEAD:{rel}"], repo, timeout=5, shell=False).returncode == 0


def package_gaps(pkg: dict[str, Any], new: bool, foreign: str | None, cfg: dict[str, Any]) -> list[str]:
    """Missing defaults of one package.json. An existing package (tracked in HEAD) only needs additive metadata.

    WHY: type, build tool and linter are migrations for a legacy package (CJS consumers, eslint config), so they are
    required of new packages and proposed for old ones, the same policy as the toolchain check."""
    gaps = [k for k in ("description", "license", "author") if not pkg.get(k)]
    manager = str(pkg.get("packageManager") or "")
    want = cfg.get("manager", "bun")
    if not manager and want:  # a falsy CONFIG manager drops the requirement
        gaps.append(f"packageManager ({FOREIGN_LOCKS[foreign] if foreign else want}@<version>)")
    elif want and not foreign and not manager.startswith(f"{want}@"):
        gaps.append(f"packageManager={manager!r}, expected {want}@<version>")
    if "TODO(" in json.dumps(pkg):
        gaps.append("template placeholder TODO(...) left")
    if not new:
        return gaps
    if cfg.get("type", "module") and pkg.get("type") != cfg.get("type", "module"):
        gaps.append(f"type={cfg.get('type', 'module')!r}")
    dev = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    scripts = pkg.get("scripts") or {}
    lint = cfg.get("lint", "oxlint")
    if lint and lint not in dev:
        gaps.append(f"devDependency {lint} (bun add -d {lint})")
    if lint == "oxlint" and any("oxlint" in str(s) and "--deny-warnings" not in str(s) for s in scripts.values()):
        gaps.append("oxlint script without --deny-warnings (plain oxlint exits 0 on findings)")
    build = cfg.get("library_build", "pkgroll")
    library = is_library(pkg)
    if build and library and (build not in dev or build not in str(scripts.get("build", ""))):
        gaps.append(f"library build via {build} (devDependency + scripts.build)")
    return gaps


def check_package(repo: Path, changed: Iterable[str], config: dict[str, Any]) -> Check:
    """Every changed package.json carries the JS/TS defaults. CONFIG["package"]: {"manager", "type", "lint",
    "library_build"} (a falsy value drops that requirement) or False."""
    claim = "changed package.json files declare manager, metadata, module type, linter and library build"
    cfg = config.get("package", {})
    if cfg is False:
        return Check("package", claim, "VERIFIED", True, "disabled by CONFIG['package']=False", required=False)
    cfg = cfg if isinstance(cfg, dict) else {}
    gaps: list[str] = []
    for rel in (p for p in changed if Path(p).name == "package.json" and is_code(p) and (repo / p).is_file()):
        try:
            pkg = json.loads((repo / rel).read_text("utf-8"))
        except (OSError, ValueError) as e:
            gaps.append(f"{rel}: unreadable ({e})")
            continue
        folder = (repo / rel).parent
        foreign = next((n for n in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml") if (folder / n).is_file()), None)
        gaps += [f"{rel}: {g}" for g in package_gaps(pkg if isinstance(pkg, dict) else {}, not tracked(repo, rel), foreign, cfg)]
    nxt = None
    if gaps:
        nxt = (f"EDIT package.json (template {PLUGIN_ROOT}/templates/package.json.tmpl; app: drop exports/files/build); "
               "an existing package keeps its module type, build and linter unless the human approves migrating")
    return Check("package", claim, "VERIFIED", not gaps, f"gaps={gaps[:20]}" if gaps else "package.json defaults present",
                 required=strict(config), next=nxt)


def coverage_chain(repo: Path, folder: str) -> list[str]:
    """`folder` and its ancestors up to the nearest one holding a package manifest, else up to the root."""
    chain, d = [], Path(folder)
    while True:
        chain.append(str(d))
        if str(d) == "." or any((repo / d / m).is_file() for m in MANIFESTS):
            return chain
        d = d.parent


def uncovered(repo: Path, folders: Iterable[str], page: str) -> list[str]:
    """Where `page` is missing: a page covers its folder and every folder below it up to the next package manifest.

    WHY: one page per leaf folder (every component directory) is sprawl that goes stale; a package is the unit that
    ships, deploys and is documented on its own, so it is the boundary that needs its own page."""
    out = set()
    for d in folders:
        chain = coverage_chain(repo, d)
        if not any((repo / c / page).is_file() for c in chain):
            out.add(str(Path(chain[-1]) / page))
    return sorted(out)


def page_folders(changed: Iterable[str], keep: Any, cfg: Any) -> list[str]:
    """Folders of changed files that `keep` selects, minus CONFIG globs; empty when the CONFIG key is False."""
    if cfg is False:
        return []
    exclude = (cfg.get("exclude") or []) if isinstance(cfg, dict) else []
    folders = sorted({str(Path(p).parent) for p in changed if keep(p)})
    return [d for d in folders if not any(fnmatch.fnmatchcase(d, g) for g in exclude)]


def check_doc_pages(repo: Path, changed: Iterable[str], config: dict[str, Any]) -> Check:
    """README.md at the root and beside every changed interface (CLI, API); ARCH.md beside changed production code.

    WHY the root always: the layout requires a root Makefile, the project's developer interface. WHY changed folders
    only: like the glob rules, the policy reaches legacy folders as they are touched instead of failing an existing
    repo at once. CONFIG["readme"] and CONFIG["arch"]: {"exclude": [folder globs]} or False."""
    changed = [p for p in changed if (repo / p).is_file()]  # a deleted file or folder needs no page
    readme = {"."} | set(page_folders(changed, lambda p: is_interface(repo, p), config.get("readme", {})))
    missing = uncovered(repo, readme, "README.md") + uncovered(repo, page_folders(changed, is_arch_source, config.get("arch", {})), "ARCH.md")
    tpl = PLUGIN_ROOT / "templates"
    nxt = None
    if missing:
        nxt = (f"WRITE {', '.join(missing)} from {tpl}/README.md.tmpl | {tpl}/ARCH.md.tmpl (only VERIFIED facts): README = how to use "
               "that folder's CLI|API; ARCH.md = why + how of its architecture, NOT a copy of README|docs; "
               "a folder that needs neither: CONFIG['readme'|'arch']['exclude']")
    return Check("docs.pages", "README.md covers the root and every changed CLI|API; ARCH.md covers every changed production folder", "VERIFIED",
                 not missing, f"missing={missing[:20]}" if missing else "README.md + ARCH.md present", required=strict(config), next=nxt)


def verify(repo: Path, changed_paths: list[str] | None = None, suites: bool = True) -> VerifyReport:
    """All checks for the changed paths. `suites=False` (or a change of pages only) runs just the page checks and
    project rules: the gate uses it when code already passed and only pages changed since."""
    repo = repo.resolve()
    if changed_paths is None:
        changed_paths = sorted(dirty_paths(repo))
    code_paths = sorted(p for p in changed_paths if is_code(p))
    doc_paths = sorted(p for p in changed_paths if is_doc(p))
    fp = code_fingerprint(repo, code_paths + doc_paths)
    checks: list[Check] = []
    verifier, config, rules = check_verifier(repo, "verifier.config")
    checks.append(verifier)
    timeout = int(config.get("timeout_s", 180))
    checks.append(check_doc_pages(repo, changed_paths, config))
    if doc_paths:
        checks.append(check_prose(repo, doc_paths, config))
    if not suites or (doc_paths and not code_paths):
        # WHY prose-only: a page edit cannot change what lint, tests or e2e prove; rerunning the suites for a typo
        # would only make the gate slower. Project rules still run, so page-specific invariants hold.
        checks += [run_custom_rule(r, repo, timeout, (), doc_paths) if isinstance(r, dict) else invalid_rule(r) for r in rules]
        return VerifyReport(fp, doc_paths, checks)
    coverage_min = float(config.get("coverage_min", 60.0))
    if config.get("layout", True):
        checks.append(check_layout(repo))
    files = walk_files(repo)  # one walk for the ignore and test checks
    checks.append(check_gitignore(repo, config, files))
    checks.append(check_wiring(repo, config))
    if config.get("toolchain", True):
        checks.append(check_toolchain(repo, code_paths))
    checks.append(check_env(repo, code_paths))
    checks.append(check_package(repo, code_paths, config))
    tests = find_test_files(repo, files)
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
        checks.append(run_custom_rule(rule, repo, timeout, code_paths, doc_paths) if isinstance(rule, dict) else invalid_rule(rule))
    return VerifyReport(fp, code_paths + doc_paths, checks)


def invalid_rule(rule: Any) -> Check:
    return Check("custom.invalid", "custom rule valid", "UNKNOWN", None, f"rule is not dict: {rule!r}", next="FIX .agents/VERIFY.py RULES")


def render_checks(checks: list[Check], evidence_max: int = 1200) -> list[str]:
    lines: list[str] = []
    proven: list[str] = []
    remains: list[str] = []
    warns: list[str] = []
    nexts: list[str] = []
    for c in checks:
        val = "=?" if c.value is None else ("=true" if c.value else "=false")
        ev = c.evidence if len(c.evidence) <= evidence_max else "…" + c.evidence[-evidence_max:]
        lines.append(f"{c.status}[{c.id}]{val} BC {ev}")
        if c.status == "VERIFIED" and c.value is True:
            proven.append(c.id)
        elif c.required:
            remains.append(c.id)
        elif c.value is not True:
            warns.append(c.id)
        if c.next:
            nexts.append(c.next)
    lines.append("PROVEN: " + (", ".join(proven) or "∅"))
    lines.append("REMAINS: " + (", ".join(remains) or "∅"))
    if warns:
        lines.append("WARNS: " + ", ".join(warns) + " (non-blocking until 0.6.0; fix now, or CONFIG['strict']=True to block)")
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
