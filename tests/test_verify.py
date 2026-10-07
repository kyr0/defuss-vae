"""Verifier: commands, rules, convention checks, coverage parsing, evidence shape."""
from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import unittest
from pathlib import Path

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    PY,
    ROOT,
    TEST_CMD,
    RepoCase,
    sh,
)

# isort: split
from vae_project import init_project
from vae_repo import (
    FALLBACK_BIN,
    find_test_files,
    is_code,
    is_test,
    make_graph,
    make_reach,
    run,
    stacks,
)
from vae_verify import (
    NO_SERVICE_STUB,
    PROBE_TAG,
    STACK_IGNORES,
    check_doc_pages,
    check_gitignore,
    check_layout,
    check_package,
    check_wiring,
    e2e_scope,
    parse_coverage,
    remedy,
    render_report,
    stack_defaults,
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

    def test_e2e_scope_matches_globs_plus_build_files_and_ignores_invalid_config(self):
        paths = ["web/app.ts", "api/main.py", "package.json", "tests/test_api.py"]
        self.assertEqual(e2e_scope({"e2e_paths": ["web/*"]}, paths), ["package.json", "web/app.ts"])
        for bad in ({}, {"e2e_paths": []}, {"e2e_paths": "web/*"}, {"e2e_paths": [1]}):
            self.assertIsNone(e2e_scope(bad, paths), f"{bad} falls back to every code file")

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

    def test_docs_only_change_runs_prose_and_page_rules_not_suites(self):
        rule = ("[{'id':'docs.no-wip','kind':'not_regex','glob':'*.md','docs':True,'pattern':'WIP','claim':'no WIP pages'},"
                " {'id':'code.hdr','kind':'contains','glob':'*','text':'# SPDX','claim':'code rule'}]")
        self.make_python_project(test_command=f"echo run >> runs.txt && {TEST_CMD}", rules=rule)
        self.write("README.md", "Fast \u2014 WIP. Mocks: " + "mock" + ".patch is banned.\n")
        report = verify(self.repo, ["README.md"])
        prose = self.check(report, "prose")
        self.assertFalse(prose.value)
        self.assertIn("README.md:1 T02 em dash", prose.evidence)
        self.assertFalse(self.check(report, "docs.no-wip").value)
        self.assertEqual(self.check(report, "code.hdr").evidence, "glob='*' files=0", "code globs never see pages")
        self.assertNotIn("tests.unit", [c.id for c in report.checks])
        self.assertFalse((self.repo / "runs.txt").exists(), "a page edit must not rerun the suites")
        self.write("README.md", "Fast, really. Mocks: " + "mock" + ".patch is banned.\n")
        self.assertTrue(verify(self.repo, ["README.md"]).verified)

    def test_prose_allow_and_disable_per_page(self):
        self.make_python_project()
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("CONFIG['prose'] = {'allow': {'docs/de/*.md': '\\u201c'}}\n")
        self.write("docs/de/a.md", "Er sagte \u201eja\u201c.\n")
        self.write("docs/b.md", "He said \u201cyes\u201d.\n")
        self.assertTrue(self.check(verify(self.repo, ["docs/de/a.md"]), "prose").value)
        self.assertFalse(self.check(verify(self.repo, ["docs/b.md"]), "prose").value)
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("CONFIG['prose'] = False\n")
        self.assertTrue(verify(self.repo, ["docs/b.md"]).verified)

    def test_prose_cli_fixes_then_reports_only_rewrites(self):
        self.make_python_project()
        self.write("docs/a.md", "\u201cHi\u201d\u2026 now \u2014 later\n")
        p = sh(f"{PY} {ROOT}/scripts/vae.py prose --repo {self.repo} --fix", self.repo, check=False)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertEqual((self.repo / "docs/a.md").read_text(), '"Hi"... now \u2014 later\n')
        self.assertIn("docs/a.md:1 T02 em dash", p.stdout)
        self.write("docs/a.md", '"Hi"... now, later\n')
        self.assertIn("VERIFIED[prose]=true", sh(f"{PY} {ROOT}/scripts/vae.py prose --repo {self.repo}", self.repo).stdout)

    def test_doc_pages_follow_interfaces_and_production_code_only(self):
        self.make_python_project()  # root README.md + ARCH.md cover every folder up to the next package manifest
        files = {
            "src/components/button/x.py": "x = 1\n", "infra/main.tf": "", "tests/test_x.py": "", "examples/demo.py": "",
            "config/app.yaml": "", "data/a.csv": "", ".github/workflows/ci.yml": "", "docs/guide.md": "",
            "tools/cli.sh": "#!/bin/sh\necho hi\n", "tests/e2e.py": "#!/usr/bin/env python3\n",
            "packages/lib/package.json": '{"name": "l", "exports": "./dist/index.js"}', "packages/lib/src/index.ts": "",
            "apps/web/package.json": '{"private": true}', "apps/web/src/main.ts": "",
        }
        for rel, text in files.items():
            self.write(rel, text)
        for rel in ("tools/cli.sh", "tests/e2e.py"):
            os.chmod(self.repo / rel, 0o755)
        c = check_doc_pages(self.repo, list(files), {})
        self.assertEqual(c.evidence, "missing=['packages/lib/README.md', 'apps/web/ARCH.md', 'packages/lib/ARCH.md']",
                         "a package restarts coverage; everything else is covered by the root pages")
        self.assertFalse(c.required, "without CONFIG['strict'] a gap warns")
        self.assertTrue(check_doc_pages(self.repo, list(files), {"strict": True}).required)
        self.write("packages/lib/src/README.md", "x")
        self.assertIn("packages/lib/README.md", check_doc_pages(self.repo, list(files), {}).evidence, "a page below the boundary does not cover it")
        (self.repo / "ARCH.md").unlink()
        self.assertIn("'ARCH.md'", check_doc_pages(self.repo, ["src/components/button/x.py"], {}).evidence, "missing pages are named at the boundary")
        cfg = {"readme": {"exclude": ["packages/*"]}, "arch": {"exclude": ["apps/*", "packages/*"]}}
        self.assertTrue(check_doc_pages(self.repo, ["packages/lib/package.json", "packages/lib/src/index.ts", "apps/web/src/main.ts"], cfg).value)
        self.assertTrue(check_doc_pages(self.repo, ["gone/old.py"], {}).value, "a deleted folder needs no page")
        (self.repo / "README.md").unlink()
        self.assertEqual(check_doc_pages(self.repo, ["calc.py"], {"readme": False, "arch": False}).evidence, "missing=['README.md']", "the root always")

    def test_package_json_defaults_for_new_packages_and_metadata_for_existing_ones(self):
        self.make_python_project()
        gaps = lambda rel="package.json", cfg={}: check_package(self.repo, [rel], cfg).evidence
        self.write("package.json", '{"name": "x", "exports": "./dist/index.js", "scripts": {"lint": "oxlint ."}}')
        for gap in ("description", "license", "author", "packageManager (bun@<version>)", "type='module'",
                    "devDependency oxlint", "oxlint script without --deny-warnings", "library build via pkgroll"):
            self.assertIn(gap, gaps())
        tpl = (ROOT / "templates/package.json.tmpl").read_text()
        self.write("package.json", tpl)
        self.assertIn("template placeholder TODO(...) left", gaps())
        self.write("package.json", tpl.replace("TODO(", "filled ("))
        self.assertTrue(check_package(self.repo, ["package.json"], {}).value, gaps())
        # An existing CJS package on npm keeps its module type, build and linter; only metadata is required.
        self.write("legacy/package.json", '{"name": "l", "main": "index.js", "packageManager": "npm@10.9.0"}')
        self.write("legacy/package-lock.json", "{}")
        self.commit_all("legacy")
        self.assertEqual(gaps("legacy/package.json"), "gaps=['legacy/package.json: description', 'legacy/package.json: license', 'legacy/package.json: author']")
        self.assertTrue(check_package(self.repo, ["legacy/package.json"], {"package": False}).value)
        self.write("legacy/package.json", '{"name": "l", "description": "d", "license": "MIT", "author": "a"}')
        self.commit_all("legacy-meta")
        self.assertTrue(check_package(self.repo, ["legacy/package.json"], {"package": {"manager": None}}).value)
        self.assertIn("packageManager (npm@<version>)", gaps("legacy/package.json"), "an npm package is asked for npm, not a migration")
        (self.repo / "legacy/package.json").unlink()
        self.assertTrue(check_package(self.repo, ["legacy/package.json"], {}).value, "a deleted manifest is not 'unreadable'")
        self.assertEqual(check_package(self.repo, ["src/main/java/calc/App.java"], {}).evidence, "∅ changed package.json")

    def test_template_mock_rule_catches_mocks_but_not_itself(self):
        namespace = runpy.run_path(str(ROOT / "templates/VERIFY.py"))
        rule = namespace["RULES"][0]
        rx = re.compile(rule["pattern"])
        self.assertIsNone(rx.search((ROOT / "templates/VERIFY.py").read_text()))
        for bad in ("from unittest import " + "mock", "m = Magic" + "Mock()", "jest" + ".mock('./db')", "vi" + ".fn()", "mock" + ".patch('x')", "@Mock\n  Foo foo;",
                    "new " + "Mock<IRepo>()", "Substitute" + ".For<IRepo>()", "A" + ".Fake<IRepo>()", "use mock" + "all::automock;", "@Mock" + "Bean Repo r;"):
            self.assertIsNotNone(rx.search(bad), bad)
        for fine in ("def mockup(): pass", "# tests avoid mocks", "monkeypatch.setenv('A', '1')"):
            self.assertIsNone(rx.search(fine), fine)
        self.make_python_project(rules=repr(namespace["RULES"]))
        self.write("test_db.py", "from unittest import " + "mock\n")
        self.assertFalse(self.check(verify(self.repo, ["test_db.py"]), "tests.no-mocks").value)

    def test_help_annotations_are_not_make_calls(self):
        self.write("Makefile", "ci: ; @echo hi ## run make deploy first\nverify: ci ## make lint test\ndeploy:\n\t@echo d\n")
        graph = make_graph(self.repo)
        self.assertEqual((graph["ci"], graph["verify"]), (set(), {"ci"}))

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
        self.write(".gitignore", "")
        # Java, C# and Go's LookupEnv reads count too (assembled from parts for the same reason as above).
        self.write("jvm/App.java", 'class App { String u = System.getenv' + '("DB_URL"); }\n')
        self.write("net/App.cs", 'var k = Environment.GetEnvironmentVariable' + '("API_TOKEN");\n')
        self.write("go/main.go", 'v, ok := os.LookupEnv' + '("GO_FLAG")\n')
        c = self.check(verify(self.repo, ["jvm/App.java", "net/App.cs", "go/main.go"]), "env.example")
        for key in ("DB_URL", "API_TOKEN", "GO_FLAG"):
            self.assertIn(key, c.evidence)

    def test_gate_commands_find_freshly_installed_toolchains(self):
        # Appended, not prepended: a tool already on PATH keeps precedence over the installers' fallback dirs.
        entries = run("echo $PATH", self.repo).stdout.strip().split(os.pathsep)
        self.assertEqual(entries[-len(FALLBACK_BIN):], [str(Path.home() / d) for d in FALLBACK_BIN])
        self.assertIn(str(Path.home() / ".local/share/mise/shims"), entries, "mise-pinned tools resolve in gate commands")
        self.assertEqual(entries[: len(os.environ["PATH"].split(os.pathsep))], os.environ["PATH"].split(os.pathsep))

    def test_missing_toolchain_points_at_make_setup(self):
        # Real outputs: GNU make on macOS (probed), dash and bash forms.
        for output, tool in (("uv run pytest\nmake: uv: No such file or directory\nmake: *** [test] Error 1", "uv"),
                             ("sh: 1: bun: not found", "bun"), ("bash: uv: command not found", "uv")):
            self.assertIn(f"make setup  (installs the missing `{tool}`", remedy("make test", output, "var/log/vae/x.log"))
        self.assertEqual(remedy("make test", "FAILED (failures=1)", "var/log/vae/x.log"), "RUN: make test  (full output: tail -n 80 var/log/vae/x.log)")
        # Other toolchains arrive through mise: the remedy names the pin instead of promising an installer.
        self.assertIn("`mise use go@<version>`", remedy("make test", "make: go: No such file or directory", "x.log"))
        self.assertIn("`mise use rust@<version>`", remedy("make test", "sh: cargo: command not found", "x.log"))

    def test_coverage_parser_reads_real_tool_output(self):
        # Formats VERIFIED by real runs: bun 1.3.11, pytest-cov under uv 0.11, c8 10 (istanbul text), go 1.26 `cover -func`.
        # The istanbul sample keeps distinct per-column values so picking % Lines (not % Stmts) is actually tested.
        bun = ("-----------|---------|---------|-------------------\nFile       | % Funcs | % Lines | Uncovered Line #s\n"
               "All files  |   50.00 |  100.00 |\n math.ts   |   50.00 |  100.00 | \n")
        pytest_cov = "Name                   Stmts   Miss  Cover\nsrc/calc/__init__.py       2      0   100%\nTOTAL                      2      0   100%\n"
        istanbul = "File      | % Stmts | % Branch | % Funcs | % Lines | Uncovered Line #s\nAll files |   85.71 |       50 |     100 |   80.12 |\n"
        go = "calc/add.go:3:\tAdd\t\t100.0%\ntotal:\t\t\t(statements)\t75.0%\n"
        # coverlet.msbuild (format VERIFIED with dotnet 9 + xUnit): % Line is the first column of the Total row.
        coverlet = ("| Module | Line   | Branch | Method |\n| Calc   | 71.74% | 69.83% | 61.9%  |\n"
                    "|         | Line   | Branch | Method |\n| Total   | 71.74% | 69.83% | 61.9%  |\n| Average | 71.74% | 69.83% | 61.9%  |\n")
        cobertura = "TOTAL 85.5%\n"  # STACKS.md one-liners print this from Cobertura `line-rate` or JaCoCo CSV
        for output, pct in ((bun, 100.0), (pytest_cov, 100.0), (istanbul, 80.12), (go, 75.0), (coverlet, 71.74), (cobertura, 85.5)):
            self.assertEqual(parse_coverage(output)[0], pct, output)
        self.assertIsNone(parse_coverage("1 pass\nRan 1 test\n")[0])

    def test_test_files_follow_each_ecosystems_convention(self):
        tests = ("calc_test.go", "tests/test_calc.py", "app/tests.py", "src/calc.test.ts", "src/test/java/a/CalcTest.java",
                 "Calc.Tests/MathTests.cs", "Calc.Tests/Program.cs", "src/commonTest/kotlin/Foo.kt", "app/src/FooTest.kt",
                 "src/FooSpec.scala", "src/FooIT.java", "tests/integration.rs", "e2e-tests/run.sh")
        production = ("src/latest.py", "src/Latest.java", "src/Contest.cs", "src/manifest.rs", "src/Limit.kt", "contest/main.go",
                      "src/testing.py", "spectrum/a.py")
        self.assertEqual([p for p in tests if not is_test(p)], [])
        self.assertEqual([p for p in production if is_test(p)], [])

    def test_rust_inline_tests_count_as_tests(self):
        # `cargo new --lib` (VERIFIED, cargo 1.9x) writes only an inline `#[cfg(test)] mod tests` into src/lib.rs.
        self.write("Cargo.toml", '[package]\nname = "calc"\nversion = "0.1.0"\nedition = "2024"\n')
        self.write("src/lib.rs", "pub fn add(a: u64, b: u64) -> u64 { a + b }\n")
        self.assertEqual(find_test_files(self.repo), [])
        self.write("src/lib.rs", "pub fn add(a: u64, b: u64) -> u64 { a + b }\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn adds() {}\n}\n")
        self.assertEqual(find_test_files(self.repo), ["src/lib.rs"])
        self.write("tests/api.rs", "#[test]\nfn api() {}\n")
        self.assertEqual(find_test_files(self.repo), ["tests/api.rs"], "named test files win; sources are read only without any")

    def test_build_files_of_every_stack_reopen_the_gate(self):
        for path in ("App/App.csproj", "App.sln", "App.slnx", "Directory.Build.props", "Directory.Packages.props", "gradle.properties",
                     "gradlew", "mvnw", "go.work", ".tool-versions", "rust-toolchain", "mise.toml", "global.json", "web/Page.razor"):
            self.assertTrue(is_code(path), path)
        self.assertFalse(is_code("App/obj/project.assets.json"), "MSBuild output is not code")

    def test_each_stack_has_ignores_and_documented_verbs(self):
        found = stacks(["go.mod", "svc/Cargo.toml", "jvm/build.gradle.kts", "api/Api.csproj", "ui/package.json", "ml/train.py", "ui/App.tsx"])
        self.assertEqual(found, {"go", "rust", "jvm", "dotnet", "js", "python", "web"})
        self.assertEqual(set(STACK_IGNORES), found, "every detected stack declares its ignores")
        docs = stack_defaults(found)
        for stack in found - {"web"}:
            verbs = {ln.split(":", 1)[0][2:] for ln in docs.get(stack, [])}
            self.assertLessEqual({"lint", "test", "coverage", "e2e", "pin"}, verbs, stack)
        self.assertIn("real Playwright browser", " ".join(docs["web"]))
        # Probed with Gradle 9.8: its JaCoCo report writes only HTML until the CSV is enabled, and tests do not start
        # without the launcher; Maven's report goal writes the CSV under target/site/jacoco/.
        for token in ("csv.required = true", "junit-platform-launcher", "target/site/jacoco/jacoco.csv"):
            self.assertIn(token, " ".join(docs["jvm"]))
        self.write(".gitignore", "var/*\ntmp/*\n.env\noutput/*\ndist/\n")
        self.write("Cargo.toml", "")
        self.write("api/Api.csproj", "<Project/>")
        self.write("build.gradle.kts", "")
        gaps = check_gitignore(self.repo, {}).evidence
        for line in ("target/", "bin/", "obj/", "build/", ".gradle/"):
            self.assertIn(line, gaps)

    def test_missing_verbs_cite_the_detected_stacks_defaults(self):
        self.write("go.mod", "module example.com/calc\n\ngo 1.26\n")
        self.write("calc.go", "package calc\n\nfunc Add(a, b int) int { return a + b }\n")
        self.write(".agents/VERIFY.py", "CONFIG = {'layout': False}\nRULES = []\n")
        self.commit_all()
        report = verify(self.repo, ["calc.go"])
        self.assertIn("go: `golangci-lint run`", self.check(report, "lint").next)
        self.assertIn("go: `go test -race ./...`", self.check(report, "tests.unit").next)
        self.assertIn("go tool cover -func", self.check(report, "coverage").next)
        self.assertNotIn("ruff", self.check(report, "lint").next, "a Go repo gets Go hints, not the bun|uv fallback")

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
        self.write(".gitignore", "var/*\ntmp/*\n.env\noutput/*\ndist/\n")
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

    def test_required_ignores_follow_the_toolchains_present(self):
        self.write(".gitignore", "var/*\ntmp/*\n.env\noutput/*\n")
        self.assertEqual(check_gitignore(self.repo, {}).evidence, "gaps=dist/")
        self.write("app/package.json", "{}")
        self.write("tool.py", "")
        c = check_gitignore(self.repo, {})
        for line in ("node_modules/", "coverage/", ".cache/", ".venv/", "__pycache__/", "*.pyc", ".pytest_cache/", ".ruff_cache/", ".coverage"):
            self.assertIn(line, c.evidence)
        self.assertTrue(c.passes() and not c.value, "without CONFIG['strict'] a gap warns but does not block")
        self.assertFalse(check_gitignore(self.repo, {"strict": True}).passes())
        self.assertIn("CONFIG['gitignore_exempt']", c.next)
        self.write(".gitignore", "var/*\ntmp/*\n.env\noutput/*\nnode_modules/\ncoverage/\n.cache/\n.venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\n.coverage\n")
        self.assertTrue(check_gitignore(self.repo, {"gitignore_exempt": ["dist/"]}).value, "a GitHub Action may commit dist/")
        self.assertTrue(check_layout(self.repo).evidence.startswith("gaps=Makefile:"), "layout keeps its own required ignores only")

    def test_defuss_vae_policy_file_does_not_make_a_repo_python(self):
        self.write(".gitignore", "var/*\ntmp/*\n.env\noutput/*\ndist/\nnode_modules/\ncoverage/\n.cache/\n")
        self.write("package.json", "{}")
        self.write(".agents/VERIFY.py", "CONFIG = {}\n")
        self.write(".github/scripts/release.py", "")
        self.assertTrue(check_gitignore(self.repo, {}).value, check_gitignore(self.repo, {}).evidence)

    def test_warnings_are_listed_but_do_not_block(self):
        self.make_python_project()
        report = verify(self.repo, ["calc.py"])
        self.assertTrue(report.verified, render_report(report))
        self.assertIn("WARNS: gitignore", render_report(report))

    def test_custom_rule_kinds_pass_fail_or_report_misconfiguration(self):
        rules = [
            {"id": "cmd.ok", "kind": "command", "command": "true"},
            {"id": "cmd.fail", "kind": "command", "command": "exit 3"},
            {"id": "cmd.missing", "kind": "command"},
            {"id": "file.ok", "kind": "file_exists", "path": "calc.py"},
            {"id": "file.missing", "kind": "file_exists", "path": "nope.txt"},
            {"id": "kind.bad", "kind": "telepathy"},
            {"id": "rx.bad", "kind": "regex", "path": "calc.py", "pattern": "("},
            {"id": "contains.gone", "kind": "contains", "path": "gone.py", "text": "x"},
            "not a dict",
        ]
        self.make_python_project(rules=repr(rules), config=", 'integration_commands': ['true'], 'coverage_command': 'exit 4'")
        report = verify(self.repo, ["calc.py"])
        got = {c.id: (c.status, c.value) for c in report.checks}
        expected = {"cmd.ok": ("VERIFIED", True), "cmd.fail": ("VERIFIED", False), "cmd.missing": ("UNKNOWN", None),
                    "file.ok": ("VERIFIED", True), "file.missing": ("VERIFIED", False), "kind.bad": ("UNKNOWN", None),
                    "rx.bad": ("UNKNOWN", None), "contains.gone": ("VERIFIED", False), "custom.invalid": ("UNKNOWN", None),
                    "tests.integration.1": ("VERIFIED", True), "coverage": ("VERIFIED", False)}
        self.assertEqual({k: got[k] for k in expected}, expected)
        self.assertIn("exit=4", self.check(report, "coverage").evidence, "a failing coverage command is a failure, not UNKNOWN")
        self.assertFalse(report.verified, "a failing or misconfigured rule fails closed")

    def test_a_broken_verify_py_fails_closed(self):
        self.make_python_project()
        for source in ("CONFIG = []\nRULES = []\n", "raise RuntimeError('boom')\n"):
            self.write(".agents/VERIFY.py", source)
            report = verify(self.repo, ["calc.py"])
            self.assertFalse(report.verified, source)
            self.assertNotEqual(self.check(report, "verifier.config").value, True, source)

    def test_cli_verify_and_prose_report_exit_codes(self):
        self.make_python_project()
        cli = [PY, str(ROOT / "scripts/vae.py")]
        ok = subprocess.run([*cli, "verify", "--repo", str(self.repo), "--json"], text=True, capture_output=True, check=False)
        data = json.loads(ok.stdout)
        self.assertEqual((ok.returncode, data["status"], data["value"]), (0, "VERIFIED", True))
        self.write(".agents/VERIFY.py", "CONFIG = {'prose': {'phrases': ['(']}}\nRULES = []\n")
        bad = subprocess.run([*cli, "prose", "--repo", str(self.repo), "README.md"], text=True, capture_output=True, check=False)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("UNKNOWN[prose] BC invalid CONFIG['prose']['phrases'] regex", bad.stdout)
        self.assertEqual(self.check(verify(self.repo, ["README.md"]), "prose").status, "UNKNOWN", "the gate fails closed too")
        (self.repo / ".agents/VERIFY.py").unlink()
        missing = subprocess.run([*cli, "verify", "--repo", str(self.repo)], text=True, capture_output=True, check=False)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("UNKNOWN[verifier.overall]=?", missing.stdout, "no policy file: verification cannot conclude")

    def test_new_projects_start_strict_and_old_configs_keep_warnings(self):
        # Option B (0.6.0): the template blocks from day one; a config without the key, like every 0.5.x project's
        # explicit False, keeps warnings until the project opts in.
        init_project(self.repo, ROOT)
        config = runpy.run_path(str(self.repo / ".agents/VERIFY.py"))["CONFIG"]
        self.assertIs(config["strict"], True)
        self.write("package.json", "{}")
        self.assertTrue(check_gitignore(self.repo, config).required, "a new project blocks on its gaps")
        self.assertFalse(check_gitignore(self.repo, {}).required, "no key: a warning")
        self.assertFalse(check_gitignore(self.repo, {"strict": False}).required, "explicit False: a warning")

    def test_existing_npm_project_with_an_untracked_lockfile_is_not_a_new_toolchain(self):
        self.make_python_project()
        self.write("web/package.json", '{"name": "w"}')
        self.commit_all("web")
        self.write("web/package-lock.json", "{}")
        self.write("new/package.json", '{"name": "n"}')
        self.write("new/package-lock.json", "{}")
        c = self.check(verify(self.repo, ["web/package-lock.json", "new/package.json", "new/package-lock.json"]), "toolchain")
        self.assertEqual(c.evidence, "newly introduced foreign lockfiles=['new/package-lock.json(npm)']",
                         "a lockfile beside a tracked manifest is an existing project, so the agent never has to commit it first")
        self.assertIn("yourself", c.next)
        # A bun project stays one: a tracked bun.lock beside the manifest makes a new npm lockfile a toolchain switch.
        self.write("bunapp/package.json", '{"name": "b"}')
        self.write("bunapp/bun.lock", "{}")
        self.commit_all("bunapp")
        self.write("bunapp/package-lock.json", "{}")
        self.assertIn("bunapp/package-lock.json(npm)", self.check(verify(self.repo, ["bunapp/package-lock.json"]), "toolchain").evidence)

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
