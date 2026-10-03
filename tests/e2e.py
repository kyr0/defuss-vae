#!/usr/bin/env python3
"""Dogfood e2e: install the release zip like a user and drive only its shipped entrypoints
(CLI + hook adapter) against a fresh consumer project — never the source tree. Evidence → output/e2e.json.

`--bench` reuses the same installed copy to time the gate cold (runs the suite) vs cached (review/docs loop)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PY = sys.executable
SID = "e2e"
steps: list[dict] = []


def run(args: list[str], cwd: Path, stdin: str | None = None, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(args, cwd=cwd, input=stdin, text=True, capture_output=True, timeout=300, check=False)
    if p.returncode not in ok:
        raise SystemExit(f"FAIL {' '.join(args)} → exit={p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def step(name: str, ok: bool, evidence: str) -> None:
    steps.append({"step": name, "ok": ok, "evidence": evidence[:300]})
    print(f"{'VERIFIED' if ok else 'FAILED'}[{name}] BC {evidence[:160]}")
    if not ok:
        raise SystemExit(f"e2e step failed: {name}\n{evidence}")


def define(makefile: str, verb: str, recipe: str) -> str:
    return re.sub(rf"(?m)^{verb}:.*$", lambda _: f"{verb}:\n\t" + recipe.replace("\n", "\n\t"), makefile, count=1)


def consumer(root: Path, td: Path) -> Path:
    proj = td / "proj"
    proj.mkdir()
    for cmd in ("git init -q", "git config user.email e2e@example.invalid", "git config user.name e2e"):
        run(cmd.split(), proj)
    (proj / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    )
    (proj / "input").mkdir()
    (proj / "input/pair.txt").write_text("2 3\n")
    out = run([PY, str(root / "scripts/vae.py"), "init", "--repo", str(proj)], proj).stdout
    step("init.scaffold", "Makefile" in out and ".gitignore" in out, out.strip())
    with (proj / ".gitignore").open("a") as f:
        f.write("!input/pair.txt\n")  # input/* is ignored by default; commit the e2e fixture explicitly
    mk = (proj / "Makefile").read_text()
    mk = define(mk, "lint", "python3 -m py_compile calc.py test_calc.py")  # hermetic stand-in; real projects: ruff / oxlint
    mk = define(mk, "test", "python3 -m unittest discover -p 'test_*.py'")
    # The consumer dogfoods its own artifact: build a zip, import only from it, read input/, write output/.
    mk = define(mk, "e2e", "mkdir -p tmp output && rm -f tmp/calc.zip && python3 -m zipfile -c tmp/calc.zip calc.py\n"
                "cd tmp && PYTHONPATH=calc.zip python3 -c \"import calc; a, b = map(int, open('../input/pair.txt').read().split()); "
                "open('../output/sum.txt', 'w').write(str(calc.add(a, b)))\"\ntest \"$$(cat output/sum.txt)\" = 5")
    mk = define(mk, "metrics", "wc -l calc.py")
    mk = define(mk, "bench", "python3 -m timeit -s 'from calc import add' 'add(2, 3)'")
    # Stdlib-only fixture keeps e2e hermetic (no network); real projects use uv/bun per the template hints.
    mk = define(mk, "coverage", "@python3 -m trace --count --summary -C tmp/cov --module unittest discover -p 'test_*.py' 2>/dev/null "
                "| awk '$$3==\"calc\"{print \"TOTAL\", $$2}'")
    (proj / "Makefile").write_text(mk)
    run(["git", "add", "-A"], proj)
    run(["git", "commit", "-qm", "scaffold"], proj)
    return proj


def hook(root: Path, proj: Path, event: dict) -> dict | None:
    p = run([PY, str(root / "hooks/lifecycle.py")], proj, stdin=json.dumps({"cwd": str(proj), "session_id": SID, **event}))
    return json.loads(p.stdout) if p.stdout.strip() else None


def gate(root: Path, proj: Path) -> subprocess.CompletedProcess[str]:
    return run([PY, str(root / "scripts/vae.py"), "gate", "--repo", str(proj), "--session", SID], proj, ok=(0, 2))


def edit_feature(proj: Path) -> None:
    (proj / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "def sub(a, b):\n    # VERIFIED: plain subtraction keeps Python numeric semantics; a helper class would add nothing.\n"
        "    return a - b\n"
    )
    (proj / "test_calc.py").write_text(
        "import unittest\nfrom calc import add, sub\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
        "    def test_sub(self):\n        self.assertEqual(sub(5, 3), 2)\n"
    )


def attest(proj: Path, fp: str) -> None:
    vae = proj / "tmp/vae" / SID
    (vae / "review.json").write_text(json.dumps({
        "schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "reviewed_paths": ["calc.py", "test_calc.py"],
        "checklist": ["requirements", "correctness", "callers", "errors", "state-concurrency", "security", "tests", "e2e",
                      "observability", "structure", "reuse", "yagni", "smells-gotchas", "abstraction", "performance", "docs"],
        "findings": [],
    }))


def attest_docs(proj: Path, fp: str) -> None:
    (proj / "tmp/vae" / SID / "docs.json").write_text(json.dumps({"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [{
        "path": "calc.py", "status": "VERIFIED", "file": "not-applicable", "method": "updated", "inline": "not-applicable",
        "alternative": "operator.sub import would hide nothing and add an import", "why": "VERIFIED: subtraction is the whole contract.",
    }]}))


def fingerprint(text: str) -> str:
    m = re.search(r'"code_fingerprint":"([0-9a-f]{64})"', text)
    if not m:
        raise SystemExit("no fingerprint in gate text:\n" + text)
    return m.group(1)


def main() -> int:
    zip_path = Path(sys.argv[1]).resolve()
    bench = "--bench" in sys.argv
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="vae-e2e-") as raw:
        td = Path(raw).resolve()
        with zipfile.ZipFile(zip_path) as z:
            root = td / "install" / "defuss-vae"
            z.extractall(root)
        step("install.doctor", run([PY, str(root / "scripts/vae.py"), "doctor"], td).stdout.startswith("VERIFIED[plugin.files]=true"), str(zip_path.name))
        proj = consumer(root, td)
        ctx = hook(root, proj, {"hook_event_name": "SessionStart", "source": "startup"})
        step("hook.session_start", "gate --repo" in ctx["hookSpecificOutput"]["additionalContext"], "context carries the gate command")
        edit_feature(proj)

        if bench:
            timings = {}
            for label in ("cold_verify", "cached_1", "cached_2"):
                t0 = time.perf_counter()
                gate(root, proj)
                timings[label] = round(time.perf_counter() - t0, 3)
            result = {"gate_seconds": timings, "zip": zip_path.name}
            (out_dir / "bench.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
            return 0

        blocked = hook(root, proj, {"hook_event_name": "Stop", "stop_hook_active": False})
        reason = (blocked or {}).get("reason", "")
        step("hook.stop.blocks_for_review", (blocked or {}).get("decision") == "block" and "GATE 2/3 review" in reason, reason[:300])
        fp = fingerprint(reason)
        denied = hook(root, proj, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -am feat"}})
        step("hook.commit.denied", denied["hookSpecificOutput"]["permissionDecision"] == "deny", denied["hookSpecificOutput"]["permissionDecisionReason"])
        attest(proj, fp)
        docs = gate(root, proj)
        step("cli.gate.docs", docs.returncode == 2 and "GATE 3/3 docs" in docs.stdout, docs.stdout[:200])
        attest_docs(proj, fp)
        done = gate(root, proj)
        step("cli.gate.done", done.returncode == 0 and "VERIFIED[gate]=true" in done.stdout, done.stdout.strip())
        step("hook.stop.allows", hook(root, proj, {"hook_event_name": "Stop", "stop_hook_active": False}) is None, "no block after gates")
        step("hook.commit.allowed", hook(root, proj, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -am feat"}}) is None, "gate clear")
        run(["git", "commit", "-qam", "feat: add sub"], proj)
        episodes = (proj / ".agents/EPISODES.md").read_text()
        step("episodes.done", " DONE fp=" in episodes, episodes.strip().splitlines()[-1])
        step("consumer.output", (proj / "output/sum.txt").read_text() == "5", "input/pair.txt → artifact → output/sum.txt")

        (proj / "calc.py").write_text((proj / "calc.py").read_text() + "print('debug')  # vae" + ":probe\n")
        probe = gate(root, proj)
        step("gate.rejects_leftover_probe", probe.returncode == 2 and "hygiene.probes" in probe.stdout, "calc.py probe line")
        run(["git", "checkout", "--", "calc.py"], proj)

        # The echo proves the service shell itself ran and wrote, separately from python's banner.
        started_svc = run(["make", "-s", "start", "RUN=echo svc-shell; python3 -u -m http.server 0 --bind 127.0.0.1"], proj)
        # WHY poll, not a fixed sleep: a cold python3 on CI macOS runners can take seconds to print its banner.
        deadline, log = time.time() + 20, ""
        while "Serving HTTP" not in log and time.time() < deadline:
            time.sleep(0.2)
            log = run(["make", "-s", "log", "N=5"], proj).stdout
        diag = ""
        if "Serving HTTP" not in log:  # failure-only diagnostics: what runs in the service's group, which python3
            pgid = (proj / "tmp/app.pid").read_text().strip()
            ps = run(["ps", "-A", "-o", "pid=,pgid=,stat=,etime=,command="], proj).stdout.splitlines()
            py = run(["sh", "-c", "command -v python3; python3 -V 2>&1"], proj, ok=(0, 1, 127)).stdout
            diag = f"; group={[ln.strip() for ln in ps if ln.split()[1:2] == [pgid]]}; python3={py.split()}"
        stopped = run(["make", "-s", "stop"], proj).stdout
        status = run(["make", "-s", "status"], proj, ok=(2,)).stdout
        parts = {"start": "running pid=" in started_svc.stdout, "log": "Serving HTTP" in log, "stop": "stopped" in stopped, "status": "stopped" in status}
        step("service.lifecycle", all(parts.values()), f"{parts}; start={started_svc.stdout.strip()!r}; log={log.strip()!r}; status={status.strip()!r}{diag}")
        step("doctor.repo", run([PY, str(root / "scripts/vae.py"), "doctor", "--repo", str(proj)], proj).returncode == 0, "budgets, tags, layout")

    evidence = {"zip": zip_path.name, "seconds": round(time.time() - started, 2), "steps": steps}
    (out_dir / "e2e.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"VERIFIED[e2e]=true BC {len(steps)} steps; evidence=output/e2e.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
