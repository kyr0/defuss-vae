"""Package contracts: manifests, skill budgets, VAE-DIALECT discipline, probe-tag hygiene."""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from vae_testkit import PY, REPO, ROOT  # first: puts plugin/scripts on sys.path

# isort: split
from vae_verify import PROBE_TAG


class PackageTests(unittest.TestCase):
    SKILLS = ("plan", "implement", "verify", "doc", "doc-edit", "wrap", "status")
    # Uppercase tokens that are identifiers or record keys, not VAE-DIALECT operators.
    NAMES = frozenset({"VERIFIED", "HYPOTHESIS", "UNKNOWN", "YAGNI", "REPL", "API", "JS", "TS", "ISO", "UTC", "RED", "GREEN", "REFACTOR", "MEMORY",
             "AGENTS", "VERIFY", "EPISODES", "CLI", "GIST", "CHANGELOG", "README", "VAE", "DIALECT", "PRIOR_ART", "DECISION", "PLAN",
             "UNCOMMITTED", "REMAINS", "FAIL", "DONE", "FINDING", "LESSON", "BREAKING", "CHANGE", "AGENT_CMD", "CLAUDE_PLUGIN_ROOT",
             "CLAUDE_SESSION_ID", "CONFIG", "RULES"})
    OPERATORS = frozenset({"NOT", "AND", "OR", "IF", "THEN", "ELSE", "WHEN", "CAUSES", "SAYS", "BC", "EVERY", "SOME", "ONE", "REQUIRES", "MAY"})

    def skill(self, name: str) -> str:
        return (ROOT / "skills" / name / "SKILL.md").read_text()

    def test_json_manifests(self):
        manifests = [json.loads((ROOT / rel).read_text()) for rel in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json")]
        json.loads((ROOT / "hooks/hooks.json").read_text())
        # Claude Code pins installs by version, so every host manifest must ship the same one.
        self.assertEqual(len({m["version"] for m in manifests}), 1, [m["version"] for m in manifests])
        # hooks/hooks.json loads by default; re-declaring it in the Claude manifest is redundant.
        self.assertNotIn("hooks", manifests[1])
        market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual((REPO / market["plugins"][0]["source"]).resolve(), ROOT)
        self.assertEqual((ROOT / "LICENSE").read_text(), (REPO / "LICENSE").read_text())
        # The README's citation and sample `claude plugin list` output name the current release, not an old one.
        readme = (REPO / "README.md").read_text()
        cited = re.findall(r"version\s*=\s*\{([^}]+)\}|Version: (\d+\.\d+\.\d+)", readme)
        self.assertEqual({a or b for a, b in cited}, {manifests[0]["version"]})

    def test_only_wrap_is_human_only_and_every_skill_defines_the_dialect(self):
        for name in self.SKILLS:
            text = self.skill(name)
            for token in ("## VAE-DIALECT core", "VERIFIED", "HYPOTHESIS", "UNKNOWN", "IF … THEN … ELSE", "../../references/VAE-DIALECT.md"):
                self.assertIn(token, text, name)
            # wrap commits, so only the human starts it; Claude Code reads the frontmatter key, Codex only its own switch.
            human = name == "wrap"
            policy = (ROOT / "skills" / name / "agents/openai.yaml").read_text()
            self.assertEqual("disable-model-invocation: true" in text, human, name)
            self.assertIn(f"allow_implicit_invocation: {str(not human).lower()}", policy, name)
            # The agent picks a skill by its description alone, so each one it may start names when.
            description = text.split("description: ", 1)[1].splitlines()[0]
            self.assertEqual(bool(re.search(r"The agent (?:may|should) start it", description)), not human, name)
            self.assertNotIn("human invocation", text, f"{name}: the frontmatter and policy enforce it; prose is token cost")

    def test_maintainer_makefile_keeps_every_exit_status(self):
        # A recipe piping into tail|head exits with tail's 0: `make coverage` passed CI below its floor (probed: 97 % vs
        # fail-under=99 gave exit 0 through the pipe, 2 without it). Recipes write to a file and show its last line.
        recipes = [ln for ln in (REPO / "Makefile").read_text().splitlines() if ln.startswith(("\t", "  "))]
        self.assertEqual([ln.strip() for ln in recipes if re.search(r"\|\s*(?:tail|head)\b", ln)], [])

    def test_plugin_doctor_passes_on_the_shipped_plugin(self):
        p = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "doctor"], capture_output=True, text=True, check=False)
        self.assertEqual((p.returncode, p.stdout.splitlines()), (0, ["VERIFIED[plugin.files]=true", "REMAINS: ∅"]))

    def test_doctor_flags_a_skill_whose_hosts_disagree_on_who_starts_it(self):
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "plugin"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
            doctor = lambda: subprocess.run([PY, str(copy / "scripts/vae.py"), "doctor"], capture_output=True, text=True, check=False)
            ok = doctor()
            self.assertEqual(ok.returncode, 0, ok.stdout)
            (copy / "skills/plan/agents/openai.yaml").unlink()  # Codex default: implicit invocation on
            wrap = copy / "skills/wrap/agents/openai.yaml"
            wrap.write_text(wrap.read_text().replace("allow_implicit_invocation: false", "allow_implicit_invocation: true"))
            verify = copy / "skills/verify/SKILL.md"
            verify.write_text(verify.read_text().replace("\nallowed-tools:", "\ndisable-model-invocation: true\nallowed-tools:", 1))
            out = doctor()
            self.assertNotEqual(out.returncode, 0)
            for name in ("plan", "wrap", "verify"):
                self.assertIn(f"skill-invocation-policy:{name}", out.stdout)
            self.assertNotIn("skill-invocation-policy:implement", out.stdout)

    def test_skill_frontmatter_is_strict_yaml(self):
        # Strict YAML parsers (e.g. the `npx skills` installer) skip a skill whose plain scalar contains ": ".
        for name in self.SKILLS:
            for line in self.skill(name).split("---", 2)[1].strip().splitlines():
                value = line.split(": ", 1)[1]
                self.assertTrue(": " not in value or value[0] == value[-1] == '"', f"{name}: quote {line!r}")

    def test_skills_use_dialect_operators_strictly(self):
        for name in self.SKILLS:
            body = self.skill(name).split("## VAE-DIALECT core")[0].split("---", 2)[2]
            body = re.sub(r"(?s)```.*?```|`[^`]*`", "", body)  # prose only; code spans hold identifiers
            words = set(re.findall(r"\b[A-Z][A-Z_]{1,}\b", body))
            self.assertEqual(words - self.OPERATORS - self.NAMES, set(), f"{name}: uppercase non-operators")
            self.assertIsNone(re.search(r"\bWHEN\b", body), f"{name}: WHEN is equivalence, never a conditional")

    def test_skill_prompt_contracts(self):
        plan, implement, verify, doc, doc_edit, wrap, status = (self.skill(n) for n in self.SKILLS)
        for token in ("language stdlib", "native runtime/platform/framework", "current primary docs/source", "Probe unknowns", "`make e2e`", "test coverage lint e2e verify", "IF new project THEN step 0 = `bun init` (JS|TS) | `uv init` (Python)"):
            self.assertIn(token, plan)
        # SessionStart injects no episodes, so each skill retrieves them by relevance; wrap reads them all.
        self.assertIn("`grep` `.agents/EPISODES.md` for touched paths|symptoms", plan)
        self.assertIn("`grep` `.agents/EPISODES.md` for touched paths|symbols|symptoms", implement)
        self.assertIn("`AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md`, `.agents/VERIFY.py`, changed files", verify)
        self.assertIn("`grep` `.agents/EPISODES.md` for changed paths|symbols|symptoms", verify)
        self.assertNotIn("`.agents/*`", verify)  # the full episode log is for wrap only
        for token in ("You are a lazy senior developer", "RED→GREEN→REFACTOR", "root cause", "Evidence loop (priority 1)", PROBE_TAG, "5. Habits:", "ISO-8601 UTC timestamp first", "EVERY key in `.env.example`", "IF new project|subproject THEN start on `bun init` (JS|TS) | `uv init` (Python)",
                      "NOT mocks", "clean consumer", "`make start|stop|status|log`", "gate --repo . --session ${CLAUDE_SESSION_ID}"):
            self.assertIn(token, implement)
        for token in ("Correctness / contract pass", "Ponytail / maintainability pass", "tangled concerns", "ISO-8601 timestamp + level", "mocked|stubbed", "publishable artifact", "failing test|probe"):
            self.assertIn(token, verify)
        for token in ("../../references/PROSE.md", "Page rules first", "Mermaid", "mermaid-cli", "prose --repo . --fix", "NOT a character swap", "THEN ask, NOT invent"):
            self.assertIn(token, doc)
        # doc-edit is doc's edit + catalog review step alone, scoped to the named pages.
        self.assertIn("`doc-edit` scopes this step to named pages", doc)
        for token in ("Scope = the named pages AND edits; NOT edit other pages|code", "NOT rewrite|restructure unrequested parts", "../../references/PROSE.md",
                      "check EVERY changed unit", "THEN ask, NOT invent", "CLI `prose --repo . <pages>` → report EVERY hit outside the edit", "THEN `--fix`"):
            self.assertIn(token, doc_edit)
        # Design discipline: one owner per concern, checked contracts, composition; text scanners and refactors need proof.
        for token in ("ONE owner module per concern", "explicit AND checked", "NOT share mutable state|inherit", "public API|events"):
            self.assertIn(token, plan)
            self.assertIn(token, implement)
        # The HEAD worktree lives outside the repo: pytest collected one under tmp/ ("import file mismatch", probed).
        for token in ("= a parser", "test EVERY scanner over it", "green tests alone NOT proof", "`cmp` its artifacts against a `HEAD` build",
                      "git worktree add $(mktemp -d) HEAD"):
            self.assertIn(token, implement)
        for token in ("../../references/CONSOLIDATION.md", "IF unsure THEN keep + retag UNKNOWN", "NOT delete on age alone", "NOT delete human-written content"):
            self.assertIn(token, wrap)
        for token in ("Conventional Commits 1.0.0", ".agents/MEMORY.md", ".agents/CLI_GIST.md", ".agents/EPISODES.md", "doctor --repo", "LESSON", "Reflect", "EVERY unencoded lesson of this work"):
            self.assertIn(token, wrap)
        self.assertIn("CLI `swarm status`: merge then reap EVERY `EXITED` agent", wrap)
        # Tests encode only VERIFIED requirements; a reproduction stays a regression test only for a verified code defect.
        self.assertIn("Invariants come from VERIFIED requirements only; a HYPOTHESIS gets a probe step, NOT a test", plan)
        self.assertIn("only IF the root cause is a VERIFIED code defect, NOT env|config that worked once", implement)
        for token in ("asserting a HYPOTHESIS|env values that worked once", "regression test IF the root cause is a VERIFIED code defect"):
            self.assertIn(token, verify)
        # A self-started plan stops for human review; the plan lives in a dated file with milestones only when complex.
        for token in ("self-started → stop after the plan for human review", "`plans/<yyyy-mm-dd_hh-mm>_<slug>.md` (UTC), updated in place",
                      "IF complex THEN milestones `- [ ]` + proof", "THEN `verify` its scope"):
            self.assertIn(token, plan)
        self.assertIn("At the goal|a plan milestone: `verify`, THEN `doc`", implement)
        self.assertIn("Scope: named paths|tests, ELSE the current change", verify)
        # Swarm orchestration: plan marks only safely splittable steps; status observes, reconciles, then acts per state.
        for token in ("`parallel` only IF target paths are disjoint AND no step depends on another's code", "`tmp/worktrees/<name>` IF outside writes are blocked, excluded from test discovery"):
            self.assertIn(token, plan)
        for token in ("swarm status --repo .", "swarm status --fix", "`EXITED` code=0 → merge its worktree through the gate",
                      "swarm stop --name", "NOT trust silence", "A process outranks its registry entry"):
            self.assertIn(token, status)

    def test_dialect_reference_defines_every_operator(self):
        text = (ROOT / "references/VAE-DIALECT.md").read_text()
        for token in self.OPERATORS | {"→", "∅", "P=?", "A REQUIRES B"}:
            self.assertIn(token, text)

    def test_skills_stay_lean(self):
        sizes = {n: (ROOT / "skills" / n / "SKILL.md").stat().st_size for n in self.SKILLS}
        for name, size in sizes.items():
            self.assertLess(size, 6000, f"{name} skill grew to {size} bytes; move provenance/examples out of runtime prompt")
        self.assertLess(sum(sizes.values()), 28000, sizes)

    def test_modules_import_only_lower_layers(self):
        layers = ["vae_repo", "vae_prose", "vae_verify", "vae_state", "vae_swarm", "vae_gate", "vae_hooks", "vae_project"]
        for i, name in enumerate(layers):
            tree = ast.parse((ROOT / "scripts" / f"{name}.py").read_text())
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("vae_")}
            self.assertLessEqual(imported, set(layers[:i]), f"{name} imports a higher layer: {imported - set(layers[:i])}")

    def test_plugin_code_never_contains_the_probe_tag(self):
        paths = sorted((ROOT / "scripts").glob("*.py")) + [ROOT / r for r in ("hooks/lifecycle.py", "templates/VERIFY.py", "templates/Makefile")]
        for path in paths + sorted((REPO / "tests").glob("*.py")):
            self.assertNotIn(PROBE_TAG, path.read_text(), path)


if __name__ == "__main__":
    unittest.main()
