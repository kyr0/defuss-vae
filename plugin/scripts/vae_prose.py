"""Static prose checks for doc pages (machine-writing tells, invisible or look-alike characters, Markdown that renders
wrong) and the page parts the anchored review (vae_edit) walks.

Pure functions over one page's text; the verifier and `vae.py prose` do the I/O. Every finding names the rule of
references/PROSE.md it instantiates, so the static check and the catalog review speak one vocabulary. A Markdown page is
checked outside code spans and fences; an HTML page (a project site, CONFIG["prose"]["html"]) in its text and
reader-facing attributes, outside code, scripts, styles and SVG.

WHY a character table, adapted from aslopcleaner (github.com/kyr0/defuss, packages/aslopcleaner), not its ASCII
normalization: aslopcleaner swaps every listed character for ASCII (`→` to `=>`), which keeps a symbol standing in for a
word. In prose the fix for an arrow, a relation sign, a check mark or an emoji is words or a layout, so those are
flagged for a rewrite by meaning; only swaps that cannot change meaning (curly quotes, the ellipsis character,
typographic spaces, invisible characters) are applied by `--fix`. Code, diagrams, quoted output, other languages'
quotation marks and a notation a page defines (VAE-DIALECT's operators) are content: a page or project allows them via
CONFIG["prose"]["allow"].
HYPOTHESIS: flagging symbols only outside code keeps false positives to pages that define a notation; falsifier:
ordinary pages that each need their own CONFIG["prose"]["allow"] entry."""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Iterable
from html import unescape
from pathlib import Path
from urllib.parse import unquote

# char -> (rule, replacement, reason). replacement None: only a rewrite by meaning fixes it, never a character swap.
CHARS = {
    "\u2014": ("T02", None, "em dash: rewrite as comma, colon, parentheses or two sentences"),
    "\u2015": ("T02", None, "horizontal bar used as a dash"),
    "\u201c": ("T06", '"', "curly double quote"),
    "\u201d": ("T06", '"', "curly double quote"),
    "\u2018": ("T06", "'", "curly single quote"),
    "\u2019": ("T06", "'", "curly apostrophe"),
    "\u2026": ("T06", "...", "ellipsis character"),
    **dict.fromkeys("\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f", ("T06", " ", (
        "non-breaking or typographic space, invisible in the source: a plain space, or `&nbsp;` in HTML where the line "
        "must not break"))),
}
SPACES = {c for c, (_, repl, _) in CHARS.items() if repl == " "}
# Symbols standing in for words (PROSE.md T06): flagged for words or a layout, never swapped. One finding per kind and line.
ARROWS = ("\u2192\u27f6\u279c\u2794\u279d\u27a1\u21e2\u21e8\u21d2\u27f9\u2190\u27f5\u21d0\u27f8\u2b05\u2194\u21c4\u21c6\u21d4\u27f7\u27fa\u2191\u21d1"
          "\u2193\u21d3\u2195\u21d5")
SYMBOLS = [
    (ARROWS, ("arrow standing in for a verb: write 'from X to Y', 'X, then Y' or 'X becomes Y', or lay it out as a list, "
              "table or diagram")),
    ("\u2264\u2265\u2260\u2248", "relation sign standing in for words: 'at most', 'at least', 'not', 'about'"),
    ("\u2714\u2705\u2611\u2713\u2717\u2718\u274c\u274e\u2716", ("check or cross mark standing in for a word: 'done', 'passes', "
                                                      "'fails', 'accept', 'reject'")),
]
# Emoji and dingbats (U+2600-27BF, U+2B00-2BFF, U+1F000-1FAFF) not listed above: decoration in technical prose.
PICTOGRAPH = re.compile("[\u2600-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff]")
LISTED = "".join(c for c, _ in SYMBOLS)
# Never content in a doc page, inside code or not: they hide text, break search, or reorder what renders (Trojan Source).
INVISIBLE = {
    "\u200b": "zero-width space", "\u2060": "word joiner", "\u00ad": "soft hyphen", "\ufeff": "byte-order mark",
    **{chr(c): "bidirectional control" for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))},
}
SPACED_EN_DASH = re.compile(r"\s\u2013\s")  # `1\u20134` (a range) stays; ` \u2013 ` used as a dash is flagged
GLYPH_BULLET = re.compile(r"^(\s*)[\u2022\u25e6\u25aa\u25ab\u25cf\u25cb\u2023\u2219]\s+")
# High-precision English tells only; projects add their own (any language) via CONFIG["prose"]["phrases"].
PHRASES = [
    (r"\bdelv(?:e|es|ed|ing)\b", "S02", "'delve': name the concrete action"),
    (r"\bit(?:'s| is) worth noting\b|\bin today's (?:fast-paced|digital|ever-changing|modern)\b|\blet's dive in\b", "S02", "filler framing"),
    (r"\bgame[- ]chang(?:er|ing)\b|\brevolutioni[sz](?:e|es|ed|ing)\b|\bunlock(?:s|ing)? the (?:full )?(?:power|potential)\b", "S03", "inflated claim"),
    (r"\btapestry\b|\ba testament to\b", "S03", "ornamental cliche"),
    (r"\bI hope this helps\b|\bas an AI (?:language )?model\b|\bhere is the (?:updated|revised|requested|improved)\b|\bcertainly!", "R07", "assistant leftover"),
]
# Case-sensitive: "a todo app" is prose, `TODO` is a leftover.
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME)\b|(?i:lorem ipsum)")
# README.md and ARCH.md state only verified facts; an open hypothesis or unknown belongs in docs/ until it is settled.
VERIFIED_ONLY = {"README.md", "ARCH.md"}
# Label forms only: `UNKNOWN:` also inside a code span (where labels are usually written), VAE-DIALECT `HYPOTHESIS[x]` in
# prose only (in a code span it quotes tool output). Naming a status ("counts as UNKNOWN") is not a claim.
UNVERIFIED_LABEL = re.compile(r"\b(?:HYPOTHESIS|UNKNOWN):")
UNVERIFIED_TAG = re.compile(r"\b(?:HYPOTHESIS|UNKNOWN)\[")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# Any `](target)`: also reaches the outer target of a badge link `[![alt](img)](target)`.
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
SCHEME = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#)")
WORD = re.compile(r"[^\W\d_]{2,}")
SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")
HTML_EXT = {".html", ".htm"}
# Not prose in an HTML page: comments, code-like elements, and the markup of every other tag (its reader-facing
# attributes excepted; a quoted value may hold `>`). WHY a scanner, not html.parser: findings need source line numbers
# and --fix must leave every byte outside the text as it is.
HTML_SKIP = re.compile(r"<!--.*?-->|<(script|style|pre|code|kbd|samp|svg|math|template|textarea)\b.*?</\1\s*>"
                       r"""|<(?:[^>"']|"[^"]*"|'[^']*')*>""", re.DOTALL | re.IGNORECASE)
HTML_ATTR = re.compile(r"""\s(?:alt|title|aria-label|placeholder|content)\s*=\s*(["'])(.*?)\1""", re.DOTALL | re.IGNORECASE)
ENTITY = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"  # what str.splitlines splits on: masking keeps line numbers
MERMAID_TYPES = {
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2", "erDiagram", "journey",
    "gantt", "pie", "gitGraph", "mindmap", "timeline", "quadrantChart", "requirementDiagram", "C4Context", "C4Container",
    "C4Component", "C4Dynamic", "C4Deployment", "sankey-beta", "xychart-beta", "block-beta", "packet-beta",
    "architecture-beta", "kanban", "radar-beta",
}


@dataclasses.dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str
    fixable: bool = False

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.rule} {self.message}" + (" (--fix)" if self.fixable else "")


def masked(line: str) -> str:
    """The line with inline code spans blanked: code is content, prose rules apply only around it."""
    return INLINE_CODE.sub(lambda m: " " * len(m.group()), line)


def mixed_script(word: str) -> bool:
    scripts = {unicodedata.name(c, "").split(" ")[0] for c in word}
    return len(scripts & set(SCRIPTS)) > 1


def html_prose(text: str) -> list[tuple[int, int, str]]:
    """The (start, end, quote) spans of an HTML page that a reader reads: text between tags (quote "") and
    reader-facing attribute values (quote: their delimiter, which a fix must not write unescaped)."""
    spans, pos = [], 0
    for m in HTML_SKIP.finditer(text):
        spans.append((pos, m.start(), ""))
        if not m.group(1) and not m.group().startswith("<!--"):
            spans += [(m.start() + a.start(2), m.start() + a.end(2), a.group(1)) for a in HTML_ATTR.finditer(m.group())]
        pos = m.end()
    spans.append((pos, len(text), ""))
    return [s for s in spans if s[0] < s[1]]


def decoded(s: str) -> str:
    """Entities as the characters they render (`&rarr;` is an arrow), except spaces: `&nbsp;` is a deliberate choice."""
    def one(m: re.Match[str]) -> str:
        ch = unescape(m.group())
        return m.group() if ch in SPACES else ch
    return ENTITY.sub(one, s)


def scan(text: str, path: str = "", root: Path | None = None, allow: str = "", phrases: Iterable[str] = ()) -> list[Finding]:
    """All findings for one page. `root` enables relative-link checks; `allow` lists characters this page may use."""
    found: list[Finding] = []
    extra = [(p, "S02", "project slop phrase") for p in phrases]
    rules = [(re.compile(p, re.IGNORECASE), r, why) for p, r, why in PHRASES + extra]
    fence: tuple[str, int, int, bool] | None = None  # (char, length, start line, mermaid)
    header = "none"  # Mermaid: none | frontmatter | done

    def add(n: int, rule: str, message: str, fixable: bool = False) -> None:
        found.append(Finding(path, n, rule, message, fixable))

    def prose_rules(n: int, prose: str, raw: str) -> None:
        """Character, phrase and placeholder rules on one line's prose; `raw` is it before entities were decoded."""
        for ch, (rule, repl, why) in CHARS.items():
            if ch in (raw if ch in SPACES else prose) and ch not in allow:
                add(n, rule, why + (f" -> {repl!r}" if repl is not None else ""), fixable=repl is not None)
        for chars, why in SYMBOLS:
            hit = "".join(dict.fromkeys(c for c in prose if c in chars and c not in allow))
            if hit:
                add(n, "T06", f"{hit} {why}")
        hit = "".join(dict.fromkeys(c for c in PICTOGRAPH.findall(prose) if c not in allow and c not in LISTED))
        if hit:
            add(n, "T06", f"{hit} emoji or pictograph in prose: say it in words, or drop the decoration")
        if "\u2013" not in allow and SPACED_EN_DASH.search(prose):
            add(n, "T02", "spaced en dash used as a dash: rewrite as comma, colon or parentheses")
        for rx, rule, why in rules:
            hit = rx.search(prose)
            if hit:
                add(n, rule, f"{why}: {hit.group()!r}")
        hit = PLACEHOLDER.search(prose)
        if hit:
            add(n, "T04", f"placeholder left in page: {hit.group()!r}")
        for word in WORD.findall(prose):
            if mixed_script(word):
                add(n, "T06", f"mixed Latin/Cyrillic/Greek letters in {word!r} (look-alike characters)")

    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        for ch, why in INVISIBLE.items():
            if ch in line and ch not in allow and not (ch == "\ufeff" and n == 1 and line.index(ch) == 0):
                add(n, "T06", f"invisible character U+{ord(ch):04X} ({why})", fixable=True)
    if Path(path).suffix.lower() in HTML_EXT:
        shown = [c if c in LINE_BREAKS else " " for c in text]
        for a, b, _ in html_prose(text):
            shown[a:b] = text[a:b]
        for n, line in enumerate("".join(shown).splitlines(), 1):
            prose_rules(n, decoded(line), line)
        return sorted(found, key=lambda f: f.line)
    for n, line in enumerate(lines, 1):
        m = FENCE.match(line)
        if fence:
            char, length, start, mermaid = fence
            if m and m.group(1)[0] == char and len(m.group(1)) >= length and not m.group(2).strip():
                if mermaid and header != "done":
                    add(start, "T07", "Mermaid block without a diagram type")
                fence = None
            elif mermaid and line.strip():
                stripped = line.strip()
                if header == "none" and stripped == "---":
                    header = "frontmatter"
                elif header == "frontmatter":
                    header = "none" if stripped == "---" else header
                elif header == "none" and not stripped.startswith("%%"):
                    header = "done"
                    if stripped.split()[0] not in MERMAID_TYPES:
                        add(n, "T07", f"unknown Mermaid diagram type {stripped.split()[0]!r}")
                elif header == "done" and stripped.count('"') % 2:
                    add(n, "T07", "unbalanced quote in Mermaid line (label breaks the diagram)")
            continue
        if m:
            info = m.group(2).strip().split()
            fence, header = (m.group(1)[0], len(m.group(1)), n, bool(info) and info[0] == "mermaid"), "none"
            continue
        prose = masked(line)
        prose_rules(n, prose, prose)
        if GLYPH_BULLET.match(prose):
            add(n, "T01", "glyph bullet does not render as a Markdown list -> '- '", fixable=True)
        hit = (UNVERIFIED_LABEL.search(line) or UNVERIFIED_TAG.search(prose)) if Path(path).name in VERIFIED_ONLY else None
        if hit:
            add(n, "B01", f"{hit.group()} claim in a verified-only page: verify it or move it to docs/")
        if root is not None:
            for target in LINK.findall(prose):
                if SCHEME.match(target):
                    continue
                rel = unquote(target.split("#", 1)[0].split("?", 1)[0])
                base = root if rel.startswith("/") else (root / path).parent
                if rel and not (base / rel.lstrip("/")).exists():
                    add(n, "T04", f"broken relative link {target!r}")
    if fence:
        add(fence[2], "T04", "unclosed code fence")
    return sorted(found, key=lambda f: f.line)


def fix(text: str, allow: str = "", html: bool = False) -> str:
    """Apply only the replacements that cannot change meaning; dashes, symbols and phrases stay for a rewrite by
    meaning. `html`: the page is HTML, fixed in its text and reader-facing attributes only."""
    if html:
        text = "".join(c for i, c in enumerate(text) if c not in INVISIBLE or c in allow or (i == 0 and c == "\ufeff"))
        out, pos = [], 0
        for a, b, quote in html_prose(text):
            fixed = prose_fix(ENTITY.sub(lambda m: entity_fix(m.group(), allow), text[a:b]), allow)
            out += [text[pos:a], fixed.replace(quote, "&quot;" if quote == '"' else "&#39;") if quote else fixed]
            pos = b
        return "".join(out) + text[pos:]
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        bom = line.startswith("\ufeff") and not out
        line = ("\ufeff" if bom else "") + "".join(c for c in line[bom:] if c not in INVISIBLE or c in allow)
        m = FENCE.match(line.rstrip("\r\n"))
        if fence or m:
            if fence and m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            elif not fence:
                fence = (m.group(1)[0], len(m.group(1)))
            out.append(line)
            continue
        parts, pos = [], 0
        for c in INLINE_CODE.finditer(line):
            parts += [prose_fix(line[pos:c.start()], allow), c.group()]
            pos = c.end()
        parts.append(prose_fix(line[pos:], allow))
        fixed = "".join(parts)
        out.append(GLYPH_BULLET.sub(r"\1- ", fixed, count=1))
    return "".join(out)


def prose_fix(s: str, allow: str) -> str:
    for ch, (_, repl, _) in CHARS.items():
        if repl is not None and ch not in allow:
            s = s.replace(ch, repl)
    return s


def entity_fix(entity: str, allow: str) -> str:
    """An HTML entity of a fixable character (`&hellip;`, `&ldquo;`) as its replacement; spaces stay deliberate."""
    ch = unescape(entity)
    repl = CHARS.get(ch, (None, None, None))[1]
    return repl if repl is not None and ch not in SPACES and ch not in allow else entity


# Page parts: the unit of the anchored review (vae_edit). WHY these defaults: pro-edit's (`maxPartChars: 8000,
# headingDepth: 2`); VERIFIED: (2026-10-11) `parts(source, 8000, 2)` reproduced the parts.json texts, headings and start
# lines of all 15 runs in feat-pro-edit/output.
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]|$)")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
CONTAINER = re.compile(r"^ {0,3}(?:[-*+][ \t]|\d{1,9}[.)][ \t]|>|\|)")  # list item, quote or table row: no setext heading
ITEM = re.compile(r"^( *)(?:[-*+]|\d{1,9}[.)])[ \t]")
RULE_ID = re.compile(r"^- \*\*([BLPRAST]\d\d [^*]+?)\.\*\*", re.MULTILINE)
PART_CHARS, HEADING_DEPTH = 8000, 2


@dataclasses.dataclass(frozen=True)
class Part:
    id: int
    first: int  # 1-based line numbers, inclusive
    last: int
    headings: tuple[str, ...]
    text: str


def blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Top-level Markdown blocks as (first line index, heading depth: 0 content, -1 frontmatter). A fenced block is
    one block whatever it holds; other blocks end at a blank line, a fence or a heading."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(lines)
    opening = lines[0].lstrip("﻿").rstrip() if lines else ""
    if opening in ("---", "+++"):
        close = ("---", "...") if opening == "---" else ("+++",)
        end = next((j for j in range(1, n) if lines[j].rstrip() in close), None)
        if end is not None:
            out.append((0, -1))
            i = end + 1
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        m = FENCE.match(lines[i])
        if m:
            out.append((i, 0))
            char, size, i = m.group(1)[0], len(m.group(1)), i + 1
            while i < n:
                c = FENCE.match(lines[i])
                i += 1
                if c and c.group(1)[0] == char and len(c.group(1)) >= size and not c.group(2).strip():
                    break
            continue
        h = HEADING.match(lines[i])
        if h:
            out.append((i, len(h.group(1))))
            i += 1
            continue
        start, depth, i = i, 0, i + 1
        while i < n and lines[i].strip() and not FENCE.match(lines[i]) and not HEADING.match(lines[i]):
            if SETEXT.match(lines[i]) and not CONTAINER.match(lines[start]):  # `Title` over `===`|`---`
                depth, i = (1 if lines[i].strip()[0] == "=" else 2), i + 1
                break
            # Each item of a list is a block of its own, so a long list can span parts; nested items stay inside.
            item, head = ITEM.match(lines[i]), ITEM.match(lines[start])
            if item and head and item.group(1) == head.group(1):
                break
            i += 1
        out.append((start, depth))
    return out


def parts(text: str, max_chars: int = PART_CHARS, heading_depth: int = HEADING_DEPTH) -> list[Part]:
    """The page as parts that join back to it exactly. A part starts at every heading of depth ≤ heading_depth and
    otherwise grows block by block up to max_chars; a heading stays with its first content block, never alone."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    bs = blocks(lines) or [(0, 0)]
    starts = [0] + [b[0] for b in bs[1:]]  # blank lines belong to the block before them
    ends = starts[1:] + [len(lines)]
    units: list[tuple[int, int, tuple[str, ...], bool]] = []
    ancestry: list[tuple[int, str]] = []
    k = 0
    while k < len(bs):
        first, boundary = k, False
        while k < len(bs) and bs[k][1] != 0:
            depth = bs[k][1]
            if depth > 0:
                while ancestry and ancestry[-1][0] >= depth:
                    ancestry.pop()
                ancestry.append((depth, lines[bs[k][0]].strip()))
                boundary = boundary or depth <= heading_depth
            k += 1
        k += k < len(bs)
        units.append((starts[first], ends[k - 1], tuple(t for _, t in ancestry), boundary))
    groups: list[list] = []
    for a, b, heads, boundary in units:
        if groups and not boundary and sum(map(len, lines[groups[-1][0]:b])) <= max_chars:
            groups[-1][1] = b
        else:
            groups.append([a, b, heads])
    out = [Part(i + 1, a + 1, b, heads, "".join(lines[a:b])) for i, (a, b, heads) in enumerate(groups)]
    if "".join(p.text for p in out) != text:
        raise ValueError("page parts do not join back to the page")
    return out

