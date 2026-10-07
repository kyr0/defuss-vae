---
name: doc-edit
description: "Edits only the named doc pages as instructed: grounded claims, catalog review of every changed unit, static check."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / doc-edit

Scope = the pages AND edits the human names; NOT edit other pages|code; NOT rewrite|restructure unrequested parts. Whole-page work → `doc`.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Ground.** read the page + the code|tests|output its edited claims describe; claims BC code|test|output, NOT recall. house style > defaults.
2. **Edit** exactly as instructed; keep structure and voice. IF new content is schematic THEN Mermaid (one idea, ≤~12 nodes), rendered to `tmp/` AND inspected.
3. **Catalog review.** read `../../references/PROSE.md`; check EVERY changed unit; fix what page|repo support. IF a fix needs an absent fact|source|decision THEN ask, NOT invent.
4. **Static check.** CLI `prose --repo . <pages>` → report EVERY hit outside the edit (the gate scans whole pages); THEN `--fix`; rewrite the rest by meaning, NOT a character swap.
5. **Gate.** CLI `gate --repo . --session ${CLAUDE_SESSION_ID}` until VERIFIED[gate]=true.

## Output contract

```text
EDITED <page>: <section> per instruction
VERIFIED[prose] BC `vae.py prose` → findings=0
OUTSIDE_EDIT: <page:line rule>|∅
```

## VAE-DIALECT core

VAE-DIALECT = min-token, uniquely decodable text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators (exact uppercase): `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/VAE-DIALECT.md`.
