"""Package contracts: manifests, skill budgets, VAE-DIALECT discipline, probe-tag hygiene."""
from __future__ import annotations

import ast
import json
import re
import unittest

from vae_testkit import REPO, ROOT  # first: puts plugin/scripts on sys.path

# isort: split
from vae_verify import PROBE_TAG


class PackageTests(unittest.TestCase):
    SKILLS = ("plan", "implement", "review", "docs", "finalize")
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

    def test_skills_are_human_only_and_define_the_dialect(self):
        for name in self.SKILLS:
            text = self.skill(name)
            for token in ("disable-model-invocation: true", "## VAE-DIALECT core", "VERIFIED", "HYPOTHESIS", "UNKNOWN", "IF … THEN … ELSE", "../../references/VAE-DIALECT.md"):
                self.assertIn(token, text, name)

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
        plan, implement, review, docs, finalize = (self.skill(n) for n in self.SKILLS)
        for token in ("language stdlib", "native runtime/platform/framework", "current primary docs/source", "Probe unknowns", "`make e2e`", "test coverage lint e2e verify", "IF new project THEN step 0 = `bun init` (JS|TS) | `uv init` (Python)"):
            self.assertIn(token, plan)
        for token in ("You are a lazy senior developer", "RED→GREEN→REFACTOR", "root cause", "Evidence loop (priority 1)", PROBE_TAG, "5. Habits:", "ISO-8601 UTC timestamp first", "EVERY key in `.env.example`", "IF new project|subproject THEN start on `bun init` (JS|TS) | `uv init` (Python)",
                      "NOT mocks", "clean consumer", "`make start|stop|status|log`", "gate --repo . --session ${CLAUDE_SESSION_ID}"):
            self.assertIn(token, implement)
        for token in ("Correctness / contract pass", "Ponytail / maintainability pass", "tangled concerns", "ISO-8601 timestamp + level", "mocked|stubbed", "publishable artifact", "failing test|probe"):
            self.assertIn(token, review)
        for token in ("../../references/PROSE.md", "Page rules first", "Mermaid", "mermaid-cli", "prose --repo . --fix", "NOT a character swap", "THEN ask, NOT invent"):
            self.assertIn(token, docs)
        for token in ("Conventional Commits 1.0.0", ".agents/MEMORY.md", ".agents/CLI_GIST.md", ".agents/EPISODES.md", "doctor --repo", "LESSON"):
            self.assertIn(token, finalize)

    def test_dialect_reference_defines_every_operator(self):
        text = (ROOT / "references/VAE-DIALECT.md").read_text()
        for token in self.OPERATORS | {"→", "∅", "P=?", "A REQUIRES B"}:
            self.assertIn(token, text)

    def test_skills_stay_lean(self):
        sizes = {n: (ROOT / "skills" / n / "SKILL.md").stat().st_size for n in self.SKILLS}
        for name, size in sizes.items():
            self.assertLess(size, 5500, f"{name} skill grew to {size} bytes; move provenance/examples out of runtime prompt")
        self.assertLess(sum(sizes.values()), 21500, sizes)

    def test_modules_import_only_lower_layers(self):
        layers = ["vae_repo", "vae_prose", "vae_verify", "vae_state", "vae_gate", "vae_hooks", "vae_project"]
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
