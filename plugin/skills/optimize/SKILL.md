---
name: optimize
description: "Researches the target project's verifiers, gates and checks for a cost-vs-assurance Pareto optimum, then writes an optimization plan into plans/ and stops for review. The agent may start it when verification cost is the task or measured verification dominates the session budget."
---

# defuss-vae 0.9.0 / optimize

Research + plan only: NOT edit verifiers|gates|tests|hooks|workflow pipeline files; the approved plan runs as a separate `implement` task.
Role: senior build engineer pricing assurance. measured cost > estimate; a kept defect gate > a saved second.

## Boundaries

- NOT propose skipping a check whose inputs intersect the change; a skip REQUIRES disjoint-inputs evidence (the check's read set ∩ the changed paths = ∅).
- A cache REQUIRES content-hash keys over EVERY input the check reads (code, config, policy, toolchain version), NOT mtime|recency.
- Deferral to the workflow pipeline only for non-gating checks; EVERY gating check still runs before commit.
- Fail-closed behavior (missing verifier|verb|output → red) is out of scope for savings.
- An existing cache|skip|scope rule: record it VERIFIED with its key, NOT re-propose it.

## Workflow

1. **Ground.** read `AGENTS.md`, `.vae/MEMORY.md`, `.vae/CLI_GIST.md`; `grep` `.vae/EPISODES.md` for verifier|gate|check names. Map the verification surface: `Makefile` verbs, workflow pipeline files, `.vae/VERIFY.py` + custom verifier|gate scripts, hooks, test suites.
2. **Trace.** EVERY check → the input set it reads + the failure classes it catches, from its code|config; NOT infer cost|coverage from names.
3. **Measure.** existing bench|log first, ELSE `time <cmd>` on the current tree, cold AND warm IF a cache exists. A cost claim REQUIRES a measurement; HYPOTHESIS until measured.
4. **Change surface.** for representative changes (recent `git log --name-only`|the human's examples): which checks ran with inputs disjoint from the change = no plausible impact path.
5. **Pareto.** EVERY candidate carries saving (measured cost × run frequency) AND assurance loss (failure classes that lose their gate; ∅ only IF proven); drop dominated candidates. IF ≥2 plausible designs AND evidence discriminates THEN matrix `design | saving | assurance loss | cheapest falsifier`.
6. **Emit** `plans/<yyyy-mm-dd_hh-mm>_<slug>.md` (UTC), updated in place: findings tagged VERIFIED|HYPOTHESIS|UNKNOWN with BC; EVERY proposal with its proof obligation (a test|rule showing the narrowed check still fires on its inputs) + rollback; milestones `- [ ]` + proof.
7. **Explain** to the human: top savings, what stays gated, risks; suggest review; name `implement` as the runner of the approved plan. THEN stop.

## Output contract

```text
PLAN: plans/<yyyy-mm-dd_hh-mm>_<slug>.md
VERIFIED[x] BC ...
HYPOTHESIS[x] BC ...; falsifier=`...`
UNKNOWN[x] BC ...; probe=`...`
DECISION: ...
check | measured time | proposed rule | evidence
```

Omit empty lines. NOT praise|summary.

## VAE-DIALECT core

VAE-DIALECT: min-token, uniquely decodable; correctness > compression. `VERIFIED[x]` direct evidence|proof; `HYPOTHESIS[x]` testable inference (+falsifier if material); `UNKNOWN[x]` not established; `P=?` unknown value. Operators, exact uppercase: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN` equivalence; lone `THEN`|`→` sequence|result (ladder: first rung that holds); `SAYS` attribution; `BC` because|evidence; `EVERY SOME ONE` ∀∃∃!; `A REQUIRES B`: B necessary for A; `MAY` ◇; `A > B` A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code; IF no unique expansion THEN plain prose. Spec: `../../references/VAE-DIALECT.md`.
