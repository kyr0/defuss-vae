---
name: status
description: "What runs and whether the record matches: swarm agents, services, resources; reconciles .agents/SWARM_STATUS.yaml drift."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / status

Quick: observe, reconcile the registry, act only as far as the human asked. A process outranks its registry entry.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Observe.** CLI `swarm status --repo .` → resources + one line per agent; `make status` → service; `git worktree list`.
2. **Reconcile.** IF `LOST` or duplicate entries THEN CLI `swarm status --fix`, rerun → ∅ drift. A worktree with neither entry nor process: report it, NOT delete it.
3. **Act** per state:
   - `EXITED` code=0 → merge its worktree through the gate, THEN CLI `swarm rm --name <n>`.
   - `EXITED` code≠0 OR lost → tail `var/log/swarm/<n>.log` → respawn from its last on-disk result OR report the cause.
   - `STALLED`|`OVERDUE` → tail its log; IF hung THEN CLI `swarm stop --name <n>`.
   - `CONFLICT` → stop the later claim; NOT two agents on one path.
   - `RESOURCES`|`OVERCOMMIT` → NOT spawn more until capacity frees.
4. **Schedule.** IF agents stay `RUNNING` THEN next check after ~`eta_in_mins`/4, within 5 to 30 min, via the harness scheduler OR cron; NOT trust silence.

## Output contract

```text
VERIFIED[swarm] BC `vae.py swarm status` → <n> running, <n> need action
<STATE> <name>: <next step>
NEXT_CHECK: <minutes>|∅
```

## VAE-DIALECT core

VAE-DIALECT = min-token, uniquely decodable text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators (exact uppercase): `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/VAE-DIALECT.md`.
