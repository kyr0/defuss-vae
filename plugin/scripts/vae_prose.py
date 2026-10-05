"""Static prose checks for doc pages: machine-writing tells, invisible or look-alike characters, Markdown that renders wrong.

Pure functions over one page's text; the verifier and `vae.py prose` do the I/O. Every finding names the rule of
references/PROSE.md it instantiates, so the static check and the catalog review speak one vocabulary.

WHY a short list of characters, not ASCII normalization: non-ASCII is not a defect (arrows, math, emoji and other
languages' quotation marks are content, PROSE.md T06). Flagged are only characters that are typical machine-writing
tells or that break rendering, search or exact matching. A page or project allows more via CONFIG["prose"].
HYPOTHESIS: this short list plus per-page allows gives fewer false positives than an ASCII allowlist; falsifier:
ordinary pages that each need their own CONFIG["prose"]["allow"] entry."""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Iterable
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
}
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


def scan(text: str, path: str = "", root: Path | None = None, allow: str = "", phrases: Iterable[str] = ()) -> list[Finding]:
    """All findings for one page. `root` enables relative-link checks; `allow` lists characters this page may use."""
    found: list[Finding] = []
    extra = [(p, "S02", "project slop phrase") for p in phrases]
    rules = [(re.compile(p, re.IGNORECASE), r, why) for p, r, why in PHRASES + extra]
    fence: tuple[str, int, int, bool] | None = None  # (char, length, start line, mermaid)
    header = "none"  # Mermaid: none | frontmatter | done

    def add(n: int, rule: str, message: str, fixable: bool = False) -> None:
        found.append(Finding(path, n, rule, message, fixable))

    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        for ch, why in INVISIBLE.items():
            if ch in line and ch not in allow and not (ch == "\ufeff" and n == 1 and line.index(ch) == 0):
                add(n, "T06", f"invisible character U+{ord(ch):04X} ({why})", fixable=True)
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
        for ch, (rule, repl, why) in CHARS.items():
            if ch in prose and ch not in allow:
                add(n, rule, why + (f" -> {repl!r}" if repl is not None else ""), fixable=repl is not None)
        if "\u2013" not in allow and SPACED_EN_DASH.search(prose):
            add(n, "T02", "spaced en dash used as a dash: rewrite as comma, colon or parentheses")
        if GLYPH_BULLET.match(prose):
            add(n, "T01", "glyph bullet does not render as a Markdown list -> '- '", fixable=True)
        for rx, rule, why in rules:
            hit = rx.search(prose)
            if hit:
                add(n, rule, f"{why}: {hit.group()!r}")
        hit = (UNVERIFIED_LABEL.search(line) or UNVERIFIED_TAG.search(prose)) if Path(path).name in VERIFIED_ONLY else None
        if hit:
            add(n, "B01", f"{hit.group()} claim in a verified-only page: verify it or move it to docs/")
        hit = PLACEHOLDER.search(prose)
        if hit:
            add(n, "T04", f"placeholder left in page: {hit.group()!r}")
        for word in WORD.findall(prose):
            if mixed_script(word):
                add(n, "T06", f"mixed Latin/Cyrillic/Greek letters in {word!r} (look-alike characters)")
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
    return found


def fix(text: str, allow: str = "") -> str:
    """Apply only the replacements that cannot change meaning; dashes and phrases stay for a rewrite by meaning."""
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
