"""Static prose checks: machine-writing tells, invisible and look-alike characters, Markdown and Mermaid rendering."""
from __future__ import annotations

import unittest

from vae_testkit import RepoCase  # first: puts plugin/scripts on sys.path

# isort: split
from vae_prose import fix, scan

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


if __name__ == "__main__":
    unittest.main()
