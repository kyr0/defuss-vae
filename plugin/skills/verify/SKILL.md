---
name: verify
description: "High-precision review against requirements, callers, real tests, gate evidence and Ponytail minimalism; fixes confirmed defects, encodes repeatable ones as tests or rules."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / verify

Default = review + fix actionable findings; IF the human asks report-only THEN NOT edit.

## Method

Review the change in context, NOT an isolated diff: read requirements|plan, `AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md`, `.agents/VERIFY.py`, changed files, relevant callers|callees, tests; `grep` `.agents/EPISODES.md` for changed paths|symbols|symptoms. NOT infer behavior from filenames|untraced diff fragments.

**Correctness / contract pass**

- requirement|spec mismatch; wrong invariants; edge|error paths; state|concurrency|lifetime|resources.
- unrequested API|schema|compat break; security|trust boundary; data loss; accessibility where applicable.
- language|framework|runtime gotchas BC current code|version|docs.
- tests absent, tautological, mocked|stubbed (EVERY test REQUIRES real subsystems in isolation), testing implementation NOT behavior, asserting a HYPOTHESIS|env values that worked once, blind to realistic mutations.
- e2e REQUIRES consuming the built publishable artifact, NOT the source tree.
- observability: leftover probe|debug print|log spam|log without ISO-8601 timestamp + level = defect; services via `make start` → `var/log/`, `tmp/*.pid`.
- gate evidence: test|integration|e2e as applicable + coverage ≥60%; UNKNOWN stays blocking and explicit.

**Ponytail / maintainability pass**

- tangled concerns (I/O in core logic), oversized|untestable modules.
- delete|reuse before adding: repo helper → stdlib → native platform|framework → installed dependency → minimum code.
- duplication, dead flexibility, one-use abstraction, premature config, wrapper|factory|interface without a second real case.
- needless files|dependencies|allocations|I/O|loops; perf change only IF measured (`make bench`) OR asymptotically|resource-obviously dominant.
- docs explain why this choice > plausible alternative, NOT what syntax does.

## Finding filter

precision > volume. Actionable finding REQUIRES `path:line|symbol`, causal failure|risk, evidence, specific smaller|correct fix. IF finding is behavioral THEN (IF demonstration cheap THEN failing test|probe ELSE HYPOTHESIS + cheapest falsifier). NOT style taste, speculative future concern, pre-existing unrelated issue, low-evidence warning. Numeric confidence REQUIRES grounded evidence. IF ≥2 plausible root causes|designs AND evidence discriminates THEN compact matrix, ordinal posterior unless real priors.

## Fix + learn

EVERY actionable finding (fix mode): root-cause fix, smallest correct diff → regression test IF the root cause is a VERIFIED code defect → IF recurrence mechanically checkable THEN `.agents/VERIFY.py` rule ELSE `.agents/MEMORY.md` line only IF future agents need it → rerun `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py gate --repo . --session ${CLAUDE_SESSION_ID}`. Source|test edits invalidate prior attestations.
Clean review valid only after inspecting the changed contract in context; "tests pass" ≠ semantic review.

## Output contract

Findings:

```text
VERIFIED[finding.<n>]@`path:line|symbol` BC <evidence>; CAUSES <impact>; fix=<minimal fix>
VERIFIED[learning.<n>] BC test|verifier|memory:<artifact>
```

∅ actionable findings:

```text
VERIFIED[review.clean]=true BC changed contract + callers|tests inspected; gate=<evidence>
```

THEN only commands|results proving fixes. NOT praise|score|generic summary.

## VAE-DIALECT core

VAE-DIALECT = min-token, uniquely decodable text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators (exact uppercase): `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/VAE-DIALECT.md`.
