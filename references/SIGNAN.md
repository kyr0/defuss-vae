# Signan engineering dialect

Purpose: minimum-token technical text that is still uniquely decodable — for agent output and for defuss-vae's own skill prompts. Compression never outranks correctness.

## Epistemics

- `VERIFIED[scope]`: established by direct code inspection, command output, test, measurement, proof, or authoritative source named in context.
- `HYPOTHESIS[scope]`: testable inference not yet established. Include cheapest discriminating test/falsifier when material.
- `UNKNOWN[scope]`: material fact not established with available evidence. Name the missing evidence when useful.
- Values: `TAG[x]=true|false`; `P=?` unknown value.
- Never promote `HYPOTHESIS`/`UNKNOWN` by confidence language. Evidence older than the last relevant edit is stale.

## Grammar

Exact uppercase words are operators only when listed here; lowercase/mixed-case occurrences are ordinary lexical text. Never uppercase other words for emphasis — it makes operators ambiguous.

Operators: `NOT`, `AND`, `OR`, `IF … THEN … ELSE`, `WHEN`, `CAUSES`, `SAYS`, `BC`, `EVERY`, `SOME`, `ONE`, `REQUIRES`, `MAY`.

Semantics:

- `IF A THEN B`: implication. `IF A THEN B ELSE C`: B if A holds, otherwise C; `ELSE` binds to the nearest `IF`.
- `A WHEN B`: equivalence between complete propositions — never a conditional.
- unpaired `THEN`, `A → B`: sequence; `cmd → result`: observed output. Ladder `a → b → c`: try in order, stop at the first rung that holds.
- `A CAUSES B`: causal claim.
- `X SAYS P`: explicit attribution.
- `P BC E`: evidence/reason attribution.
- `EVERY`/`SOME`/`ONE`: ∀/∃/∃!. `EVERY A IS B`: class inclusion.
- `A REQUIRES B`: B is necessary for A (□(A→B)). Postfix `P REQUIRES`: □P (obligatory).
- `MAY`: ◇ (permitted/possible) over its clause.
- `A > B` between goals/options: A outranks B. Other comparisons use canonical `= ≠ < ≤ > ≥`.
- `a|b`: alternatives for one slot. `∅`: none/empty.
- `x = y`: definition/identity. `X:=P`: local alias for an exact prior proposition/AST only; never creates a new assertion. `X:P`: unary predication only when unambiguous.
- `P@C`: typed context; `(P)@C`: proposition context.
- `,`: same-role list without logical fanout. `;`: unordered sibling propositions. Parentheses: scope.
- `P?`: question.

## Losslessness

Preserve every material proposition/referent plus scope, polarity, modality, quantification, attribution, comparison, causality, order, context, numbers, units, code, notation, and requested verbatim text. Unsupported meaning stays lexical/conventional prose. Never compress code, commands, identifiers, paths, commit messages, or quotations.

Before emitting compressed text, mentally expand it. IF expansion is not unique OR semantic loss > negligible THEN use the shortest longer form that is unique. User-requested format overrides Signan.

## Instruction pattern

defuss-vae skills are written in Signan: one imperative clause per line or bullet; conditions `IF … THEN … ELSE`; prohibitions `NOT`; ladders `→`; priorities `>`; exact commands, paths and identifiers in backticks. A persona line or quoted rule MAY stay verbatim prose.

## Engineering output pattern

Prefer compact records such as:

```text
VERIFIED[tests] BC `make test` → 142/142 pass
HYPOTHESIS[race] BC shared mutable cache; falsifier=`make test T=cache -count=50`
UNKNOWN[e2e] BC browser harness unavailable
IF HYPOTHESIS[race] THEN next=probe cache writer ELSE next=bisect
```

Bayesian matrices MAY be used only when ≥2 live hypotheses/designs AND evidence discriminates. Numeric priors/posteriors require grounded frequencies/measurements; otherwise use ordinal posterior ordering + falsifier.
