---
name: doc-edit
description: "Edits only the named doc pages as instructed, with an anchored catalog review, a review page and the static check. The agent may start it after doc, for targeted fixes."
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae 0.9.0 / doc-edit

Scope = the named pages, whole unless the prompt names parts|sections, AND the requested edits; NOT edit other pages|code; NOT restructure beyond what a catalog rule fixes. New|synced pages → `doc`.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Ground.** read the page + the code|tests|output its edited claims describe; claims BC code|test|output, NOT recall. house style > defaults. Evidence = internal (repo files, local attachments); NOT gather external information while editing; a claim only an outside source settles → `belegen|pruefen` + a question; deep research only IF the human starts|confirms it.
2. **Edit** exactly as instructed: what is, VERIFIED facts, NOT change narrative (catalog P09); keep structure and voice. IF new content is schematic THEN Mermaid (one idea, ≤~12 nodes), rendered to `tmp/` AND inspected.
3. **Review** EVERY page: CLI `edit --repo . start <page> --model <your model id>` → per printed part EVERY rule of `../../references/PROSE.md`, findings as JSON per `../../references/edit/CONTRACT.md` in the printed draft file, then its `NEXT` until `VERIFIED[edit.review]`. Named parts|sections only: `[]` for the other parts. `--confidence high` only IF certain; ELSE the engine's second opinion arrives → submit the consensus + one reason per unpaired finding (`--decisions`). `REJECTED` → fix the JSON; drop a quote you cannot anchor, NOT guess it twice.
4. **Apply.** CLI `edit --repo . apply --run <run>`: resolved replacements only, hash-guarded, typed-decision check IF configured. EVERY `OPEN` line → fix from repo evidence, ELSE ask the human; NOT invent.
5. **Human review.** give the human the `PAGE:` link (edit text, comment, reply, accept|reject, "Send to agent"; a dashed card is applied, accepted unless answered). Human done → CLI `comments <review folder> --repo .` → follow its `DO` line: revise only what the human proposed|accepted, check with CLI `edit --repo . review <review folder> --notes <file> --check`, offer the next review revision; open questions go there, NOT chat.
6. **Gate.** CLI `gate --repo . --session ${CLAUDE_SESSION_ID}` until VERIFIED[gate]=true.

Engine-only review (the human asks; hours on long pages): CLI `swarm spawn --repo . --name edit-<page> --goal "review <page>" --workdir . --targets tmp/vae/edit --eta 30 -- python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py edit --repo <abs root> start <page> --engine`; after a failure `edit resume --run <run>`; then steps 4-6. Contract, modes, files: `../../references/EDIT.md`.

## Output contract

```text
EDITED <page>: <section> per instruction
REVIEW <run>: parts=<n>, findings=<n>, replacements=<n>, open=<n>
APPLIED <a>/<r>; OPEN <page:line> <rule> <action>: <question>|∅
PAGE: http://127.0.0.1:<port>/<page>-<n>-<sha8>/
VERIFIED[edit.review] BC <run>/final.json
```

## VAE-DIALECT core

VAE-DIALECT: min-token, uniquely decodable; correctness > compression. `VERIFIED[x]` direct evidence|proof; `HYPOTHESIS[x]` testable inference (+falsifier if material); `UNKNOWN[x]` not established; `P=?` unknown value. Operators, exact uppercase: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN` equivalence; lone `THEN`|`→` sequence|result (ladder: first rung that holds); `SAYS` attribution; `BC` because|evidence; `EVERY SOME ONE` ∀∃∃!; `A REQUIRES B`: B necessary for A; `MAY` ◇; `A > B` A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code; IF no unique expansion THEN plain prose. Spec: `../../references/VAE-DIALECT.md`.
