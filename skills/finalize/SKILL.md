---
name: finalize
description: Human-triggered finalization of verified work — fresh gate proof, coherent Conventional Commits without unrelated changes, CHANGELOG, and consolidated agent memory (MEMORY, CLI gist, episodes, AGENTS.md).
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / finalize

Precondition: explicit human invocation. NOT push|merge|release|rewrite history|destructive cleanup|remote action unless the human explicitly asks.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Reconstruct state.** read `git status`, relevant diff|history, requirements|plan, `AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md`, `.agents/EPISODES.md`. Unrelated|pre-existing user changes: preserve AND keep out of commits unless causally required.
2. **Prove current state.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py gate --repo . --session ${CLAUDE_SESSION_ID}` → `VERIFIED[gate]=true` for the current fingerprint; read full results. NOT claim success|commit code from stale evidence. IF fail|UNKNOWN THEN fix cause first; code edit → gate again.
3. **Partition by causal concern.** EVERY commit independently understandable|revertible AND leaves the repo coherent. Separate unrelated feat|fix|refactor|test|build; a regression test stays with the behavior it proves unless project convention differs. Conventional Commits 1.0.0: `type(scope): imperative description`; `feat` feature, `fix` bug fix, also `refactor|perf|test|docs|build|ci|chore`; `!`|`BREAKING CHANGE:` only for actual breaking API|behavior.
4. **Commit implementation groups.** Stage exact paths|hunks; inspect staged diff before each commit. NOT `git add -A` IF unrelated changes exist. NOT `--no-verify`; NOT rewrite published history.
5. **Init state.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py init --repo .` → missing `.agents/*`, `Makefile`, gitignored `var/log/` `tmp/`, managed `AGENTS.md` block; never overwrites.
6. **Consolidate memory; replace stale, NOT append-only.** Hierarchy: test|`VERIFY.py` rule > MEMORY line > EPISODES line.
   - `CHANGELOG.md`: concise `Unreleased` user-visible changes; keep project format.
   - `.agents/MEMORY.md`: one tagged line per durable decision|invariant|constraint NOT derivable from code|git|docs; delete superseded lines.
   - `.agents/EPISODES.md`: gate writes `FAIL|DONE|FINDING`; add ≤1 `LESSON` line per falsified HYPOTHESIS|dead end|root cause not yet captured. IF a lesson recurs ≥2 THEN promote it (test|rule|MEMORY) AND delete its lines.
   - `.agents/CLI_GIST.md`: shortest non-Makefile setup|dev|data|release commands; `VERIFIED` only IF observed success ELSE `UNKNOWN`; delete stale.
   - `AGENTS.md`: preserve human content; managed block bounded; add only rules that prevent repeat mistakes.
7. **Check state.** CLI `doctor --repo .` → budgets, epistemic tags, layout. IF production|test code changed during finalize THEN gate before the next commit.
8. **Commit metadata** coherently (`docs(...)`|`chore(...)`). Final `git status` = only intentionally untouched unrelated changes; NOT call it clean IF such remain.
9. **Report** ordered SHAs|messages, exact verification commands|results, remaining UNKNOWN, intentionally uncommitted paths. NOT push unless requested.

## Output contract

```text
VERIFIED[gate] BC `vae.py gate` → VERIFIED[gate]=true
VERIFIED[commit.1] BC `<sha> type(scope): summary`
VERIFIED[state] BC `vae.py doctor --repo .` → REMAINS: ∅
UNKNOWN[x] BC ...
UNCOMMITTED: <intentional paths>|∅
```

## Signan core

Signan = min-token, uniquely decodable technical text; correctness > compression. `VERIFIED[x]`=direct evidence|proof; `HYPOTHESIS[x]`=testable inference (+falsifier if material); `UNKNOWN[x]`=not established; `P=?` unknown value. Operators = exact uppercase only: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN`=equivalence; unpaired `THEN`|`→`=sequence|result (ladder: first rung that holds); `SAYS`=attribution; `BC`=because|evidence; `EVERY SOME ONE`=∀ ∃ ∃!; `A REQUIRES B`=B necessary for A; `MAY`=◇; `A > B`=A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code. IF unique expansion impossible THEN conventional prose. Spec: `../../references/SIGNAN.md`.
