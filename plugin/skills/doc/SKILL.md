---
name: doc
description: "Documentation: grounded claims, universal prose catalog, Mermaid for schematic content, static check."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / doc

Scope = doc pages (`*.md|*.mdx`); code comments → the gate's docs step.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Ground.** read EVERY page in scope + the code|tests it describes; claims BC code|test|output, NOT recall. house style > defaults.
2. **Page rules first.** EVERY page gets the built-in `prose` check. IF a page has its own invariant (section|diagram|command) THEN add a `.agents/VERIFY.py` `RULES` entry before writing (`path`=page | `glob` + `"docs": True`); house-style characters (`„“`, `…`, `→`, `—`, etc.) → `CONFIG["prose"]["allow"][glob]`.
3. **Form.** IF schematic (≥3 ordered steps, branches, components + links, states, messages) THEN Mermaid (one idea, ≤~12 nodes) ELSE prose|table|list. Render (`bunx @mermaid-js/mermaid-cli`) to `tmp/` AND inspect; else UNKNOWN[mermaid.render].
4. **Write.** why > what. `README.md` (root, EVERY package with a CLI|API) per `../../templates/README.md.tmpl`; `ARCH.md` per package per `../../templates/ARCH.md.tmpl`, NOT a copy of README; both only VERIFIED facts.
5. **Static check.** CLI `prose --repo . --fix <pages>`; rewrite the rest by meaning (em dash → comma|colon|parentheses), NOT a character swap.
6. **Edit + catalog review.** apply the requested edits; read `../../references/PROSE.md`; check EVERY changed unit; fix what page|repo support. IF a fix needs an absent fact|source|decision THEN ask, NOT invent. `doc-edit` scopes this step to named pages.
7. **Gate.** CLI `gate --repo . --session ${CLAUDE_SESSION_ID}` until VERIFIED[gate]=true.

## Output contract

```text
VERIFIED[prose] BC `vae.py prose` → findings=0
VERIFIED[mermaid.render] BC tmp/<page>.png inspected|UNKNOWN
FINDING <rule> <page:line>: problem; instruction
```

## VAE-DIALECT core

VAE-DIALECT = min-token, uniquely decodable text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators (exact uppercase): `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/VAE-DIALECT.md`.
