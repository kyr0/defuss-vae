from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vae_core as v

PY = sys.executable
TEST_CMD = f"{PY} -m unittest discover -p 'test_*.py'"


def sh(cmd: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=cwd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and p.returncode:
        raise AssertionError(f"cmd failed {cmd}: {p.stdout}")
    return p


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        self.repo.mkdir()
        self.repo = self.repo.resolve()  # macOS: /var → /private/var; git reports resolved paths
        sh("git init -q", self.repo)
        sh("git config user.email test@example.invalid", self.repo)
        sh("git config user.name Test", self.repo)

    def tearDown(self):
        self.td.cleanup()

    def write(self, rel: str, text: str) -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit_all(self, msg="init"):
        sh("git add -A && git commit -qm " + msg, self.repo)

    def make_python_project(self, test_command: str = TEST_CMD, coverage: str = "75", rules: str = "[]", layout: bool = False):
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        self.write("test_calc.py", "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n")
        self.write(".agents/VERIFY.py", (
            f"CONFIG={{'coverage_min':60, 'test_command':{test_command!r},\n"
            f" 'coverage_command':\"{PY} -c \\\"print('TOTAL {coverage}%')\\\"\", 'timeout_s':60, 'layout':{layout}}}\n"
            f"RULES={rules}\n"
        ))
        self.commit_all()

    def check(self, report: v.VerifyReport, cid: str) -> v.Check:
        return next(c for c in report.checks if c.id == cid)


class VerifyTests(RepoCase):
    def test_passes_with_compact_evidence_and_self_ignored_log(self):
        self.make_python_project()
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        report = v.verify(self.repo, ["calc.py"])
        self.assertTrue(report.verified, v.render_report(report))
        unit = self.check(report, "tests.unit")
        self.assertTrue(unit.evidence.startswith("exit=0; ") and len(unit.evidence) < 250, unit.evidence)
        self.assertEqual(self.check(report, "coverage").metric, 75.0)
        self.assertIn("Ran 1 test", (self.repo / "var/log/vae/tests.unit.log").read_text())
        self.assertNotIn("var/", sh("git status --porcelain", self.repo).stdout)

    def test_failing_test_reports_tail_and_log_path(self):
        self.make_python_project()
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        report = v.verify(self.repo, ["calc.py"])
        unit = self.check(report, "tests.unit")
        self.assertFalse(unit.value)
        self.assertIn("log=var/log/vae/tests.unit.log", unit.evidence)
        self.assertIn("AssertionError", unit.evidence)
        self.assertIn("tail -n 80 var/log/vae/tests.unit.log", unit.next)

    def test_timeout_with_partial_output_fails_closed_instead_of_crashing(self):
        # Regression: TimeoutExpired.stdout is bytes even with text=True; str + bytes crashed the Stop hook (fail-open).
        self.make_python_project(test_command="echo partial; sleep 5")
        self.write(".agents/VERIFY.py", self.repo.joinpath(".agents/VERIFY.py").read_text().replace("'timeout_s':60", "'timeout_s':1"))
        unit = self.check(v.verify(self.repo, ["calc.py"]), "tests.unit")
        self.assertFalse(unit.value)
        self.assertIn("exit=124", unit.evidence)
        self.assertIn("partial", unit.evidence)
        self.assertIn("TIMEOUT>1s", unit.evidence)

    def test_coverage_without_metric_fails_closed(self):
        self.make_python_project()
        self.write(".agents/VERIFY.py", f"CONFIG={{'test_command':{TEST_CMD!r},'coverage_command':'echo no metric here','layout':False}}\nRULES=[]\n")
        report = v.verify(self.repo, ["calc.py"])
        self.assertFalse(report.verified)
        self.assertEqual(self.check(report, "coverage").status, "UNKNOWN")

    def test_coverage_below_minimum_fails(self):
        self.make_python_project(coverage="50")
        report = v.verify(self.repo, ["calc.py"])
        c = self.check(report, "coverage")
        self.assertEqual((c.status, c.value), ("VERIFIED", False))

    def test_custom_exact_rule(self):
        self.make_python_project(rules="[{'id':'marker','kind':'contains','path':'calc.py','text':'return a + b','claim':'canonical add'}]")
        report = v.verify(self.repo, ["calc.py"])
        self.assertTrue(report.verified, v.render_report(report))
        self.assertTrue(self.check(report, "marker").value)

    def test_leftover_probe_fails_until_removed(self):
        self.make_python_project()
        self.write("calc.py", f"def add(a, b):\n    print(a, b)  # {v.PROBE_TAG}\n    return a + b\n")
        c = self.check(v.verify(self.repo, ["calc.py"]), "hygiene.probes")
        self.assertFalse(c.value)
        self.assertIn("calc.py:2", c.evidence)
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        self.assertTrue(self.check(v.verify(self.repo, ["calc.py"]), "hygiene.probes").value)

    def test_glob_rule_only_sees_changed_files(self):
        rule = "[{'id':'hdr','kind':'contains','glob':'*.py','text':'# SPDX','claim':'license header'}]"
        self.make_python_project(rules=rule)
        self.write("new.py", "x = 1\n")
        c = self.check(v.verify(self.repo, ["new.py"]), "hdr")
        self.assertEqual(c.evidence, "glob='*.py' files=1; hits=['new.py']")
        self.write("new.py", "# SPDX\nx = 1\n")
        self.assertTrue(self.check(v.verify(self.repo, ["new.py"]), "hdr").value)

    def test_template_mock_rule_catches_mocks_but_not_itself(self):
        namespace: dict = {}
        exec((ROOT / "templates/VERIFY.py").read_text(), namespace)
        rule = namespace["RULES"][0]
        rx = re.compile(rule["pattern"])
        self.assertIsNone(rx.search((ROOT / "templates/VERIFY.py").read_text()))
        for bad in ("from unittest import mock", "m = MagicMock()", "jest" + ".mock('./db')", "vi" + ".fn()", "mock" + ".patch('x')", "@Mock\n  Foo foo;"):
            self.assertIsNotNone(rx.search(bad), bad)
        for fine in ("def mockup(): pass", "# tests avoid mocks", "monkeypatch.setenv('A', '1')"):
            self.assertIsNone(rx.search(fine), fine)
        self.make_python_project(rules=repr(namespace["RULES"]))
        self.write("test_db.py", "from unittest import mock\n")
        self.assertFalse(self.check(v.verify(self.repo, ["test_db.py"]), "tests.no-mocks").value)

    def test_makefile_verbs_win_discovery(self):
        self.write("Makefile", "test:\n\t@true\ne2e: dist\n\t@true\nX := y\nURL ::= z\n")
        self.write("package.json", json.dumps({"scripts": {"test": "jest", "e2e": "playwright test"}}))
        d = v.discover_commands(self.repo, self.repo)
        self.assertEqual((d.test_command, d.e2e_commands), ("make test", ["make e2e"]))
        self.assertEqual(v.make_targets(self.repo), {"test", "e2e"})

    def test_layout_gaps_then_init_closes_them(self):
        self.make_python_project(layout=True)
        c = self.check(v.verify(self.repo, ["calc.py"]), "layout")
        self.assertFalse(c.value)
        self.assertIn("Makefile:start", c.evidence)
        self.assertIn("gitignore:var/log/", c.evidence)
        self.assertIn("scripts/vae.py init --repo", c.next)
        v.init_project(self.repo, ROOT)
        self.assertTrue(v.check_layout(self.repo).value, v.check_layout(self.repo).evidence)


class GateTests(RepoCase):
    def stop(self, sid: str, active: bool = False):
        return v.stop_gate({"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": sid, "stop_hook_active": active}, ROOT)

    def commit(self, sid: str, command: str = "git commit -m x"):
        return v.commit_gate({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.repo), "session_id": sid})

    def attest(self, sid: str, fp: str, findings: list | None = None):
        v.write_json(v.attestation_path(self.repo, sid, "review"), {
            "schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": v.REVIEW_CHECKLIST,
            "reviewed_paths": ["calc.py"], "findings": findings or [],
        })

    def attest_docs(self, sid: str, fp: str):
        v.write_json(v.attestation_path(self.repo, sid, "docs"), {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [{
            "path": "calc.py", "status": "VERIFIED", "file": "updated", "method": "updated", "inline": "updated",
            "alternative": "wrapper/helper would add no semantic value",
            "why": "VERIFIED: direct addition preserves Python numeric semantics with less machinery.",
        }]})

    def test_sequence_blocks_once_then_review_docs_done_and_commit_gate(self):
        self.make_python_project()
        sid = "s1"
        v.init_session(self.repo, sid)
        self.write("calc.py", "def add(a, b):\n    # VERIFIED: direct addition preserves Python numeric semantics.\n    return a + b\n")
        out = self.stop(sid)
        self.assertEqual(out["decision"], "block")
        self.assertIn("GATE 2/3 review", out["reason"])
        self.assertIn(f"gate --repo {self.repo} --session {sid}", out["reason"])
        self.assertEqual(self.commit(sid)["hookSpecificOutput"]["permissionDecision"], "deny")
        fp = v.code_fingerprint(self.repo, ["calc.py"])
        finding = {"status": "VERIFIED", "resolved": True, "location": "calc.py:2", "evidence": "sign bug",
                   "learning": {"status": "VERIFIED", "kind": "test", "why": "subtraction slipped in; test_add pins it"}}
        self.attest(sid, fp, [finding])
        self.assertIn("GATE 3/3 docs", self.stop(sid)["reason"])
        self.attest_docs(sid, fp)
        self.assertIsNone(self.stop(sid))
        self.assertIsNone(self.stop(sid))
        self.assertIsNone(self.commit(sid))
        entries = v.episode_entries(self.repo)
        self.assertEqual(sum(" DONE " in e for e in entries), 1, entries)
        self.assertTrue(any("FINDING calc.py:2 learn=test: subtraction slipped in" in e for e in entries), entries)

    def test_repeat_stop_in_same_turn_does_not_block(self):
        self.make_python_project()
        v.init_session(self.repo, "s2")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        self.assertEqual(self.stop("s2")["decision"], "block")
        out = self.stop("s2", active=True)
        self.assertNotIn("decision", out)
        self.assertIn("GATE 2/3 review", out["hookSpecificOutput"]["additionalContext"])

    def test_verification_is_cached_by_fingerprint_and_policy(self):
        self.make_python_project(test_command=f"echo run >> runs.txt && {TEST_CMD}")
        v.init_session(self.repo, "s3")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        runs = lambda: (self.repo / "runs.txt").read_text().count("run")
        v.gate(self.repo, "s3", ROOT)
        v.gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 1)
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        v.gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 2)
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("# policy edit\n")
        v.gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 3)

    def test_failures_logged_once_per_distinct_set(self):
        self.make_python_project()
        v.init_session(self.repo, "s4")
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        g = v.gate(self.repo, "s4", ROOT)
        self.assertFalse(g.done)
        self.assertIn("GATE 1/3 verify: FAIL", g.text)
        self.write("calc.py", "def add(a, b):\n    return a - b - 0\n")
        v.gate(self.repo, "s4", ROOT)
        fails = [e for e in v.episode_entries(self.repo) if " FAIL " in e]
        self.assertEqual(len(fails), 1, fails)
        self.assertTrue(fails[0].endswith("FAIL tests.unit"), fails)

    def test_source_edit_invalidates_attestations(self):
        self.make_python_project()
        v.init_session(self.repo, "s5")
        self.write("calc.py", "def add(a,b): return a+b\n")
        fp1 = v.code_fingerprint(self.repo, ["calc.py"])
        self.attest("s5", fp1)
        self.write("calc.py", "def add(a,b): return (a+b)\n")
        fp2 = v.code_fingerprint(self.repo, ["calc.py"])
        self.assertNotEqual(fp1, fp2)
        self.assertFalse(v.validate_review(v.attestation_path(self.repo, "s5", "review"), fp2)[0])

    def test_review_attestation_must_cover_changed_code_paths(self):
        self.make_python_project()
        v.init_session(self.repo, "s6")
        self.write("calc.py", "def add(a,b): return a+b\n")
        fp = v.code_fingerprint(self.repo, ["calc.py"])
        rp = v.attestation_path(self.repo, "s6", "review")
        v.write_json(rp, {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": v.REVIEW_CHECKLIST, "reviewed_paths": [], "findings": []})
        ok, reason = v.validate_review(rp, fp, ["calc.py"])
        self.assertFalse(ok)
        self.assertIn("reviewed_paths incomplete", reason)

    def test_oversized_gate_text_keeps_head_tail_and_full_copy(self):
        text = "HEAD" + "x" * 20000 + "LOOP: python3 vae.py gate"
        out = v.bounded(self.repo, "big", text)
        self.assertLess(len(out), 9000)
        self.assertTrue(out.startswith("HEAD") and out.endswith("LOOP: python3 vae.py gate"))
        self.assertEqual((self.repo / "tmp/vae/big/gate.txt").read_text(), text)
        self.assertEqual(v.bounded(self.repo, "big", "short"), "short")

    def test_commit_detection_covers_global_options(self):
        for cmd in ("git commit -m x", "git -C . commit", "git -c core.hooksPath=/dev/null commit -m x",
                    "git --no-pager commit", "npm test && git commit -am y"):
            self.assertTrue(v.is_commit_command(cmd), cmd)
        for cmd in ("git log --grep commit", "legit commit", "git status"):
            self.assertFalse(v.is_commit_command(cmd), cmd)

    def test_session_start_keeps_baseline_and_injects_memory(self):
        self.make_python_project()
        v.init_project(self.repo, ROOT)
        with (self.repo / ".agents/MEMORY.md").open("a") as f:
            f.write("- VERIFIED[db] migrations run via `make migrate` BC 2026-09-30 run\n")
        v.append_episodes(self.repo, "old", ["LESSON HYPOTHESIS[cache] falsified BC probe"], ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "same"}
        ctx = v.session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- VERIFIED[db] migrations run via `make migrate`", ctx)
        self.assertNotIn("VERIFIED[scope] fact", ctx)
        self.assertIn("LESSON HYPOTHESIS[cache]", ctx)
        self.assertIn(f"gate --repo {self.repo} --session same", ctx)
        self.assertLess(len(ctx), 9001)
        s1 = v.read_json(v.state_path(self.repo, "same"))
        self.write("calc.py", "def add(a,b): return a+b\n")
        v.session_start(event, ROOT)
        s2 = v.read_json(v.state_path(self.repo, "same"))
        self.assertEqual(s1["baseline"], s2["baseline"])
        self.assertIn("calc.py", v.changed_since(self.repo, s2["baseline"]))

    def test_episodes_stay_bounded(self):
        v.append_episodes(self.repo, "s", [f"FAIL x{i}" for i in range(v.EPISODE_KEEP + 5)], ROOT)
        text = (self.repo / ".agents/EPISODES.md").read_text()
        entries = v.episode_entries(self.repo)
        self.assertEqual(len(entries), v.EPISODE_KEEP)
        self.assertTrue(entries[0].endswith("FAIL x5") and text.startswith("# Episodes"))

    def test_adapter_fails_closed_when_gate_crashes(self):
        self.make_python_project()
        self.write("tmp", "a file where the runtime dir must go\n")
        self.write("calc.py", "def add(a,b): return a+b\n")

        def hook(event: dict) -> str:
            event = {"cwd": str(self.repo), "session_id": "crash", **event}
            return subprocess.run([PY, str(ROOT / "hooks/lifecycle.py")], input=json.dumps(event), text=True, capture_output=True).stdout

        denied = json.loads(hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("could not run", denied["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(json.loads(hook({"hook_event_name": "Stop", "stop_hook_active": False}))["decision"], "block")
        self.assertEqual(hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}), "")


class InitDoctorTests(RepoCase):
    def test_init_scaffolds_layout_preserves_agents_and_is_idempotent(self):
        self.write("AGENTS.md", "# User rules\n\nKeep me.\n")
        self.write(".gitignore", "/tmp/\n")
        self.commit_all()
        first = v.init_project(self.repo, ROOT)
        self.assertEqual(set(first), {".agents/VERIFY.py", ".agents/EPISODES.md", ".agents/MEMORY.md", ".agents/CLI_GIST.md", "Makefile", ".gitignore", "AGENTS.md"})
        self.assertEqual((self.repo / ".gitignore").read_text(), "/tmp/\n/var/log/\n")
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Keep me.", text)
        self.assertIn(v.PROBE_TAG, text)
        self.assertEqual(v.init_project(self.repo, ROOT), [])
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(v.MANAGED_START), 1)

    def test_doctor_repo_enforces_budget_tags_and_layout(self):
        v.init_project(self.repo, ROOT)
        passes = lambda: {c.id: c.passes() for c in v.doctor_repo(self.repo)}
        self.assertTrue(all(passes().values()), v.render_checks(v.doctor_repo(self.repo)))
        memory = self.repo / ".agents/MEMORY.md"
        with memory.open("a") as f:
            f.write("- remember to be careful\n")
        self.assertFalse(passes()["state.MEMORY.md"])
        memory.write_text("- VERIFIED[x] " + "y" * v.STATE_BUDGET["MEMORY.md"] + "\n")
        self.assertFalse(passes()["state.MEMORY.md"])


@unittest.skipUnless(shutil.which("make"), "make not installed")
class MakefileTemplateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        shutil.copy(ROOT / "templates/Makefile", self.dir / "Makefile")

    def tearDown(self):
        sh("make -s stop", self.dir, check=False)
        self.td.cleanup()

    def test_service_lifecycle_reaps_whole_process_group(self):
        start = sh("make -s start RUN='echo hello; sleep 300 & sleep 301'", self.dir)
        self.assertIn("running pid=", start.stdout)
        pgid = int((self.dir / "tmp/app.pid").read_text())
        os.killpg(pgid, 0)  # raises if the group is gone
        self.assertEqual(sh("make -s status", self.dir, check=False).returncode, 0)
        self.assertIn("hello", sh("make -s log N=5", self.dir).stdout)
        sh("make -s stop", self.dir)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)
        status = sh("make -s status", self.dir, check=False)
        self.assertIn("stopped", status.stdout)
        self.assertIn("Error 3", status.stdout)

    def test_crash_on_start_is_reported_and_placeholders_fail_closed(self):
        self.assertIn("dead", sh("make -s start RUN='exit 7'", self.dir, check=False).stdout)
        self.assertIn("dead pid=", sh("make -s status", self.dir, check=False).stdout)
        for verb in ("test", "e2e"):
            p = sh(f"make -s {verb}", self.dir, check=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(f"UNKNOWN[{verb}]", p.stdout)


class PackageTests(unittest.TestCase):
    SKILLS = ("plan", "implement", "review", "finalize")
    # Uppercase tokens that are identifiers or record keys, not Signan operators.
    NAMES = {"VERIFIED", "HYPOTHESIS", "UNKNOWN", "YAGNI", "REPL", "API", "RED", "GREEN", "REFACTOR", "MEMORY",
             "AGENTS", "VERIFY", "EPISODES", "CLI", "GIST", "CHANGELOG", "SIGNAN", "PRIOR_ART", "DECISION", "PLAN",
             "UNCOMMITTED", "REMAINS", "FAIL", "DONE", "FINDING", "LESSON", "BREAKING", "CHANGE", "AGENT_CMD", "CLAUDE_PLUGIN_ROOT",
             "CLAUDE_SESSION_ID"}
    OPERATORS = {"NOT", "AND", "OR", "IF", "THEN", "ELSE", "WHEN", "CAUSES", "SAYS", "BC", "EVERY", "SOME", "ONE", "REQUIRES", "MAY"}

    def skill(self, name: str) -> str:
        return (ROOT / "skills" / name / "SKILL.md").read_text()

    def test_json_manifests(self):
        for rel in ("plugin.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json", "hooks/hooks.json"):
            json.loads((ROOT / rel).read_text())

    def test_skills_are_human_only_and_define_signan(self):
        for name in self.SKILLS:
            text = self.skill(name)
            for token in ("disable-model-invocation: true", "## Signan core", "VERIFIED", "HYPOTHESIS", "UNKNOWN", "IF … THEN … ELSE", "../../references/SIGNAN.md"):
                self.assertIn(token, text, name)

    def test_skills_use_signan_operators_strictly(self):
        for name in self.SKILLS:
            body = self.skill(name).split("## Signan core")[0].split("---", 2)[2]
            body = re.sub(r"(?s)```.*?```|`[^`]*`", "", body)  # prose only; code spans hold identifiers
            words = set(re.findall(r"\b[A-Z][A-Z_]{1,}\b", body))
            self.assertEqual(words - self.OPERATORS - self.NAMES, set(), f"{name}: uppercase non-operators")
            self.assertIsNone(re.search(r"\bWHEN\b", body), f"{name}: WHEN is equivalence, never a conditional")

    def test_skill_prompt_contracts(self):
        plan, implement, review, finalize = (self.skill(n) for n in self.SKILLS)
        for token in ("language stdlib", "native runtime/platform/framework", "current primary docs/source", "Probe unknowns", "`make e2e`"):
            self.assertIn(token, plan)
        for token in ("You are a lazy senior developer", "RED→GREEN→REFACTOR", "root cause", "Evidence loop (priority 1)", v.PROBE_TAG,
                      "NOT mocks", "clean consumer", "`make start|stop|status|log`", "gate --repo . --session ${CLAUDE_SESSION_ID}"):
            self.assertIn(token, implement)
        for token in ("Correctness / contract pass", "Ponytail / maintainability pass", "mocked|stubbed", "publishable artifact", "failing test|probe"):
            self.assertIn(token, review)
        for token in ("Conventional Commits 1.0.0", ".agents/MEMORY.md", ".agents/CLI_GIST.md", ".agents/EPISODES.md", "doctor --repo", "LESSON"):
            self.assertIn(token, finalize)

    def test_signan_reference_defines_every_operator(self):
        text = (ROOT / "references/SIGNAN.md").read_text()
        for token in self.OPERATORS | {"→", "∅", "P=?", "A REQUIRES B"}:
            self.assertIn(token, text)

    def test_skills_stay_lean(self):
        sizes = {n: (ROOT / "skills" / n / "SKILL.md").stat().st_size for n in self.SKILLS}
        for name, size in sizes.items():
            self.assertLess(size, 5500, f"{name} skill grew to {size} bytes; move provenance/examples out of runtime prompt")
        self.assertLess(sum(sizes.values()), 18500, sizes)

    def test_plugin_code_never_contains_the_probe_tag(self):
        for rel in ("scripts/vae_core.py", "scripts/vae.py", "hooks/lifecycle.py", "templates/VERIFY.py", "templates/Makefile", "tests/test_vae.py", "tests/e2e.py"):
            self.assertNotIn(v.PROBE_TAG, (ROOT / rel).read_text(), rel)


if __name__ == "__main__":
    unittest.main()
