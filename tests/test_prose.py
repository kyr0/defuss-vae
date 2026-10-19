"""Static prose checks: machine-writing tells, invisible and look-alike characters, Markdown and Mermaid rendering."""
from __future__ import annotations

import subprocess
import unittest

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    REPO,
    RepoCase,
)

# isort: split
from vae_prose import fix, parts, scan

EM, ELL, ZWSP, RLO, CYR_O, ARROW = "\u2014", "\u2026", "\u200b", "\u202e", "\u043e", "\u2192"
FENCE = "`" * 3


def rules(text: str, **kw) -> list[tuple[int, str]]:
    return [(f.line, f.rule) for f in scan(text, "page.md", **kw)]


class ScanTests(unittest.TestCase):
    def test_flags_machine_writing_tells_with_catalog_rules(self):
        text = f"Fast {EM} really.\nWait{ELL}\n\u201cQuoted\u201d\nLet's delve into it.\nA \u2013 B\n"
        self.assertEqual(rules(text), [(1, "T02"), (2, "T06"), (3, "T06"), (3, "T06"), (4, "S02"), (5, "T02")])

    def test_ranges_code_and_allowed_characters_are_content(self):
        text = f"Pages 1\u20134, `a {EM} b`, `x {ARROW} y \u2264 z \U0001F512`.\n{FENCE}\nx = '{EM}' {ARROW} \u2705\n{FENCE}\n"
        self.assertEqual(rules(text), [])
        self.assertEqual(rules(f"IF {ELL} THEN a {ARROW} b\n", allow=ELL + ARROW), [])

    def test_symbols_standing_in_for_words_need_a_rewrite_by_meaning(self):
        text = f"Coverage 31.6% {ARROW} 75.6%.\nCoverage \u2265 60%.\nAccept (\u2705) or reject (\u274c).\n\U0001F680 Fast.\n"
        found = scan(text, "page.md")
        self.assertEqual([(f.line, f.rule, f.fixable) for f in found], [(n, "T06", False) for n in (1, 2, 3, 4)])
        self.assertIn("from X to Y", found[0].message)
        self.assertIn("\u2705\u274c", found[2].message, "one finding per kind of symbol on a line")
        self.assertEqual(fix(text), text, "never a symbol swap: an ASCII arrow is the same tell")

    def test_typographic_spaces_become_plain_spaces(self):
        text = "10\u00a0ms and 5\u202f% `a\u00a0b`\n"
        self.assertEqual([(f.line, f.rule, f.fixable) for f in scan(text, "page.md")], [(1, "T06", True), (1, "T06", True)])
        self.assertEqual(fix(text), "10 ms and 5 % `a\u00a0b`\n")

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


class HtmlTests(unittest.TestCase):
    # a project site: text and reader-facing attributes are prose; code, scripts, comments and entity spaces are not
    PAGE = ("<p>From 55 &rarr; 231\n and \u201cquoted\u201d.</p>\n"
            "<pre><code>a \u2192 b \u201cq\u201d</code></pre>\n"
            '<img src="x.png"\n  alt="Go \u2192 there">\n'
            "<p>1&nbsp;ms, TODO</p>\n"
            '<script>const s = "\u2192 \u201cx\u201d";</script><!-- \u2192 -->\n'
            "<p>Wait&hellip; done</p>\n")

    def test_html_pages_check_their_text_and_reader_facing_attributes(self):
        found = [(f.line, f.rule, f.fixable) for f in scan(self.PAGE, "docs/index.html")]
        self.assertEqual(found, [(1, "T06", False), (2, "T06", True), (2, "T06", True), (5, "T06", False), (6, "T04", False),
                                 (8, "T06", True)])

    def test_html_attributes_stay_well_formed(self):
        page = '<img alt="the \u201cbest\u201d tool" title=\'it\u2019s\' data-x="a > b \u2192 c">\n'
        out = fix(page, html=True)
        self.assertEqual(out, '<img alt="the &quot;best&quot; tool" title=\'it&#39;s\' data-x="a > b \u2192 c">\n')
        self.assertEqual(scan(out, "docs/index.html"), [], "a quoted '>' does not end the tag")

    def test_html_fix_touches_only_text(self):
        out = fix(self.PAGE, html=True)
        self.assertEqual(out, self.PAGE.replace("\u201cquoted\u201d", '"quoted"').replace("&hellip;", "..."))
        self.assertEqual([(f.line, f.rule) for f in scan(out, "docs/index.html")], [(1, "T06"), (5, "T06"), (6, "T04")])


class LinkTests(RepoCase):
    def test_relative_links_resolve_against_the_page(self):
        self.write("docs/a.md", "x")
        self.write("docs/my file.md", "x")
        self.write("img/logo.png", "x")
        page = "[ok](a.md#top) [space](my%20file.md) [up](../img/logo.png) [web](https://x.invalid) [anchor](#h) [bad](missing.md) ![b](/nope.png)\n"
        found = scan(page, "docs/index.md", self.repo)
        self.assertEqual([f.message for f in found], ["broken relative link 'missing.md'", "broken relative link '/nope.png'"])


class PartsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
