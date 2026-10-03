"""Verifier: commands, rules, convention checks, coverage parsing, evidence shape."""
from __future__ import annotations

import json
import os
import re
import runpy
import unittest
from pathlib import Path

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    ROOT,
    TEST_CMD,
    RepoCase,
    sh,
)

# isort: split
from vae_project import init_project
from vae_repo import is_code, make_graph, make_reach, run
from vae_verify import (
    NO_SERVICE_STUB,
    PROBE_TAG,
    check_layout,
    check_wiring,
    parse_coverage,
    remedy,
    render_report,
    verify,
)


class VerifyTests(RepoCase):
    def test_passes_with_compact_evidence_and_self_ignored_log(self):
        self.make_python_project()
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        report = verify(self.repo, ["calc.py"])
        self.assertTrue(report.verified, render_report(report))
        unit = self.check(report, "tests.unit")
        self.assertTrue(unit.evidence.startswith("exit=0; ") and len(unit.evidence) < 250, unit.evidence)
        self.assertEqual(self.check(report, "coverage").metric, 75.0)
        self.assertIn("Ran 1 test", (self.repo / "var/log/vae/tests.unit.log").read_text())
        self.assertEqual(self.check(report, "tests.e2e.evidence").evidence, "changed=['output/sum.txt']")
        self.assertNotIn("var/", sh("git status --porcelain", self.repo).stdout)

    def test_failing_test_reports_tail_and_log_path(self):
        self.make_python_project()
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        report = verify(self.repo, ["calc.py"])
        unit = self.check(report, "tests.unit")
        self.assertFalse(unit.value)
        self.assertIn("log=var/log/vae/tests.unit.log", unit.evidence)
        self.assertIn("AssertionError", unit.evidence)
        self.assertIn("tail -n 80 var/log/vae/tests.unit.log", unit.next)

    def test_timeout_with_partial_output_fails_closed_instead_of_crashing(self):
        # Regression: TimeoutExpired.stdout is bytes even with text=True; str + bytes crashed the Stop hook (fail-open).
        self.make_python_project(test_command="echo partial; sleep 5")
        self.write(".agents/VERIFY.py", self.repo.joinpath(".agents/VERIFY.py").read_text().replace("'timeout_s':60", "'timeout_s':1"))
        unit = self.check(verify(self.repo, ["calc.py"]), "tests.unit")
        self.assertFalse(unit.value)
        self.assertIn("exit=124", unit.evidence)
        self.assertIn("partial", unit.evidence)
        self.assertIn("TIMEOUT>1s", unit.evidence)

    def test_coverage_without_metric_fails_closed(self):
        self.make_python_project()
        self.write(".agents/VERIFY.py", f"CONFIG={{'test_command':{TEST_CMD!r},'coverage_command':'echo no metric here','layout':False}}\nRULES=[]\n")
        report = verify(self.repo, ["calc.py"])
        self.assertFalse(report.verified)
        self.assertEqual(self.check(report, "coverage").status, "UNKNOWN")

    def test_coverage_below_minimum_fails(self):
        self.make_python_project(coverage="50")
        report = verify(self.repo, ["calc.py"])
        c = self.check(report, "coverage")
        self.assertEqual((c.status, c.value), ("VERIFIED", False))

    def test_custom_exact_rule(self):
        self.make_python_project(rules="[{'id':'marker','kind':'contains','path':'calc.py','text':'return a + b','claim':'canonical add'}]")
        report = verify(self.repo, ["calc.py"])
        self.assertTrue(report.verified, render_report(report))
        self.assertTrue(self.check(report, "marker").value)

    def test_leftover_probe_fails_until_removed(self):
        self.make_python_project()
        self.write("calc.py", f"def add(a, b):\n    print(a, b)  # {PROBE_TAG}\n    return a + b\n")
        c = self.check(verify(self.repo, ["calc.py"]), "hygiene.probes")
        self.assertFalse(c.value)
        self.assertIn("calc.py:2", c.evidence)
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        self.assertTrue(self.check(verify(self.repo, ["calc.py"]), "hygiene.probes").value)

    def test_glob_rule_only_sees_changed_files(self):
        rule = "[{'id':'hdr','kind':'contains','glob':'*.py','text':'# SPDX','claim':'license header'}]"
        self.make_python_project(rules=rule)
        self.write("new.py", "x = 1\n")
        c = self.check(verify(self.repo, ["new.py"]), "hdr")
        self.assertEqual(c.evidence, "glob='*.py' files=1; hits=['new.py']")
        self.write("new.py", "# SPDX\nx = 1\n")
        self.assertTrue(self.check(verify(self.repo, ["new.py"]), "hdr").value)

    def test_template_mock_rule_catches_mocks_but_not_itself(self):
        namespace = runpy.run_path(str(ROOT / "templates/VERIFY.py"))
        rule = namespace["RULES"][0]
        rx = re.compile(rule["pattern"])
        self.assertIsNone(rx.search((ROOT / "templates/VERIFY.py").read_text()))
        for bad in ("from unittest import " + "mock", "m = Magic" + "Mock()", "jest" + ".mock('./db')", "vi" + ".fn()", "mock" + ".patch('x')", "@Mock\n  Foo foo;"):
            self.assertIsNotNone(rx.search(bad), bad)
        for fine in ("def mockup(): pass", "# tests avoid mocks", "monkeypatch.setenv('A', '1')"):
            self.assertIsNone(rx.search(fine), fine)
        self.make_python_project(rules=repr(namespace["RULES"]))
        self.write("test_db.py", "from unittest import " + "mock\n")
        self.assertFalse(self.check(verify(self.repo, ["test_db.py"]), "tests.no-mocks").value)

    def test_makefile_is_the_only_command_source(self):
        # Ecosystem manifests alone must not be guessed into commands: without Makefile verbs the gate fails closed.
        self.write("package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.write("pyproject.toml", "[project]\nname = 'x'\n")
        self.write("test_x.py", "def test_x(): pass\n")
        report = verify(self.repo, ["test_x.py"])
        unit, cov = self.check(report, "tests.unit"), self.check(report, "coverage")
        self.assertEqual((unit.status, cov.status), ("UNKNOWN", "UNKNOWN"))
        self.assertIn("Makefile target `test`", unit.next)
        # Missing lint|e2e verbs and a missing VERIFY.py fail closed instead of being skipped.
        self.assertEqual((self.check(report, "lint").status, self.check(report, "tests.e2e").status), ("UNKNOWN", "UNKNOWN"))
        verifier = self.check(report, "verifier.config")
        self.assertEqual((verifier.value, verifier.evidence), (False, "missing"))
        self.assertIn("scripts/vae.py init --repo", verifier.next)
        self.write("Makefile", "lint:\n\t@true\ntest:\n\t@true\ncoverage:\n\t@echo 'TOTAL 80%'\ne2e: dist\n\t@true\ndist:\n\t@true\nX := y\nURL ::= z\n")
        report = verify(self.repo, ["test_x.py"])
        self.assertEqual((self.check(report, "lint").command, self.check(report, "tests.unit").command, self.check(report, "tests.e2e.1").command),
                         ("make lint", "make test", "make e2e"))
        self.assertEqual(self.check(report, "coverage").metric, 80.0)
        self.assertEqual(set(make_graph(self.repo)), {"lint", "test", "coverage", "e2e", "dist"})
        # A passing e2e that leaves no consumer output (`@true`) is not evidence.
        evidence = self.check(report, "tests.e2e.evidence")
        self.assertEqual((evidence.value, evidence.evidence), (False, "output/ unchanged by e2e"))
        self.write("Makefile", "lint:\n\t@echo 'x.py:1:1: F401 unused import'; exit 1\n")
        lint = self.check(verify(self.repo, ["test_x.py"]), "lint")
        self.assertFalse(lint.value)
        self.assertIn("F401", lint.evidence)

    def test_new_projects_must_start_on_bun_or_uv(self):
        self.make_python_project()
        self.write("yarn.lock", "# existing toolchain\n")
        self.commit_all("existing-yarn")
        self.write("yarn.lock", "# existing toolchain, updated\n")
        self.write("web/package-lock.json", "{}\n")
        self.write("tool/requirements.txt", "requests\n")
        c = self.check(verify(self.repo, ["yarn.lock", "web/package-lock.json", "tool/requirements.txt"]), "toolchain")
        self.assertFalse(c.value)
        self.assertIn("web/package-lock.json(npm)", c.evidence)
        self.assertIn("tool/requirements.txt(pip)", c.evidence)
        self.assertNotIn("yarn.lock", c.evidence)  # tracked at HEAD → existing toolchain is kept
        (self.repo / "web/package-lock.json").unlink()
        self.write("uv.lock", "version = 1\n")  # `uv export` may write requirements.txt in a uv project
        self.assertTrue(self.check(verify(self.repo, ["tool/requirements.txt", "uv.lock"]), "toolchain").value)
        self.assertTrue(is_code("poetry.lock") and is_code("tool/requirements.txt"), "lockfile changes must reopen the gate")

    def test_env_example_must_declare_every_env_var(self):
        self.make_python_project()
        # Reads are assembled from parts so this test file never matches the env-read pattern itself.
        self.write("app/config.py", "import os\nURL = os.environ" + ".get('API_URL')\nKEY = os.getenv" + "('API_KEY')\nH = os.environ" + "['HOME']\n")
        self.write(".env", "API_URL=http://localhost\nLOCAL_ONLY=1\n")
        c = self.check(verify(self.repo, ["app/config.py"]), "env.example")
        self.assertFalse(c.value)
        self.assertIn(".env.example:API_KEY,API_URL,LOCAL_ONLY", c.evidence)  # HOME is OS-provided, exempt
        self.write(".env.example", "API_URL=\nAPI_KEY=\nLOCAL_ONLY=\n")
        self.assertTrue(self.check(verify(self.repo, ["app/config.py"]), "env.example").value)
        self.write("svc/.env.example", "SVC_PORT=8080\n")
        self.write("svc/main.ts", "const port = Bun.env" + ".SVC_PORT\nconst t = process.env" + ".SVC_TOKEN\n")
        c = self.check(verify(self.repo, ["svc/main.ts"]), "env.example")
        self.assertIn("svc/.env.example:SVC_TOKEN", c.evidence)  # nearest example, not the root one
        self.write(".gitignore", ".env*\n")
        self.assertIn(".env.example is gitignored", self.check(verify(self.repo, ["svc/main.ts"]), "env.example").evidence)

    def test_gate_commands_find_freshly_installed_toolchains(self):
        # Appended, not prepended: a tool already on PATH keeps precedence over the installers' fallback dirs.
        entries = run("echo $PATH", self.repo).stdout.strip().split(os.pathsep)
        self.assertEqual(entries[-2:], [str(Path.home() / ".local/bin"), str(Path.home() / ".bun/bin")])
        self.assertEqual(entries[: len(os.environ["PATH"].split(os.pathsep))], os.environ["PATH"].split(os.pathsep))

    def test_missing_toolchain_points_at_make_setup(self):
        # Real outputs: GNU make on macOS (probed), dash and bash forms.
        for output, tool in (("uv run pytest\nmake: uv: No such file or directory\nmake: *** [test] Error 1", "uv"),
                             ("sh: 1: bun: not found", "bun"), ("bash: uv: command not found", "uv")):
            self.assertIn(f"make setup  (installs the missing `{tool}`", remedy("make test", output, "var/log/vae/x.log"))
        self.assertEqual(remedy("make test", "FAILED (failures=1)", "var/log/vae/x.log"), "RUN: make test  (full output: tail -n 80 var/log/vae/x.log)")

    def test_coverage_parser_reads_real_tool_output(self):
        # Formats VERIFIED by real runs: bun 1.3.11, pytest-cov under uv 0.11, c8 10 (istanbul text), go 1.26 `cover -func`.
        # The istanbul sample keeps distinct per-column values so picking % Lines (not % Stmts) is actually tested.
        bun = ("-----------|---------|---------|-------------------\nFile       | % Funcs | % Lines | Uncovered Line #s\n"
               "All files  |   50.00 |  100.00 |\n math.ts   |   50.00 |  100.00 | \n")
        pytest_cov = "Name                   Stmts   Miss  Cover\nsrc/calc/__init__.py       2      0   100%\nTOTAL                      2      0   100%\n"
        istanbul = "File      | % Stmts | % Branch | % Funcs | % Lines | Uncovered Line #s\nAll files |   85.71 |       50 |     100 |   80.12 |\n"
        go = "calc/add.go:3:\tAdd\t\t100.0%\ntotal:\t\t\t(statements)\t75.0%\n"
        for output, pct in ((bun, 100.0), (pytest_cov, 100.0), (istanbul, 80.12), (go, 75.0)):
            self.assertEqual(parse_coverage(output)[0], pct, output)
        self.assertIsNone(parse_coverage("1 pass\nRan 1 test\n")[0])

    def test_verify_target_must_run_every_gate_verb_even_without_layout(self):
        # CI runs only `make verify`; the gate runs the verbs one by one, so a hollow `verify` would pass locally only.
        verbs = "lint test coverage e2e:\n\t@true\n"
        self.write("Makefile", verbs + "verify: lint test coverage\n\tcd sub && $(MAKE) -C sub e2e\n")
        self.write(".agents/VERIFY.py", "CONFIG={'layout':False}\nRULES=[]\n")
        wiring = self.check(verify(self.repo, []), "wiring")
        self.assertEqual((wiring.value, wiring.evidence), (False, "verify→coverage,lint,test; missing=e2e"))
        self.assertIn("verify: lint test coverage e2e", wiring.next)
        # A verb the gate takes from CONFIG is the project's own CI concern, not verify's.
        self.assertTrue(check_wiring(self.repo, {"e2e_commands": ["true"]}).value)
        # Prerequisites, `$(VAR)` expansion, transitive targets and recursive `$(MAKE)` calls all count.
        self.write("Makefile", verbs + "CHECKS := coverage \\\n  e2e\nverify: ci $(CHECKS)\nci:\n\t@$(MAKE) -s lint test && echo done\n")
        self.assertTrue(check_wiring(self.repo, {}).value, check_wiring(self.repo, {}).evidence)
        self.assertEqual(make_reach(make_graph(self.repo), "verify"), {"ci", "lint", "test", "coverage", "e2e"})

    def test_library_without_service_keeps_layout_with_one_stub_line(self):
        self.write(".gitignore", "var/*\ntmp/*\n.env\n")
        self.write("Makefile", "setup metrics bench lint test coverage e2e:\n\t@true\nverify: lint test coverage e2e\n")
        c = check_layout(self.repo)
        self.assertEqual(c.evidence, "gaps=Makefile:start,Makefile:stop,Makefile:status,Makefile:log")
        stub = NO_SERVICE_STUB.format(verbs="start stop restart status log")
        self.assertIn(f"no service (library): add the Makefile line `{stub}`", c.next)
        self.assertNotIn("copy `", c.next)  # only service verbs are missing
        with (self.repo / "Makefile").open("a") as f:
            f.write(stub + "\n")
        self.assertTrue(check_layout(self.repo).value)
        self.assertEqual(sh("make -s status", self.repo).stdout.strip(), "∅ status: no service")
        # An existing service verb is never redefined by the suggested line.
        self.write("Makefile", "setup metrics bench lint test coverage e2e verify log:\n\t@true\n")
        self.assertIn("`start stop restart status: ;", check_layout(self.repo).next)

    def test_layout_gaps_then_init_closes_them(self):
        self.make_python_project(layout=True)
        c = self.check(verify(self.repo, ["calc.py"]), "layout")
        self.assertFalse(c.value)
        self.assertIn("Makefile:start", c.evidence)
        self.assertIn("gitignore:var/*", c.evidence)
        self.assertIn("scripts/vae.py init --repo", c.next)
        init_project(self.repo, ROOT)
        self.assertTrue(check_layout(self.repo).value, check_layout(self.repo).evidence)


if __name__ == "__main__":
    unittest.main()
