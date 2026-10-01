---
name: plan
description: Human-triggered lean planning: trace the real path, probe unknowns, climb the prior-art ladder, emit the smallest plan whose invariants are executable checks.
disable-model-invocation: true
---

# defuss-vae / plan

Precondition: explicit human invocation. NOT implement unless the human asks for plan + implement.
Role: senior engineer planning for a strong engineer without hidden context. evidence > design; smallest correct testable plan > elaborate architecture.

## Workflow

1. **Ground.** read `AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md`; `grep` `.agents/EPISODES.md` for touched paths|symptoms. read EVERY user-referenced file before claims about it. versions BC manifests|lockfiles, NOT recall.
2. **Trace.** entrypoint → state|data|control flow → output. IF bug THEN reproduce OR name the failing invariant; inspect EVERY caller of the shared function|contract before choosing the fix location.
3. **Probe unknowns.** IF a material runtime fact=? THEN observe, cheapest first: read code → existing test|command → REPL|`curl`|`make log` → scratch script in `tmp/` → ask human. NOT edit source in plan; HYPOTHESIS stays HYPOTHESIS until observed.
4. **Prior-art ladder** (stop at first rung that fully holds): YAGNI → repo helper|pattern → language stdlib → native runtime/platform/framework → installed dependency → current primary docs/source → external prior art (only to resolve remaining UNKNOWN).
5. **Proof before edits.** EVERY acceptance invariant REQUIRES an executable check the gate runs: `make test` (real subsystems in isolation, NOT mocks) | `make e2e` (build publishable artifact → clean consumer → `input/` → `output/`) | `.agents/VERIFY.py` rule. An existing test counts only IF it exercises the changed contract.
6. **Minimum design.** deletion|reuse > addition; fewest files. NOT speculative interface|factory|config|wrapper|helper|migration|compat layer|dependency. Preserve trust-boundary validation, data-loss protection, security, accessibility, explicit requirements.
7. **Alternatives only IF live.** IF ≥2 plausible designs AND evidence discriminates THEN matrix `H | for | against | posterior order | cheapest falsifier`; numeric probability REQUIRES grounded base rates|measurements.
8. **Layout.** `Makefile` verbs `setup start stop status log metrics bench test coverage lint e2e verify`; services log `var/log/<svc>.stdout|.stderr`, pid `tmp/<svc>.pid` (gitignored). IF new project THEN step 0 = `bun init` (JS|TS) | `uv init` (Python). IF layout missing THEN `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py init --repo .` precedes feature steps.
9. **Emit** steps in dependency order; EVERY step names `path:symbol`, semantic change, proof, why-doc obligation; risk|rollback only IF material.

## Output contract

```text
VERIFIED[scope] BC ...
UNKNOWN[x] BC ...; probe=`...`
PRIOR_ART: rung → evidence
DECISION: ...
PLAN:
1. `path:symbol` — change; proof=`make test|make e2e|rule:<id>`; docs=`why ...`
HYPOTHESIS[x] BC ...; falsifier=`...`
```

Omit empty lines. NOT implementation code except a tiny signature|schema fragment. NOT generic process prose.

## Signan core

Signan = min-token, uniquely decodable technical text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators = exact uppercase only: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/SIGNAN.md`.
