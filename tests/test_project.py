"""Scaffolding (init, doctor) and the real Makefile template run with real processes."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from vae_testkit import ROOT, RepoCase, sh  # first: puts plugin/scripts on sys.path

# isort: split
from vae_hooks import STATE_BUDGET
from vae_project import MANAGED_START, doctor_repo, init_project
from vae_verify import PROBE_TAG, render_checks


class InitDoctorTests(RepoCase):
    def test_init_scaffolds_layout_preserves_agents_and_is_idempotent(self):
        self.write("AGENTS.md", "# User rules\n\nKeep me.\n")
        self.write(".gitignore", "/tmp/\n")
        self.commit_all()
        first = init_project(self.repo, ROOT)
        self.assertEqual(set(first), {".agents/VERIFY.py", ".agents/EPISODES.md", ".agents/MEMORY.md", ".agents/CLI_GIST.md", "Makefile", ".gitignore", "AGENTS.md"})
        # `/tmp/` already covers tmp/*, so only the missing defaults are appended, in order.
        self.assertEqual((self.repo / ".gitignore").read_text(), "/tmp/\n.env\n.venv/\n__pycache__/\n.pytest_cache/\n.ruff_cache/\nnode_modules/\n*.pyc\nvar/*\noutput/*\ninput/*\n.DS_Store\n")
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Keep me.", text)
        self.assertIn(PROBE_TAG, text)
        self.assertEqual(init_project(self.repo, ROOT), [])
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(MANAGED_START), 1)

    def test_ci_scaffold_needs_a_github_remote_and_never_duplicates(self):
        self.assertNotIn(".github/workflows/verify.yml", init_project(self.repo, ROOT))  # no remote: CI could not run
        sh("git remote add origin git@github.com:example/app.git", self.repo)
        self.assertIn(".github/workflows/verify.yml", init_project(self.repo, ROOT))
        wf = (self.repo / ".github/workflows/verify.yml").read_text()
        self.assertIn("make setup", wf)
        self.assertIn("make verify", wf)
        self.assertEqual(init_project(self.repo, ROOT), [])  # idempotent
        other = self.repo.parent / "other"
        other.mkdir()
        sh("git init -q && git remote add origin https://github.com/example/b.git", other)
        (other / ".github/workflows").mkdir(parents=True)
        (other / ".github/workflows/ci.yml").write_text("jobs: {t: {steps: [{run: make verify}]}}\n")
        self.assertNotIn(".github/workflows/verify.yml", init_project(other, ROOT))  # existing `make verify` CI wins

    def test_doctor_repo_enforces_budget_tags_and_layout(self):
        init_project(self.repo, ROOT)
        passes = lambda: {c.id: c.passes() for c in doctor_repo(self.repo)}
        self.assertTrue(all(passes().values()), render_checks(doctor_repo(self.repo)))
        memory = self.repo / ".agents/MEMORY.md"
        with memory.open("a") as f:
            f.write("- remember to be careful\n")
        self.assertFalse(passes()["state.MEMORY.md"])
        memory.write_text("- VERIFIED[x] " + "y" * STATE_BUDGET["MEMORY.md"] + "\n")
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

    def test_setup_is_the_default_goal_and_a_no_op_without_lockfiles(self):
        p = sh("make -s", self.dir, check=False)  # bare `make` runs setup; no uv.lock/bun.lock means nothing to install
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_verify_runs_the_gate_verbs_cheapest_first_and_fails_closed(self):
        p = sh("make -s verify", self.dir, check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("UNKNOWN[lint]"), p.stdout)  # lint first; nothing after a failure runs

    def test_crash_on_start_is_reported_and_placeholders_fail_closed(self):
        self.assertIn("dead", sh("make -s start RUN='exit 7'", self.dir, check=False).stdout)
        self.assertIn("dead pid=", sh("make -s status", self.dir, check=False).stdout)
        for verb in ("test", "coverage", "lint", "e2e"):
            p = sh(f"make -s {verb}", self.dir, check=False)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn(f"UNKNOWN[{verb}]", p.stdout)


if __name__ == "__main__":
    unittest.main()
