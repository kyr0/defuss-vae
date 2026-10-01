"""Repository facts: git state, path classification, process execution, JSON state I/O.

VERIFIED: stdlib-only, so lifecycle hooks need no installs and run on any python3 >= 3.9."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
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
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "bun.lock", "bun.lockb", "uv.lock",
    "yarn.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "requirements.txt", "pom.xml", "build.gradle",
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
    # VERIFIED: make 3.81 execs simple recipes (`uv run pytest`) with its *own* PATH, ignoring a Makefile `export PATH`;
    # appending the installers' default dirs lets the gate find a uv/bun that `make setup` installed this session,
    # while a tool already on PATH (e.g. Homebrew's) keeps precedence.
    merged["PATH"] = os.pathsep.join([merged.get("PATH", ""), str(Path.home() / ".local/bin"), str(Path.home() / ".bun/bin")])
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
            check=False,
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
    p = subprocess.run(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
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
        or n.startswith("test_") or ".test." in n or ".spec." in n
        or n.endswith(("_test.py", "_test.go", "test.java", "tests.java"))
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


def make_targets(repo: Path) -> set[str]:
    try:
        text = (repo / "Makefile").read_text("utf-8", errors="replace")
    except OSError:
        return set()
    # Rule lines only (`a b: deps`, `a::`); `:=` and `::=` assignments are excluded by the lookahead.
    return {n for m in re.finditer(r"(?m)^([A-Za-z0-9_.%/ -]+?)\s*:(?!:?=)", text) for n in m.group(1).split()}


def safe_name(raw: str) -> str:
    # lstrip(".") blocks `..` traversal out of tmp/vae/ via a crafted session id.
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).lstrip(".")[:80] or "_"


def runtime_dir(repo: Path, rel: str) -> Path:
    """Layout runtime dir that ignores itself: gate state and logs never reach `git status`,
    even in repos that have not gitignored var/ and tmp/ yet."""
    d = repo / rel
    d.mkdir(parents=True, exist_ok=True)
    marker = d / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", "utf-8")
    return d


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        v = json.loads(path.read_text("utf-8"))
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)
