---
name: doc
description: "Writes and syncs doc pages: grounded claims, prose catalog, Mermaid, static check. The agent may start it only after implementation."
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / doc

Scope = doc pages (`*.md|*.mdx`); code comments → the gate's docs step.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Ground.** read EVERY page in scope + the code|tests it describes; claims BC code|test|output, NOT recall. house style > defaults.
2. **Page rules first.** EVERY page gets the built-in `prose` check. IF a page has its own invariant (section|diagram|command) THEN add a `.agents/VERIFY.py` `RULES` entry before writing (`path`=page | `glob` + `"docs": True`); house-style characters (`„“`, `…`, `→`, `—`) → `CONFIG["prose"]["allow"][glob]`.
3. **Form.** IF schematic (≥3 ordered steps, branches, components + links, states, messages) THEN Mermaid (one idea, ≤~12 nodes) ELSE prose|table|list. Render (`bunx @mermaid-js/mermaid-cli`) to `tmp/` AND inspect; else UNKNOWN[mermaid.render].
4. **Write** what is: VERIFIED facts, NOT change narrative (catalog P09: now|new|existing|has been|no longer); why > what. `README.md` (root, EVERY package with a CLI|API) per `../../templates/README.md.tmpl`; `ARCH.md` per package per `../../templates/ARCH.md.tmpl`, NOT a copy of README.
5. **Static check.** CLI `prose --repo . --fix <pages>`; rewrite the rest by meaning, NOT a character swap.
6. **Walk** EVERY written page, CLI `prose --repo . --walk <page>`: per window EVERY rule of `../../references/PROSE.md`, then its `NEXT` until `VERIFIED[walk]`. IF a fix needs an absent fact|source|decision THEN ask, NOT invent.
7. **Gate.** CLI `gate --repo . --session ${CLAUDE_SESSION_ID}` until VERIFIED[gate]=true.

## Output contract

```text
VERIFIED[walk] BC `vae.py prose --walk <page>` → parts=<n>, prose findings=0
VERIFIED[mermaid.render] BC tmp/<page>.png inspected|UNKNOWN
FINDING <rule> <page:line>: problem; instruction
```

## VAE-DIALECT core

VAE-DIALECT: min-token, uniquely decodable; correctness > compression. `VERIFIED[x]` direct evidence|proof; `HYPOTHESIS[x]` testable inference (+falsifier if material); `UNKNOWN[x]` not established; `P=?` unknown value. Operators, exact uppercase: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN` equivalence; lone `THEN`|`→` sequence|result (ladder: first rung that holds); `SAYS` attribution; `BC` because|evidence; `EVERY SOME ONE` ∀∃∃!; `A REQUIRES B`: B necessary for A; `MAY` ◇; `A > B` A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code; IF no unique expansion THEN plain prose. Spec: `../../references/VAE-DIALECT.md`.
