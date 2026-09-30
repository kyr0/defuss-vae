#!/usr/bin/env python3
"""VERIFIED: defuss-vae core uses stdlib-only Python so lifecycle gates need no project dependencies."""
from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EPI = {"VERIFIED", "UNKNOWN", "HYPOTHESIS"}
SOURCE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
    ".go", ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash", ".zsh", ".fish",
    ".vue", ".svelte", ".astro",
}
ENGINEERING_EXT = SOURCE_EXT | {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sql",
    ".graphql", ".gql", ".proto", ".tf", ".tfvars", ".css", ".scss", ".less", ".html",
}
TEST_EXT = SOURCE_EXT
CODE_CONFIG = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "Makefile", "CMakeLists.txt",
    "tsconfig.json", "vite.config.js", "vite.config.ts", "vitest.config.js", "vitest.config.ts",
    "jest.config.js", "jest.config.ts", "eslint.config.js", "eslint.config.mjs",
}
IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "target", "dist", "build",
    ".next", ".nuxt", ".turbo", "coverage", ".coverage", "vendor", "__pycache__",
}
# Layout roots holding runtime state, logs and build outputs: never code, never test sources.
RUNTIME_TOP = {"tmp", "var", "output"}
MAKE_TARGETS = ("start", "stop", "status", "log", "metrics", "bench", "test", "e2e")
REVIEW_CHECKLIST = [
    "requirements", "correctness", "callers", "errors", "state-concurrency", "security", "tests",
    "e2e", "observability", "reuse", "yagni", "smells-gotchas", "abstraction", "performance", "docs",
]
DOC_LEVELS = ("file", "method", "inline")
MANAGED_START = "<!-- defuss-vae:start -->"
MANAGED_END = "<!-- defuss-vae:end -->"
# Split literal: this file must not contain the tag itself, or the probe rule would flag the plugin's own source.
PROBE_TAG = "vae" + ":probe"
BUILTIN_RULES = [{
    "id": "hygiene.probes", "kind": "not_regex", "glob": "*", "pattern": re.escape(PROBE_TAG),
    "claim": "no temporary debug probe left in changed code",
}]
EPISODE_KEEP = 100
# Injected into every session start, so the budget is a per-session token cost, not just disk.
STATE_BUDGET = {"MEMORY.md": 4096, "CLI_GIST.md": 2048}
ENTRY_RE = re.compile(r"\d{4}-\d\d-\d\dT")
GITIGNORE = {"var/log/x": "/var/log/", "tmp/x": "/tmp/"}
COMMIT_RE = re.compile(r"(?<![\w-])git(?:\s+(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?))*\s+commit\b")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run(
    command: str | list[str],
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    shell: bool | None = None,
) -> subprocess.CompletedProcess[str]:
    if shell is None:
        shell = isinstance(command, str)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=merged,
            executable="/bin/sh" if shell and os.name != "nt" else None,
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        return subprocess.CompletedProcess(command, 124, out + f"\nTIMEOUT>{timeout}s")


def git_root(cwd: Path) -> Path | None:
    p = run(["git", "rev-parse", "--show-toplevel"], cwd, timeout=5, shell=False)
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 and p.stdout.strip() else None


def git_head(repo: Path) -> str | None:
    p = run(["git", "rev-parse", "HEAD"], repo, timeout=5, shell=False)
    return p.stdout.strip() if p.returncode == 0 else None


def _nul_paths(args: list[str], repo: Path) -> set[str]:
    p = subprocess.run(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
    if p.returncode != 0:
        return set()
    return {x.decode("utf-8", "surrogateescape") for x in p.stdout.split(b"\0") if x}


def dirty_paths(repo: Path) -> set[str]:
    paths = set()
    paths |= _nul_paths(["git", "diff", "--name-only", "-z"], repo)
    paths |= _nul_paths(["git", "diff", "--cached", "--name-only", "-z"], repo)
    paths |= _nul_paths(["git", "ls-files", "--others", "--exclude-standard", "-z"], repo)
    return paths


def committed_paths(repo: Path, start_head: str | None) -> set[str]:
    head = git_head(repo)
    if not start_head or not head or start_head == head:
        return set()
    return _nul_paths(["git", "diff", "--name-only", "-z", start_head, head], repo)


def file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "<deleted>"
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_dirty(repo: Path) -> dict[str, str]:
    return {p: file_hash(repo / p) for p in sorted(dirty_paths(repo))}


def changed_since(repo: Path, baseline: dict[str, Any]) -> list[str]:
    before: dict[str, str] = baseline.get("dirty", {})
    current = snapshot_dirty(repo)
    changed = {p for p, h in current.items() if before.get(p) != h}
    changed |= committed_paths(repo, baseline.get("head"))
    return sorted(changed)


def is_code(path: str) -> bool:
    p = Path(path)
    if not p.parts or p.parts[0] in RUNTIME_TOP or path.startswith(".agents/"):
        return False
    if any(part in IGNORE_DIRS for part in p.parts):
        return False
    return p.suffix.lower() in ENGINEERING_EXT or p.name in CODE_CONFIG or p.name in {"Dockerfile", "Containerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}


def is_test(path: str) -> bool:
    s = path.replace("\\", "/").lower()
    n = Path(s).name
    return (
        "/test/" in f"/{s}/" or "/tests/" in f"/{s}/" or "__tests__" in s
        or n.startswith("test_") or n.endswith("_test.py") or n.endswith("_test.go")
        or ".test." in n or ".spec." in n or n.endswith("test.java") or n.endswith("tests.java")
    )


def is_production_source(path: str) -> bool:
    p = Path(path)
    return is_code(path) and p.suffix.lower() in SOURCE_EXT and not is_test(path)


def code_fingerprint(repo: Path, paths: Iterable[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted({p for p in paths if is_code(p)}):
        h.update(rel.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(file_hash(repo / rel).encode())
        h.update(b"\0")
    return h.hexdigest()


def walk_files(repo: Path, limit: int = 50000) -> list[str]:
    out: list[str] = []
    for base, dirs, files in os.walk(repo):
        b = Path(base)
        top = b == repo
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".cache") and not (top and d in RUNTIME_TOP)]
        for f in files:
            out.append(str((b / f).relative_to(repo)).replace("\\", "/"))
            if len(out) >= limit:
                return out
    return out


def find_test_files(repo: Path) -> list[str]:
    return [p for p in walk_files(repo) if is_test(p) and Path(p).suffix.lower() in TEST_EXT]


def package_json(repo: Path) -> dict[str, Any]:
    p = repo / "package.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def package_manager(repo: Path) -> str:
    if (repo / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo / "yarn.lock").exists():
        return "yarn"
    if (repo / "bun.lockb").exists() or (repo / "bun.lock").exists():
        return "bun"
    return "npm"


def pm_script(pm: str, script: str) -> str:
    if pm == "yarn":
        return f"yarn {shlex.quote(script)}"
    return f"{pm} run {shlex.quote(script)}"


def pm_exec(pm: str, executable: str, args: str) -> str:
    if pm == "pnpm":
        return f"pnpm exec {executable} {args}"
    if pm == "yarn":
        return f"yarn {executable} {args}"
    if pm == "bun":
        return f"bun x {executable} {args}"
    return f"npx --no-install {executable} {args}"


def make_targets(repo: Path) -> set[str]:
    try:
        text = (repo / "Makefile").read_text("utf-8", errors="replace")
    except OSError:
        return set()
    # Rule lines only (`a b: deps`, `a::`); `:=` and `::=` assignments are excluded by the lookahead.
    return {n for m in re.finditer(r"(?m)^([A-Za-z0-9_.%/ -]+?)\s*:(?!:?=)", text) for n in m.group(1).split()}


def py_has_module(repo: Path, module: str) -> bool:
    q = run([sys.executable, "-c", f"import {module}"], repo, timeout=8, shell=False)
    return q.returncode == 0


def cargo_has_llvm_cov(repo: Path) -> bool:
    return run(["cargo", "llvm-cov", "--version"], repo, timeout=8, shell=False).returncode == 0


@dataclasses.dataclass
class Discovery:
    test_command: str | None = None
    coverage_command: str | None = None
    integration_commands: list[str] = dataclasses.field(default_factory=list)
    e2e_commands: list[str] = dataclasses.field(default_factory=list)
    basis: list[str] = dataclasses.field(default_factory=list)


def discover_commands(repo: Path, tmp: Path) -> Discovery:
    d = Discovery()
    # WHY Makefile first: the layout makes `make <verb>` the one interface humans, agents and CI share;
    # ecosystem autodiscovery only fills verbs the Makefile lacks.
    targets = make_targets(repo)
    if "test" in targets:
        d.test_command = "make test"
        d.basis.append("Makefile:test")
    if "coverage" in targets:
        d.coverage_command = "make coverage"
        d.basis.append("Makefile:coverage")
    if "integration" in targets:
        d.integration_commands.append("make integration")
    if "e2e" in targets:
        d.e2e_commands.append("make e2e")
    pkg = package_json(repo)
    scripts = pkg.get("scripts", {}) if isinstance(pkg.get("scripts"), dict) else {}
    if scripts:
        pm = package_manager(repo)
        test_script = str(scripts.get("test", ""))
        if d.test_command is None and test_script and "no test specified" not in test_script.lower():
            d.test_command = pm_script(pm, "test")
            d.basis.append(f"package.json scripts.test via {pm}")
        if not d.integration_commands:
            d.integration_commands += [pm_script(pm, k) for k in ("test:integration", "integration") if k in scripts][:1]
        if not d.e2e_commands:
            d.e2e_commands += [pm_script(pm, k) for k in ("test:e2e", "e2e") if k in scripts][:1]
        if d.coverage_command is None and "coverage" in scripts:
            d.coverage_command = pm_script(pm, "coverage")
            d.basis.append("package.json scripts.coverage")
        elif d.coverage_command is None:
            deps: dict[str, Any] = {}
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(pkg.get(key), dict):
                    deps.update(pkg[key])
            if "jest" in deps:
                d.coverage_command = pm_exec(pm, "jest", f"--coverage --coverageReporters=json-summary --coverageDirectory={shlex.quote(str(tmp))}")
                d.basis.append("jest dependency")
            elif "vitest" in deps and ("@vitest/coverage-v8" in deps or "@vitest/coverage-istanbul" in deps):
                d.coverage_command = pm_exec(pm, "vitest", f"run --coverage --coverage.reporter=json-summary --coverage.reportsDirectory={shlex.quote(str(tmp))}")
                d.basis.append("vitest + coverage provider dependencies")

    py_project = any((repo / n).exists() for n in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"))
    py_tests = any(p.endswith(".py") for p in find_test_files(repo))
    if d.test_command is None and py_tests:
        if py_has_module(repo, "pytest") or py_project:
            d.test_command = f"{shlex.quote(sys.executable)} -m pytest"
            d.basis.append("Python tests + pytest/project metadata")
        else:
            d.test_command = f"{shlex.quote(sys.executable)} -m unittest discover"
            d.basis.append("Python tests → unittest discovery")
    if d.coverage_command is None and py_tests and py_has_module(repo, "pytest") and py_has_module(repo, "pytest_cov"):
        out = tmp / "coverage.json"
        d.coverage_command = f"{shlex.quote(sys.executable)} -m pytest --cov=. --cov-report=json:{shlex.quote(str(out))}"
        d.basis.append("pytest + pytest-cov installed")

    if d.test_command is None and (repo / "go.mod").exists():
        d.test_command = "go test ./..."
        d.basis.append("go.mod")
    if d.coverage_command is None and (repo / "go.mod").exists():
        out = tmp / "coverage.out"
        d.coverage_command = f"go test ./... -coverprofile={shlex.quote(str(out))} && go tool cover -func={shlex.quote(str(out))}"
        d.basis.append("Go standard coverage")

    if d.test_command is None and (repo / "Cargo.toml").exists():
        d.test_command = "cargo test"
        d.basis.append("Cargo.toml")
    if d.coverage_command is None and (repo / "Cargo.toml").exists() and cargo_has_llvm_cov(repo):
        out = tmp / "llvm-cov.json"
        d.coverage_command = f"cargo llvm-cov --json --output-path {shlex.quote(str(out))}"
        d.basis.append("cargo-llvm-cov installed")

    if d.test_command is None and (repo / "pom.xml").exists():
        d.test_command = "./mvnw test" if (repo / "mvnw").exists() else "mvn test"
        d.basis.append("pom.xml")
    if d.test_command is None and ((repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists()):
        d.test_command = "./gradlew test" if (repo / "gradlew").exists() else "gradle test"
        d.basis.append("Gradle build")
    return d


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
    except Exception as e:
        return {}, [], f"{type(e).__name__}: {e}"


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
    discovery_basis: list[str]
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
            "discovery_basis": self.discovery_basis,
            "created_at": self.created_at,
        }


def trim_output(s: str, n: int = 4000) -> str:
    s = s.strip()
    return s if len(s) <= n else "…" + s[-n:]


def parse_coverage(repo: Path, tmp: Path, output: str) -> tuple[float | None, str]:
    candidates = [tmp / "coverage.json", tmp / "coverage-summary.json", repo / "coverage" / "coverage-summary.json"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text("utf-8"))
            if "totals" in obj and isinstance(obj["totals"], dict):
                v = obj["totals"].get("percent_covered")
                if isinstance(v, (int, float)):
                    return float(v), f"{p}:totals.percent_covered"
            total = obj.get("total", {}) if isinstance(obj, dict) else {}
            lines = total.get("lines", {}) if isinstance(total, dict) else {}
            v = lines.get("pct") if isinstance(lines, dict) else None
            if isinstance(v, (int, float)):
                return float(v), f"{p}:total.lines.pct"
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, list) and data:
                totals = data[0].get("totals", {})
                lines = totals.get("lines", {}) if isinstance(totals, dict) else {}
                v = lines.get("percent") if isinstance(lines, dict) else None
                if isinstance(v, (int, float)):
                    return float(v), f"{p}:data[0].totals.lines.percent"
        except Exception:
            pass
    m = re.search(r"(?mi)^total:\s+\(statements\)\s+([0-9]+(?:\.[0-9]+)?)%", output)
    if m:
        return float(m.group(1)), "go tool cover total"
    m = re.search(r"(?mi)^\s*all files\s*\|(?:\s*[0-9.]+\s*\|){3}\s*([0-9.]+)", output)
    if m:
        return float(m.group(1)), "coverage table All files/lines"
    m = re.search(r"(?mi)^\s*(?:total|coverage)\b[^\n%]*?([0-9]+(?:\.[0-9]+)?)%", output)
    if m:
        return float(m.group(1)), "coverage stdout total"
    return None, "no unambiguous supported coverage metric found"


def expand_command(cmd: str, repo: Path, tmp: Path) -> str:
    return cmd.format(
        repo=shlex.quote(str(repo)),
        coverage_json=shlex.quote(str(tmp / "coverage.json")),
        coverage_file=shlex.quote(str(tmp / "coverage.out")),
        tmp=shlex.quote(str(tmp)),
    )


def safe_name(raw: str) -> str:
    # lstrip(".") blocks `..` traversal out of tmp/vae/ via a crafted session id.
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).lstrip(".")[:80] or "_"


def runtime_dir(repo: Path, rel: str) -> Path:
    """Layout runtime dir that ignores itself: gate state and logs never reach `git status`,
    even in repos that have not gitignored var/log/ and tmp/ yet."""
    d = repo / rel
    d.mkdir(parents=True, exist_ok=True)
    marker = d / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", "utf-8")
    return d


def run_logged(check_id: str, command: str, repo: Path, timeout: int) -> tuple[subprocess.CompletedProcess[str], str]:
    p = run(command, repo, timeout=timeout)
    log = runtime_dir(repo, "var/log/vae") / f"{safe_name(check_id)}.log"
    log.write_text(f"$ {command}\n{p.stdout}", "utf-8")
    return p, str(log.relative_to(repo))


def check_command(check_id: str, claim: str, command: str, repo: Path, timeout: int) -> Check:
    p, log = run_logged(check_id, command, repo, timeout)
    if p.returncode == 0:
        # WHY last line only: passing output is noise in the agent's context; the full run stays in the log.
        last = next((ln.strip() for ln in reversed(p.stdout.splitlines()) if ln.strip()), "")
        return Check(check_id, claim, "VERIFIED", True, f"exit=0; {last[:200]}", command=command)
    return Check(
        check_id, claim, "VERIFIED", False, f"exit={p.returncode}; log={log}; {trim_output(p.stdout, 1200)}",
        command=command, next=f"RUN: {command}  (full output: tail -n 80 {log})",
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


def check_layout(repo: Path) -> Check:
    gaps = [f"Makefile:{t}" for t in MAKE_TARGETS if t not in make_targets(repo)]
    ignored = ignored_probes(repo)
    gaps += [f"gitignore:{probe[:-1]}" for probe in GITIGNORE if probe not in ignored]
    return Check(
        "layout", "Makefile verbs + gitignored var/log/, tmp/", "VERIFIED", not gaps,
        "gaps=" + ",".join(gaps) if gaps else "Makefile verbs + ignores present",
        next=None if not gaps else (
            f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo}  "
            f"(then define missing verbs; template {PLUGIN_ROOT}/templates/Makefile)"
        ),
    )


def verify(repo: Path, changed_paths: list[str] | None = None) -> VerifyReport:
    repo = repo.resolve()
    if changed_paths is None:
        changed_paths = sorted(dirty_paths(repo))
    code_paths = sorted(p for p in changed_paths if is_code(p))
    checks: list[Check] = []
    config, rules, config_error = load_project_verifier(repo)
    checks.append(Check(
        "verifier.config", ".agents/VERIFY.py loads", "VERIFIED", config_error is None,
        config_error or "CONFIG/RULES valid", next="FIX: .agents/VERIFY.py" if config_error else None,
    ))
    coverage_min = float(config.get("coverage_min", 60.0))
    timeout = int(config.get("timeout_s", 180))
    if config.get("layout", True):
        checks.append(check_layout(repo))
    tests = find_test_files(repo)
    checks.append(Check(
        "tests.exist", "repository has tests", "VERIFIED", bool(tests),
        f"count={len(tests)}" + (f"; sample={tests[:5]}" if tests else ""),
        next=None if tests else "ADD: tests exercising real subsystems for the changed behavior",
    ))

    with tempfile.TemporaryDirectory(prefix="defuss-vae-") as td:
        tmp = Path(td)
        d = discover_commands(repo, tmp)
        test_cmd = config.get("test_command") or d.test_command
        coverage_cmd = config.get("coverage_command") or d.coverage_command
        integration = list(config.get("integration_commands") or d.integration_commands)
        e2e = list(config.get("e2e_commands") or d.e2e_commands)
        if test_cmd:
            checks.append(check_command("tests.unit", "test command passes", expand_command(str(test_cmd), repo, tmp), repo, timeout))
        else:
            checks.append(Check(
                "tests.unit", "test command passes", "UNKNOWN", None, "no test command configured or discovered",
                next="ADD Makefile target `test` OR EDIT .agents/VERIFY.py CONFIG['test_command']='…'",
            ))
        for i, cmd in enumerate(integration):
            checks.append(check_command(f"tests.integration.{i+1}", "integration test command passes", expand_command(str(cmd), repo, tmp), repo, timeout))
        for i, cmd in enumerate(e2e):
            checks.append(check_command(f"tests.e2e.{i+1}", "e2e (dogfood) command passes", expand_command(str(cmd), repo, tmp), repo, timeout))
        claim = f"coverage >= {coverage_min:g}%"
        if coverage_cmd:
            ccmd = expand_command(str(coverage_cmd), repo, tmp)
            p, log = run_logged("coverage", ccmd, repo, timeout)
            pct, evidence = parse_coverage(repo, tmp, p.stdout) if p.returncode == 0 else (None, "")
            if p.returncode != 0:
                checks.append(Check(
                    "coverage", claim, "VERIFIED", False, f"exit={p.returncode}; log={log}; {trim_output(p.stdout, 1200)}",
                    command=ccmd, next=f"RUN/FIX: {ccmd}  (full output: tail -n 80 {log})",
                ))
            elif pct is None:
                checks.append(Check(
                    "coverage", claim, "UNKNOWN", None, f"command passed but metric UNKNOWN; {evidence}; log={log}",
                    command=ccmd, next="EDIT .agents/VERIFY.py coverage_command to emit a supported total % or JSON ({coverage_json})",
                ))
            else:
                checks.append(Check(
                    "coverage", claim, "VERIFIED", pct >= coverage_min, f"coverage={pct:.2f}% via {evidence}",
                    command=ccmd, next=None if pct >= coverage_min else f"ADD tests until coverage >= {coverage_min:g}%", metric=pct,
                ))
        else:
            checks.append(Check(
                "coverage", claim, "UNKNOWN", None, "no coverage command configured or discovered",
                next="ADD Makefile target `coverage` printing `TOTAL <n>%` OR EDIT .agents/VERIFY.py CONFIG['coverage_command']",
            ))
    for rule in BUILTIN_RULES + rules:
        if isinstance(rule, dict):
            checks.append(run_custom_rule(rule, repo, timeout, code_paths))
        else:
            checks.append(Check("custom.invalid", "custom rule valid", "UNKNOWN", None, f"rule is not dict: {rule!r}", next="FIX .agents/VERIFY.py RULES"))
    return VerifyReport(code_fingerprint(repo, code_paths), code_paths, checks, d.basis)


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
        lines = [f"HYPOTHESIS[command.discovery.{i}] BC {b}" for i, b in enumerate(report.discovery_basis, 1)]
        lines += render_checks(report.checks, evidence_max)
        if report.overall_status == "VERIFIED":
            lines.append(f"VERIFIED[verifier.overall]={'true' if report.overall_value else 'false'}")
        else:
            lines.append("UNKNOWN[verifier.overall]=?")
        return "\n".join(lines)

    text = build(1200)
    # VERIFIED: Claude Code swaps hook strings >10,000 chars for a file path + 2,000-char preview; stay well below.
    return text if len(text) <= 7000 else build(300)


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


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        v = json.loads(path.read_text("utf-8"))
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)


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


def validate_review(path: Path, fp: str, changed_paths: list[str] | None = None) -> tuple[bool, str]:
    obj = read_json(path)
    if not obj:
        return False, "review attestation missing/invalid"
    if obj.get("schema") != 1 or obj.get("code_fingerprint") != fp or obj.get("status") != "VERIFIED":
        return False, "review attestation stale or status!=VERIFIED"
    got = obj.get("checklist")
    if not isinstance(got, list) or any(x not in got for x in REVIEW_CHECKLIST):
        return False, "review checklist incomplete"
    reviewed = obj.get("reviewed_paths")
    if not isinstance(reviewed, list) or any(not isinstance(x, str) for x in reviewed):
        return False, "reviewed_paths missing/invalid"
    if changed_paths is not None:
        expected = {x for x in changed_paths if is_code(x)}
        missing = expected - set(reviewed)
        if missing:
            return False, "reviewed_paths incomplete: " + ", ".join(sorted(missing))
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return False, "review findings must be list"
    for f in findings:
        if not isinstance(f, dict):
            return False, "review finding invalid"
        if f.get("status") != "VERIFIED" or f.get("resolved") is not True:
            return False, "all review findings must be VERIFIED+resolved"
        if not str(f.get("location") or "").strip() or not str(f.get("evidence") or "").strip():
            return False, "each review finding needs location+evidence"
        learning = f.get("learning")
        if not isinstance(learning, dict) or learning.get("status") not in EPI:
            return False, "each finding needs learning status/action"
        if learning.get("status") == "VERIFIED" and learning.get("kind") not in {"test", "verifier", "memory", "none"}:
            return False, "learning.kind invalid"
    return True, "review attestation VERIFIED"


def validate_docs(path: Path, fp: str, changed_paths: list[str], repo: Path | None = None) -> tuple[bool, str]:
    obj = read_json(path)
    if not obj:
        return False, "docs attestation missing/invalid"
    if obj.get("schema") != 1 or obj.get("code_fingerprint") != fp or obj.get("status") != "VERIFIED":
        return False, "docs attestation stale or status!=VERIFIED"
    entries = obj.get("files")
    if not isinstance(entries, list):
        return False, "docs files must be list"
    by_path = {e.get("path"): e for e in entries if isinstance(e, dict)}
    expected = [p for p in changed_paths if is_production_source(p)]
    for rel in expected:
        e = by_path.get(rel)
        if not e:
            return False, f"docs missing file assessment: {rel}"
        if e.get("status") != "VERIFIED":
            return False, f"docs assessment not VERIFIED: {rel}"
        why = str(e.get("why") or "")
        alternative = str(e.get("alternative") or "").strip()
        if not why.startswith(("VERIFIED:", "UNKNOWN:", "HYPOTHESIS:")):
            return False, f"docs why missing epistemic label: {rel}"
        if not alternative:
            return False, f"docs alternative missing: {rel}"
        levels = [e.get(level) for level in DOC_LEVELS]
        for level, action in zip(DOC_LEVELS, levels):
            if action not in {"updated", "not-applicable"}:
                return False, f"docs {level} not assessed: {rel}"
        if all(action == "not-applicable" for action in levels) and not why.startswith("VERIFIED:"):
            return False, f"all-not-applicable docs rationale must be VERIFIED: {rel}"
        if repo is not None and any(action == "updated" for action in levels):
            source = repo / rel
            text = source.read_text("utf-8", errors="replace") if source.exists() else ""
            if not re.search(r"\b(?:VERIFIED|UNKNOWN|HYPOTHESIS):", text):
                return False, f"updated docs lack epistemic prefix in source: {rel}"
    return True, "docs file/method/inline assessment VERIFIED"


VERIFY_NEXT = (
    "NEXT: fix the lowest causal failure; rerun only needed scope. IF cause=? THEN probe before editing "
    f"(smallest discriminating observation; temporary lines tagged `{PROBE_TAG}`). "
    "IF a new deterministic failure class is not yet encoded THEN add a test OR .agents/VERIFY.py rule. NOT claim VERIFIED from inference."
)


def review_instruction(fp: str, path: Path, changed: list[str]) -> str:
    shape = {
        "schema": 1, "status": "VERIFIED", "code_fingerprint": fp,
        "reviewed_paths": [p for p in changed if is_code(p)], "checklist": REVIEW_CHECKLIST,
        "findings": [{"status": "VERIFIED", "resolved": True, "location": "path:line|symbol", "evidence": "...",
                      "learning": {"status": "VERIFIED|UNKNOWN|HYPOTHESIS", "kind": "test|verifier|memory|none", "why": "..."}}],
    }
    return (
        "defuss-vae GATE 2/3 review: REQUIRED (verify=VERIFIED). Do NOT invoke a skill.\n"
        "Review requirements|plan + current diff + EVERY changed code path + relevant callers|callees|tests.\n"
        "PASS1 requirements, correctness, error paths, state|concurrency|resources, security, API|schema compat, "
        "tests (real subsystems in isolation, NOT mocks), e2e (consumes the built artifact), observability (no leftover probe|debug spam).\n"
        "PASS2 Ponytail: delete|reuse → stdlib → native → installed dependency → minimum code; NOT duplicate machinery, speculative config|abstraction, unmeasured optimization.\n"
        "Actionable finding REQUIRES location + causal evidence + minimal fix; fix EVERY one. "
        "IF recurrence mechanically checkable THEN regression test OR .agents/VERIFY.py rule ELSE learning.status=UNKNOWN + why.\n"
        "IF you edit source|test THEN rerun LOOP before attesting (fingerprint changes).\n"
        f"THEN write {path} (findings=[] IF none):\n{json.dumps(shape, separators=(',', ':'))}"
    )


def docs_instruction(fp: str, path: Path, changed: list[str]) -> str:
    prod = [p for p in changed if is_production_source(p)]
    entry = {"path": "<each required path>", "status": "VERIFIED", "file": "updated|not-applicable", "method": "updated|not-applicable",
             "inline": "updated|not-applicable", "alternative": "plausible alternative OR why none applies", "why": "VERIFIED: ..."}
    return (
        "defuss-vae GATE 3/3 docs: REQUIRED (review=VERIFIED). Do NOT invoke a skill.\n"
        "EVERY changed production file: assess file, changed method|function, non-obvious inline. Document why this design > plausible alternative, "
        "NOT what syntax does; material claims prefixed VERIFIED:|HYPOTHESIS:|UNKNOWN:; not-applicable REQUIRES a concrete reason; no filler.\n"
        f"THEN write {path}:\n"
        + json.dumps({"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [entry]}, separators=(",", ":"))
        + "\nrequired paths=" + json.dumps(prod, separators=(",", ":"))
    )


@dataclasses.dataclass
class Gate:
    done: bool
    text: str


def bounded(repo: Path, session_id: str, text: str, limit: int = 9000) -> str:
    # VERIFIED: Claude Code swaps hook strings >10,000 chars for a file path + 2,000-char preview; keep head + tail
    # (AGENT_CMD/LOOP live at the end) visible instead, and the full text on disk.
    if len(text) <= limit:
        return text
    full = session_dir(repo, session_id) / "gate.txt"
    full.write_text(text, "utf-8")
    return text[:6000] + f"\n…truncated; full gate text: {full}\n" + text[-2500:]


def verify_key(repo: Path, fp: str) -> str:
    # WHY: policy files sit outside the code fingerprint yet change verification results, so they key the cache too.
    parts = [fp, file_hash(repo / ".agents" / "VERIFY.py"), file_hash(repo / ".gitignore")]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def gate(repo: Path, session_id: str, plugin_root: Path = PLUGIN_ROOT) -> Gate:
    """One state machine for the Stop hook and the in-turn CLI: verify → review → docs.

    WHY a CLI loop: Claude Code honors one Stop block per turn, so the agent must be able to
    re-run the same gate itself until done instead of relying on repeated hook continuations."""
    state = load_session(repo, session_id)
    state["gate_runs"] = int(state.get("gate_runs", 0)) + 1
    sp = state_path(repo, session_id)
    changed = changed_since(repo, state["baseline"])
    code = [p for p in changed if is_code(p)]
    if not code:
        write_json(sp, state)
        return Gate(True, "VERIFIED[gate]=true BC ∅ code changes since session baseline")
    bootstrap(repo, plugin_root)
    fp = code_fingerprint(repo, code)
    loop = (f"LOOP: python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {session_id} "
            "→ repeat until VERIFIED[gate]=true; git commit stays denied until then.")
    # WHY cache: fingerprint + policy hashes identify the verified content, so review/docs loop turns skip re-running suites.
    if state.get("verified_key") != verify_key(repo, fp):
        report = verify(repo, changed)
        if not report.verified:
            state["verified_fp"] = state["verified_key"] = None
            failing = ",".join(c.id + ("=?" if c.status != "VERIFIED" else "") for c in report.checks if not c.passes())
            if state.get("last_fail") != failing:
                append_episodes(repo, session_id, [f"FAIL {failing}"], plugin_root)
                state["last_fail"] = failing
            write_json(sp, state)
            return Gate(False, bounded(repo, session_id, "defuss-vae GATE 1/3 verify: FAIL\n" + render_report(report) + "\n" + VERIFY_NEXT + "\n" + loop))
        cov = next((c.metric for c in report.checks if c.id == "coverage"), None)
        state.update(verified_fp=fp, verified_key=verify_key(repo, fp), last_fail=None, coverage=cov)
    rp = attestation_path(repo, session_id, "review")
    ok, why = validate_review(rp, fp, changed)
    if not ok:
        write_json(sp, state)
        return Gate(False, bounded(repo, session_id, review_instruction(fp, rp, changed) + f"\nSTATE: {why}\n" + loop))
    dp = attestation_path(repo, session_id, "docs")
    ok, why = validate_docs(dp, fp, changed, repo)
    if not ok:
        write_json(sp, state)
        return Gate(False, bounded(repo, session_id, docs_instruction(fp, dp, changed) + f"\nSTATE: {why}\n" + loop))
    if state.get("completed_fp") != fp:
        state["completed_fp"] = fp
        cov = state.get("coverage")
        paths = ",".join(code[:4]) + (f"(+{len(code) - 4})" if len(code) > 4 else "")
        entries = [f"DONE fp={fp[:12]} cov={'?' if cov is None else f'{cov:.1f}%'} paths={paths}"]
        for f in (read_json(rp) or {}).get("findings") or []:
            learning = f.get("learning") if isinstance(f.get("learning"), dict) else {}
            lesson = " ".join(str(learning.get("why") or f.get("evidence") or "").split())[:160]
            entries.append(f"FINDING {f.get('location')} learn={learning.get('kind')}: {lesson}")
        append_episodes(repo, session_id, entries, plugin_root)
    write_json(sp, state)
    return Gate(True, f"VERIFIED[gate]=true BC verify+review+docs@fp={fp[:12]}")


def stop_gate(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    g = gate(repo, str(event.get("session_id") or "unknown"), plugin_root)
    if g.done:
        return None
    # VERIFIED: Claude Code honors one Stop block per turn and additionalContext alone ends the turn;
    # later stops leave the gate text for the next prompt while the commit gate stays closed.
    if event.get("stop_hook_active"):
        return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": g.text}}
    return {"decision": "block", "reason": g.text}


def is_commit_command(command: str) -> bool:
    return COMMIT_RE.search(command) is not None


def commit_gate(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("tool_name") or "") not in {"Bash", "PowerShell"}:
        return None
    if not is_commit_command(str((event.get("tool_input") or {}).get("command") or "")):
        return None
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    state = load_session(repo, sid)
    changed = changed_since(repo, state["baseline"])
    code_changed = [p for p in changed if is_code(p)]
    if not code_changed:
        return None
    fp = code_fingerprint(repo, code_changed)
    review_ok, review_reason = validate_review(attestation_path(repo, sid, "review"), fp, changed)
    docs_ok, docs_reason = validate_docs(attestation_path(repo, sid, "docs"), fp, changed, repo)
    verified = state.get("verified_fp") == fp
    if verified and review_ok and docs_ok:
        return None
    return deny(
        f"defuss-vae commit gate: VERIFIED[verifier]={str(verified).lower()}; review={review_reason}; docs={docs_reason}. "
        f"Run python3 {PLUGIN_ROOT}/scripts/vae.py gate --repo {repo} --session {sid} until VERIFIED[gate]=true."
    )


def deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


# One rule text for both channels: SessionStart context (Claude Code) and the AGENTS.md block (hosts without hooks).
RULES_TEXT = f"""Skills `plan` `implement` `review` `finalize` are human-triggered only; never auto-invoke them. Outside skills write plain concise prose.
Evidence > assumption: IF a runtime fact is unknown THEN observe before editing (read → existing test/command → smallest discriminating probe → ask). Temporary probe lines carry `{PROBE_TAG}` and the gate rejects leftovers; read logs bounded (`make log`, tail, grep); no log spraying.
Layout: `.agents/` agent state; `Makefile` verbs start stop status log metrics bench test e2e; services only via `make start` → `var/log/<svc>.stdout|.stderr`, `tmp/<svc>.pid` (gitignored); programs read `input/`, write `output/`.
test = real subsystems in isolation, no mocks; e2e = build the publishable artifact and consume it like a user.
Epistemics: `VERIFIED` = direct evidence; `HYPOTHESIS` = testable inference + falsifier; `UNKNOWN` = not established. Never promote by rhetoric.
Ponytail: understand → YAGNI → reuse → stdlib → native → installed dependency → minimum code; bug fix = root cause + sibling callers.
Docs: why this design beats a plausible alternative; prefix material claims `VERIFIED:`, `HYPOTHESIS:` or `UNKNOWN:`.
Lessons: test | `.agents/VERIFY.py` rule > MEMORY line > EPISODES line."""


def session_context(repo: Path, session_id: str, plugin_root: Path = PLUGIN_ROOT) -> str:
    parts = [
        "defuss-vae (verified agentic engineering):\n" + RULES_TEXT,
        f"GATE before finishing code changes: python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {session_id} "
        "(repeat until VERIFIED[gate]=true; git commit is denied until then).",
    ]
    # WHY inject instead of "go read": memory that is not loaded is not used; the budgets keep this cheap.
    for name, budget in STATE_BUDGET.items():
        entries = memory_entries(repo / ".agents" / name)
        if entries:
            parts.append(f".agents/{name}:\n" + "\n".join(entries)[:budget])
    recent = episode_entries(repo)[-3:]
    if recent:
        parts.append("Recent .agents/EPISODES.md:\n" + "\n".join(recent))
    return "\n".join(parts)[:9000]


def session_start(event: dict[str, Any], plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any] | None:
    repo = git_root(Path(event.get("cwd") or os.getcwd()).resolve())
    if not repo:
        return None
    sid = str(event.get("session_id") or "unknown")
    # VERIFIED: resume/compact can fire SessionStart for the same session; preserving the original baseline prevents gate bypass.
    if not state_path(repo, sid).exists():
        init_session(repo, sid)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": session_context(repo, sid, plugin_root)}}


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


def init_project(repo: Path, template_root: Path = PLUGIN_ROOT) -> list[str]:
    """Scaffold the layout; never overwrites anything the project already owns."""
    rels = (".agents/VERIFY.py", ".agents/EPISODES.md", ".agents/MEMORY.md", ".agents/CLI_GIST.md", "Makefile")
    changed = [r for r in rels if ensure_from_template(repo, r, template_root)]
    ignored = ignored_probes(repo)
    gi = repo / ".gitignore"
    text = gi.read_text("utf-8") if gi.exists() else ""
    add = [line for probe, line in GITIGNORE.items() if probe not in ignored]
    if add:
        gi.write_text(text + ("\n" if text and not text.endswith("\n") else "") + "\n".join(add) + "\n", "utf-8")
        changed.append(".gitignore")
    before = (repo / "AGENTS.md").read_text("utf-8") if (repo / "AGENTS.md").exists() else None
    ensure_managed_agents(repo)
    if (repo / "AGENTS.md").read_text("utf-8") != before:
        changed.append("AGENTS.md")
    return changed


def doctor_repo(repo: Path) -> list[Check]:
    """Deterministic memory hygiene: loadable policy, bounded + epistemically tagged state, layout."""
    init_cmd = f"RUN: python3 {PLUGIN_ROOT}/scripts/vae.py init --repo {repo}"
    exists = (repo / ".agents" / "VERIFY.py").exists()
    _, _, err = load_project_verifier(repo)
    checks = [Check("state.verifier", ".agents/VERIFY.py exists and loads", "VERIFIED", exists and err is None,
                    err or ("ok" if exists else "missing"), next=None if exists and err is None else (f"FIX: {err}" if err else init_cmd))]
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
    checks.append(check_layout(repo))
    return checks
