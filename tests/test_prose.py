"""Static prose checks: machine-writing tells, invisible and look-alike characters, Markdown and Mermaid rendering."""
from __future__ import annotations

import subprocess
import unittest

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    PY,
    REPO,
    ROOT,
    RepoCase,
)

# isort: split
from vae_prose import Finding, fix, parts, scan, walk

EM, ELL, ZWSP, RLO, CYR_O = "\u2014", "\u2026", "\u200b", "\u202e", "\u043e"
FENCE = "`" * 3


def rules(text: str, **kw) -> list[tuple[int, str]]:
    return [(f.line, f.rule) for f in scan(text, "page.md", **kw)]


class ScanTests(unittest.TestCase):
    def test_flags_machine_writing_tells_with_catalog_rules(self):
        text = f"Fast {EM} really.\nWait{ELL}\n\u201cQuoted\u201d\nLet's delve into it.\nA \u2013 B\n"
        self.assertEqual(rules(text), [(1, "T02"), (2, "T06"), (3, "T06"), (3, "T06"), (4, "S02"), (5, "T02")])

    def test_ranges_code_and_allowed_characters_are_content(self):
        text = f"Pages 1\u20134, `a {EM} b`, arrows \u2192 \u2264 and emoji \U0001F512.\n{FENCE}\nx = '{EM}'\n{FENCE}\n"
        self.assertEqual(rules(text), [])
        self.assertEqual(rules(f"IF {ELL} THEN\n", allow=ELL), [])

    def test_invisible_and_bidi_characters_count_even_inside_code(self):
        self.assertEqual(rules(f"a{ZWSP}b\n{FENCE}\nx{RLO}y\n{FENCE}\n"), [(1, "T06"), (3, "T06")])
        self.assertEqual(rules("\ufeffTitle\n"), [], "a leading BOM is encoding, not content")

    def test_look_alike_letters_in_one_word(self):
        self.assertEqual(rules(f"Account K{CYR_O}nto-1\n"), [(1, "T06")])
        self.assertEqual(rules("\u03b1\u03b2\u03b3 and Konto\n"), [], "pure Greek and pure Latin words stay")

    def test_rendering_breaks(self):
        self.assertEqual(rules("\u2022 item\n"), [(1, "T01")])
        self.assertEqual(rules(f"{FENCE}python\nx = 1\n"), [(1, "T04")])
        self.assertEqual(rules("Left: TODO\n"), [(1, "T04")])
        self.assertEqual(rules("Build a todo app; use Claude as an AI pair programmer.\n"), [], "ordinary words are not tells")
        self.assertEqual(rules("As an AI language model, I cannot.\n"), [(1, "R07")])
        self.assertEqual(rules(f"{FENCE}mermaid\nflowchat LR\n  A --> B\n{FENCE}\n"), [(2, "T07")])
        self.assertEqual(rules(f"{FENCE}mermaid\n%% note\nflowchart LR\n  A -->|\"x| B\n{FENCE}\n"), [(4, "T07")])
        self.assertEqual(rules(f"{FENCE}mermaid\n---\ntitle: t\n---\nsequenceDiagram\n  A->>B: hi\n{FENCE}\n"), [])
        self.assertEqual(rules(f"{FENCE}mermaid\n{FENCE}\n"), [(1, "T07")])

    def test_readme_and_arch_state_only_verified_facts(self):
        text = "HYPOTHESIS: it scales; a gap counts as `UNKNOWN`.\n`UNKNOWN:` why.\nVERIFIED: tested; HYPOTHESIS[x] BC y\nPrints `UNKNOWN[metrics]` and exits 2.\n"
        self.assertEqual([(f.line, f.rule) for f in scan(text, "src/ARCH.md")], [(1, "B01"), (2, "B01"), (3, "B01")])
        self.assertEqual([(f.line, f.rule) for f in scan(text, "README.md")], [(1, "B01"), (2, "B01"), (3, "B01")])
        self.assertEqual(rules(text), [], "other pages may carry open hypotheses")

    def test_project_phrases(self):
        self.assertEqual(rules("Ein bahnbrechendes Werkzeug.\n", phrases=[r"bahnbrechend\w*"]), [(1, "S02")])


class FixTests(unittest.TestCase):
    def test_fix_applies_only_meaning_preserving_replacements(self):
        text = f"\u201cHi\u201d{ELL} it\u2019s{ZWSP} here {EM} now `\u201ccode\u201d`\n\u2022 item\n{FENCE}\ns = \u201cq\u201d{ZWSP}\n{FENCE}\n"
        out = fix(text)
        self.assertEqual(out, f"\"Hi\"... it's here {EM} now `\u201ccode\u201d`\n- item\n{FENCE}\ns = \u201cq\u201d\n{FENCE}\n")
        self.assertEqual(rules(out), [(1, "T02")], "only the em dash is left for a rewrite by meaning")
        self.assertEqual(fix(f"IF {ELL} THEN\n", allow=ELL), f"IF {ELL} THEN\n")


class LinkTests(RepoCase):
    def test_relative_links_resolve_against_the_page(self):
        self.write("docs/a.md", "x")
        self.write("docs/my file.md", "x")
        self.write("img/logo.png", "x")
        page = "[ok](a.md#top) [space](my%20file.md) [up](../img/logo.png) [web](https://x.invalid) [anchor](#h) [bad](missing.md) ![b](/nope.png)\n"
        found = scan(page, "docs/index.md", self.repo)
        self.assertEqual([f.message for f in found], ["broken relative link 'missing.md'", "broken relative link '/nope.png'"])


class WalkTests(RepoCase):
    def test_parts_join_back_and_never_cut_a_fence_or_strand_a_heading(self):
        pages = subprocess.run(["git", "ls-files", "*.md"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
        for page in pages:
            text = (REPO / page).read_text()
            ps = parts(text)
            self.assertEqual("".join(p.text for p in ps), text, page)
            for p in ps:
                self.assertEqual(sum(1 for ln in p.text.splitlines() if ln.lstrip().startswith(FENCE)) % 2, 0, f"{page} part {p.id}")
                content = [ln for ln in p.text.splitlines() if ln.strip()]
                self.assertFalse(content[-1].lstrip().startswith("#"), f"{page} part {p.id} ends on a heading")

    def test_parts_start_at_top_headings_and_split_long_lists_per_item(self):
        text = "---\ntitle: x\n---\nIntro\n\n# One\nPara\n\nTitle\n=====\nBody\n\n" + "".join(f"- item {i} " + "w" * 60 + "\n" for i in range(9))
        ps = parts(text, max_chars=200)
        self.assertEqual([p.headings for p in ps[:2]], [(), ("# One",)])
        self.assertEqual(ps[2].headings, ("Title",), "a setext heading starts a part and is never left alone")
        self.assertTrue(ps[2].text.startswith("Title\n=====\nBody"))
        self.assertGreater(len(ps), 3, "a long list spans parts, one item per block")
        self.assertTrue(all(p.text.startswith(("- item", "---", "# One", "Title")) for p in ps))
        self.assertEqual(parts(""), [])

    def test_walk_steps_through_every_part_then_requires_a_clean_static_check(self):
        text = "# Page\nOne.\n\n# Next\nTwo.\n"
        catalog = "- **B01 Facts.** Q?\n- **T02 Dashes.** Q?\n"
        cli = "python3 vae.py prose --repo /r"
        done, first = walk("p.md", text, 1, catalog, [], cli)
        self.assertFalse(done)
        for token in ("WALK p.md part 1/2", "CATALOG", "CHECK EVERY rule against part 1: B01 Facts, T02 Dashes", "NEXT: python3 vae.py prose --repo /r --walk p.md --part 2"):
            self.assertIn(token, first)
        self.assertNotIn("CONTEXT", first)
        done, second = walk("p.md", text, 2, catalog, [Finding("p.md", 5, "T02", "em dash")], cli)
        self.assertIn("CONTEXT part 1 (lines 1-3)", second)
        self.assertIn("STATIC: p.md:5 T02 em dash", second)
        self.assertNotIn("CATALOG", second, "the catalog is printed once")
        self.assertEqual(walk("p.md", text, 3, catalog, [], cli), (True, "VERIFIED[walk]=true BC p.md: 2 parts walked, prose findings=0"))
        done, last = walk("p.md", text, 3, catalog, [Finding("p.md", 5, "T02", "em dash")], cli)
        self.assertFalse(done)
        self.assertIn("python3 vae.py prose --repo /r --fix p.md", last)
        self.assertIn("NEXT: python3 vae.py prose --repo /r --walk p.md --part 3", last)
        self.assertTrue(walk("p.md", text, 4, catalog, [], cli)[1].startswith("UNKNOWN[walk] BC part 4 is outside 1..3"))

    def test_cli_walk_exits_2_until_verified(self):
        self.write("README.md", "# Calc\nAdds numbers.\n")
        run = lambda *args: subprocess.run([PY, str(ROOT / "scripts/vae.py"), "prose", "--repo", str(self.repo), *args],
                                           capture_output=True, text=True, check=False)
        step = run("--walk", "README.md")
        self.assertEqual(step.returncode, 2)
        self.assertIn("P09 Current state", step.stdout, "the first step prints the shipped catalog")
        self.assertEqual((run("--walk", "README.md", "--part", "2").returncode), 0)
        self.assertIn("UNKNOWN[walk] BC missing.md is not a file", run("--walk", "missing.md").stdout)


if __name__ == "__main__":
    unittest.main()
