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
from vae_project import MANAGED_START, doctor_repo, init_project, runs_verify
from vae_state import EPISODE_LEADS, MEMORY_LINE, append_episodes
from vae_verify import PROBE_TAG, render_checks


class InitDoctorTests(RepoCase):
    def test_init_scaffolds_layout_preserves_agents_and_is_idempotent(self):
        self.write("AGENTS.md", "# User rules\n\nKeep me.\n")
        self.write(".gitignore", "/tmp/\n")
        self.commit_all()
        first = init_project(self.repo, ROOT)
        self.assertEqual(set(first), {".agents/VERIFY.py", ".agents/EPISODES.md", ".agents/MEMORY.md", ".agents/CLI_GIST.md", "Makefile", ".gitignore", "AGENTS.md"})
        # `/tmp/` already covers tmp/*, so only the missing defaults are appended, in order.
        self.assertEqual((self.repo / ".gitignore").read_text(), "/tmp/\n.env\nvar/*\noutput/*\ninput/*\n.DS_Store\ndist/\n.agents/SWARM_STATUS.yaml\n",
                         "no toolchain yet: base lines only; init adds each stack's lines once its files exist")
        text = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Keep me.", text)
        self.assertIn(PROBE_TAG, text)
        # Hosts without hooks get the rules only through this block: the core always, a stack's defaults once its files exist.
        self.assertIn("real Playwright browser", text)
        self.assertNotIn("--enable-unsafe-swiftshader", text, "no UI yet: no web defaults")
        self.assertEqual(init_project(self.repo, ROOT), [])
        self.write("web/index.html", "<!doctype html>")
        self.assertIn("AGENTS.md", init_project(self.repo, ROOT))
        for token in ("WebGL2", "--enable-unsafe-swiftshader", "grantPermissions", "real network", "caddy reverse-proxy"):
            self.assertIn(token, (self.repo / "AGENTS.md").read_text())
        (self.repo / "web/index.html").unlink()
        init_project(self.repo, ROOT)
        # Stack lines arrive with the stack; an exempt line (a committed build) is never appended.
        (self.repo / ".gitignore").write_text("/tmp/\n.env\nvar/*\noutput/*\ninput/*\n.DS_Store\n")
        self.write("app.py", "")
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("CONFIG['gitignore_exempt'] = ['dist/']\n")
        init_project(self.repo, ROOT)
        text = (self.repo / ".gitignore").read_text()
        self.assertIn(".venv/\n", text)
        self.assertNotIn("dist/", text)
        self.assertNotIn("node_modules/", text)
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(MANAGED_START), 1)

    def test_ci_scaffold_needs_a_github_remote_and_never_duplicates(self):
        self.assertNotIn(".github/workflows/verify.yml", init_project(self.repo, ROOT))  # no remote: CI could not run
        sh("git remote add origin git@github.com:example/app.git", self.repo)
        self.assertIn(".github/workflows/verify.yml", init_project(self.repo, ROOT))
        wf = (self.repo / ".github/workflows/verify.yml").read_text()
        # The default render is the template itself, so `make lint` (actionlint on the template) covers it.
        self.assertEqual(wf, (ROOT / "templates/verify.yml").read_text())
        for token in ("jdx/mise-action@v5", "astral-sh/setup-uv@", "oven-sh/setup-bun@", "- run: make setup\n", "- run: make verify\n"):
            self.assertIn(token, wf)
        # setup-uv has no floating major tag after v7: `@v10` failed to resolve (tags API, 2026-10-07); pin a commit.
        self.assertRegex(wf, r"astral-sh/setup-uv@[0-9a-f]{40} # v\d+\.\d+\.\d+\n")
        self.assertEqual(init_project(self.repo, ROOT), [])  # idempotent
        other = self.repo.parent / "other"
        other.mkdir()
        sh("git init -q && git remote add origin https://github.com/example/b.git", other)
        (other / ".github/workflows").mkdir(parents=True)
        (other / ".github/workflows/ci.yml").write_text("jobs: {t: {steps: [{run: make -j4 verify}]}}\n")
        self.assertNotIn(".github/workflows/verify.yml", init_project(other, ROOT))  # existing verify CI wins

    def test_ci_config_sets_the_commands_or_turns_ci_off(self):
        sh("git remote add origin git@github.com:example/app.git", self.repo)
        init_project(self.repo, ROOT)
        wf = self.repo / ".github/workflows/verify.yml"
        verify_py = self.repo / ".agents/VERIFY.py"
        wf.unlink()
        with verify_py.open("a") as f:
            f.write("CONFIG['ci'] = False\n")
        self.assertNotIn(".github/workflows/verify.yml", init_project(self.repo, ROOT))
        with verify_py.open("a") as f:
            f.write("CONFIG['ci'] = ['make setup', 'make ci: all', 'echo \"#done\"']\n")
        init_project(self.repo, ROOT)
        steps = wf.read_text().split('CONFIG["ci"] commands')[1]
        # YAML-unsafe commands (`: `, `#`) become quoted strings, so the step runs exactly the configured text.
        self.assertEqual(steps.splitlines()[1:], ["      - run: make setup", '      - run: "make ci: all"',
                                                  '      - run: "echo \\"#done\\""'])
        with verify_py.open("a") as f:
            f.write("CONFIG['ci'] = 'make verify'\n")
        p = sh(f"python3 {ROOT}/scripts/vae.py init --repo .", self.repo, check=False)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("UNKNOWN[init] BC CONFIG['ci'] must be", p.stdout)

    def test_existing_workflow_counts_when_it_runs_the_projects_verify_commands(self):
        config = {"test_command": "uv run pytest -q", "e2e_commands": ["uv build", "uv run e2e.py"]}
        cases = {
            "run: make verify": True,
            'run: "make -C . -j 4 verify"': True,
            "run: |\n  make lint test\n  make coverage e2e": True,
            "run: make lint coverage\n# make test e2e": False,  # a commented-out step runs nothing
            "run: make lint coverage && uv run pytest \\\n    -q\nrun: uv build && uv run e2e.py": True,
            "run: make lint coverage e2e && uv run pytest": False,  # not the configured test command
            "run: cmake --build . && make test": False,
            "run: make setup && make ci": False,
        }
        for workflow, expected in cases.items():
            self.assertEqual(runs_verify(workflow, config), expected, workflow)
        self.assertTrue(runs_verify("run: make setup && make ci", {"ci": ["make setup", "make ci"]}))
        self.assertTrue(runs_verify("run: make lint test coverage e2e", {}))

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
        # A promoted lesson states its reason and stays one concise line.
        memory.write_text("- VERIFIED[src/db] pool size 4 BC load test deadlocked at 8\n")
        self.assertTrue(passes()["state.MEMORY.md"])
        memory.write_text("- VERIFIED[src/db] pool size 4\n")
        check = next(c for c in doctor_repo(self.repo) if c.id == "state.MEMORY.md")
        self.assertFalse(check.passes())
        self.assertIn("no BC", check.evidence)
        memory.write_text("- VERIFIED[src/db] pool size 4 BC " + "y" * MEMORY_LINE + "\n")
        self.assertFalse(passes()["state.MEMORY.md"])
        # The CLI gist holds commands; their evidence is the observed run, not a BC clause.
        (self.repo / ".agents/CLI_GIST.md").write_text("- VERIFIED[setup] `make setup`\n")
        self.assertTrue(passes()["state.CLI_GIST.md"])

    def test_doctor_caps_unsettled_episode_leads(self):
        init_project(self.repo, ROOT)
        lead = lambda: next(c for c in doctor_repo(self.repo) if c.id == "state.EPISODES.md")
        append_episodes(self.repo, "s", [f"LESSON l{i} BC x" for i in range(EPISODE_LEADS)] + ["DONE fp=1"]
                        + [f"FINDING a.py:{i} learn=test: pinned" for i in range(5)], ROOT)
        self.assertTrue(lead().passes(), lead().evidence)
        append_episodes(self.repo, "s", ["FINDING b.py:g learn=none: UNKNOWN lock"], ROOT)
        self.assertFalse(lead().passes())
        self.assertIn(f"leads={EPISODE_LEADS + 1}", lead().evidence)
        self.assertIn("references/CONSOLIDATION.md", lead().next)

    def test_doctor_lists_memory_that_cites_missing_paths_without_blocking(self):
        init_project(self.repo, ROOT)
        self.write("src/kept.py", "")
        self.write(".agents/MEMORY.md", "# Agent memory\n- VERIFIED[a] `src/old.py:load` validates input\n"
                   "- VERIFIED[b] src/kept.py stays pure; agent/human split; see ../../references/X.md and src/*.py\n")
        with (self.repo / "AGENTS.md").open("a") as f:
            f.write("\n- Run src/gone.sh before release.\n")
        stale = next(c for c in doctor_repo(self.repo) if c.id == "state.stale")
        self.assertTrue(stale.passes() and stale.value is False, "a candidate list, never a blocker")
        self.assertIn(".agents/MEMORY.md: src/old.py", stale.evidence)
        self.assertIn("AGENTS.md: src/gone.sh", stale.evidence)
        for fine in ("src/kept.py", "agent/human", "references/X.md", "src/*.py"):
            self.assertNotIn(f": {fine} ", stale.evidence, fine)
        self.assertIn("references/CONSOLIDATION.md", stale.next)


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

    def test_help_is_the_default_goal_and_setup_a_no_op_without_pins(self):
        p = sh("make -s", self.dir)  # bare `make` explains instead of installing anything
        listed = [ln.split()[0] for ln in p.stdout.splitlines()]
        self.assertEqual(listed, ["help", "setup", "start", "stop", "restart", "status", "log", "metrics", "bench", "test",
                                  "coverage", "lint", "e2e", "verify"], "every verb documents its usage")
        p = sh("make -s setup", self.dir, check=False)  # no mise.toml, uv.lock or bun.lock: nothing to install
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_env_keys_reach_recipes_and_the_app(self):
        (self.dir / ".env").write_text("API_URL=http://localhost:9\nexport TOKEN=abc\n# NOTE=x\n")
        with (self.dir / "Makefile").open("a") as f:
            f.write('\nshow: ; @echo "$$API_URL $$TOKEN $${NOTE:-unset} $${N:-unset}"\n')
        # Only the .env keys are exported: the template's own variables (N, NAME, LOG) stay out of the app's env.
        self.assertEqual(sh("make -s show", self.dir).stdout.strip(), "http://localhost:9 abc unset unset")
        sh("make -s start RUN='echo $$API_URL; sleep 30'", self.dir)  # `$$`: make expands `$A` in a command-line value
        self.assertIn("http://localhost:9", sh("make -s log N=5", self.dir).stdout, "the app `start` runs sees .env")

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
        self.assertIn("web: Playwright", sh("make -s e2e", self.dir, check=False).stdout)


if __name__ == "__main__":
    unittest.main()
