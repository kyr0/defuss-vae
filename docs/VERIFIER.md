# Verifier and gate contract

## Required claims

For code changes (paths outside `.agents/`, `tmp/`, `var/`, `output/`), `VERIFIED[gate]=true` REQUIRES, in order:

1. **verify** — every required check passes:
   - `.agents/VERIFY.py` loads;
   - `layout` (unless `CONFIG["layout"]=False`): Makefile verbs `start stop status log metrics bench test e2e` exist and `var/log/`, `tmp/` are gitignored;
   - repository has test files;
   - test command exits 0 (`make test` first, then ecosystem autodiscovery or `CONFIG`);
   - every integration/e2e command exits 0 (`make integration`/`make e2e` first);
   - measured coverage ≥ `coverage_min` (default 60%);
   - built-in `hygiene.probes`: no temporary probe tag in changed code;
   - every project rule passes.
2. **review** — attestation for the current fingerprint covers every changed code path, the full checklist, and only resolved findings with location + evidence + learning.
3. **docs** — attestation assesses file/method/inline for every changed production file, with a plausible alternative and an epistemically prefixed rationale.

A missing command or metric is `UNKNOWN` and fails closed. Any code edit changes the fingerprint and reopens the gate.

## Why a gate CLI and not only the Stop hook

`VERIFIED:` (Claude Code hooks reference, checked 2026-09-30) a Stop hook can block once per turn; `hookSpecificOutput.additionalContext` on Stop does not continue the turn. v0.2.0 returned only `additionalContext`, so its "forced" gates never forced a continuation; only the commit gate enforced anything.

Therefore the Stop hook blocks once with the gate text, which carries the exact `vae.py gate --repo … --session …` command; the agent loops that command in-turn. The commit gate remains the hard backstop.

## Why a verification cache

`VERIFIED:` the fingerprint hashes every changed code file; `.agents/VERIFY.py` and `.gitignore` hashes join it in the cache key because they change verification results without being code. Identical key ⇒ identical inputs, so the review and docs loop turns skip re-running suites. `UNKNOWN:` environment drift (installed toolchain changes) is not part of the key; editing any code file or policy re-runs everything.

## Why state lives in `tmp/vae/` and logs in `var/log/vae/`

The agent and the hook must agree on attestation paths without environment plumbing, and writing inside the project avoids permission prompts that an out-of-project data directory triggers. Both directories write a `.gitignore` containing `*`, so they never reach `git status` even before the project adopts the layout.

## Learning

`VERIFIED:` a deterministic failure already represented by a failing test or rule needs no duplicate rule.

IF review finds a deterministic recurrence class that is not yet encoded THEN the agent adds a regression test OR a `.agents/VERIFY.py` rule; IF encoding is not feasible THEN the finding's learning is `UNKNOWN` with a reason. Rules encode invariants, not taste. `glob` rules apply only to changed files so policy covers new work without blocking on untouched legacy code.

## Why project-local Python

`VERIFIED:` the Python stdlib covers process execution, matching, JSON, git state and rule loading; hooks need no package installs.

`VERIFIED:` `.agents/VERIFY.py` is versioned and agent-editable, so learned constraints survive sessions and harnesses.

`UNKNOWN:` hosts without Python 3 cannot run the gates; they need an equivalent adapter. The skills themselves still work.
