---
name: wrap
description: "Wraps everything up: fresh gate proof, coherent Conventional Commits, CHANGELOG; reflects lessons learned into new memories and consolidates agent memory. Only the human starts it."
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py *)
---

# defuss-vae / wrap

NOT push|merge|release|rewrite history|destructive cleanup|remote action unless the human explicitly asks.
CLI = `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/vae.py` (other hosts: plugin root = skill dir/../..).

## Workflow

1. **Reconstruct state.** read `git status`, relevant diff|history, requirements|plan, `AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md`, `.agents/EPISODES.md`. Unrelated|pre-existing user changes: preserve AND keep out of commits unless causally required. CLI `swarm status`: merge then reap EVERY `EXITED` agent; NOT wrap paths a live agent targets.
2. **Prove current state.** CLI `gate --repo . --session ${CLAUDE_SESSION_ID}` → `VERIFIED[gate]=true` for the current fingerprint; read full results. NOT claim success|commit code from stale evidence. IF fail|UNKNOWN THEN fix cause first; code edit → gate again.
3. **Partition by causal concern.** EVERY commit independently understandable|revertible AND leaves the repo coherent. Separate unrelated feat|fix|refactor|test|build; a regression test stays with the behavior it proves unless project convention differs. Conventional Commits 1.0.0: `type(scope): imperative description`; `feat` feature, `fix` bug fix, also `refactor|perf|test|docs|build|ci|chore`; `!`|`BREAKING CHANGE:` only for actual breaking API|behavior.
4. **Commit implementation groups.** Stage exact paths|hunks; inspect staged diff before each commit. NOT `git add -A` IF unrelated changes exist. NOT `--no-verify`; NOT rewrite published history.
5. **Init state.** CLI `init --repo .` → missing `.agents/*`, `Makefile`, default `.gitignore` lines, managed `AGENTS.md` block; never overwrites.
6. **Reflect + consolidate memory** per `../../references/CONSOLIDATION.md`; hierarchy test|`VERIFY.py` rule > MEMORY line > EPISODES line. `CHANGELOG.md`: concise `Unreleased` user-visible changes.
   - Reflect: EVERY unencoded lesson of this work (falsified HYPOTHESIS, dead end, root cause, working command) + EVERY lead (`LESSON`, `FINDING learn=none`) → test|`VERIFY.py` rule|ONE concise MEMORY line + `BC`|`.agents/CLI_GIST.md` line, then delete the lead; OR delete it BC evidence.
   - Audit EVERY `.agents/MEMORY.md`|`.agents/CLI_GIST.md`|`.agents/EPISODES.md` entry + agent-added `AGENTS.md` rule against current code|tests|commands; `doctor --repo .` lists entries citing missing paths.
   - delete|rewrite only BC evidence: contradicted, superseded by test|rule, derivable, target moved|removed, duplicate. IF unsure THEN keep + retag UNKNOWN; NOT delete on age alone; NOT delete human-written content, propose it.
   - Report EVERY removal|rewrite + its evidence.
7. **Check state.** CLI `doctor --repo .` → budgets, epistemic tags, layout. IF production|test code changed during wrap THEN gate before the next commit.
8. **Commit metadata** coherently (`docs(...)`|`chore(...)`). Final `git status` = only intentionally untouched unrelated changes; NOT call it clean IF such remain.
9. **Report** ordered SHAs|messages, exact verification commands|results, remaining UNKNOWN, intentionally uncommitted paths.

## Output contract

```text
VERIFIED[gate] BC `vae.py gate` → VERIFIED[gate]=true
VERIFIED[commit.1] BC `<sha> type(scope): summary`
VERIFIED[state] BC `vae.py doctor --repo .` → REMAINS: ∅
UNKNOWN[x] BC ...
UNCOMMITTED: <intentional paths>|∅
```

## VAE-DIALECT core

VAE-DIALECT: min-token, uniquely decodable; correctness > compression. `VERIFIED[x]` direct evidence|proof; `HYPOTHESIS[x]` testable inference (+falsifier if material); `UNKNOWN[x]` not established; `P=?` unknown value. Operators, exact uppercase: `NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`. `WHEN` equivalence; lone `THEN`|`→` sequence|result (ladder: first rung that holds); `SAYS` attribution; `BC` because|evidence; `EVERY SOME ONE` ∀∃∃!; `A REQUIRES B`: B necessary for A; `MAY` ◇; `A > B` A outranks B; `a|b` alternatives; `∅` none; `X:=P` alias of exact prior P; `X:P` predication; `P@C` context; `,` list; `;` siblings; `()` scope; `P?` question. Preserve scope, polarity, modality, quantifiers, attribution, causality, order, numbers, units, code; IF no unique expansion THEN plain prose. Spec: `../../references/VAE-DIALECT.md`.
