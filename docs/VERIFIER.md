# Verifier and gate contract

## Required claims

For code changes (paths outside `.agents/`, `tmp/`, `var/`, `output/`), `VERIFIED[gate]=true` REQUIRES, in order:

1. **verify** — every required check passes:
   - `.agents/VERIFY.py` exists and loads (`init` writes it);
   - `layout` (unless `CONFIG["layout"]=False`): Makefile verbs `setup start stop status log metrics bench test coverage lint e2e verify` exist, `verify` reaches `lint test coverage e2e` (prerequisites, transitively, or same-Makefile `$(MAKE) t` calls), and `var/`, `tmp/`, `.env` are gitignored (`init` writes these plus the other defaults);
   - `toolchain` (unless `CONFIG["toolchain"]=False`): the change introduces no npm/yarn/pnpm/poetry/pipenv/pdm lockfile, and no pip `requirements.txt` without a `uv.lock`, that `HEAD` doesn't track;
   - `env.example`: every env var that changed code reads directly (JS/TS `process.env`/`Bun.env`/`import.meta.env`, Python `os.environ`/`os.getenv`, Go `os.Getenv`, Rust `env::var`; OS-provided names exempt) or root `.env` sets is declared in the nearest `.env.example`, which is not gitignored;
   - repository has test files;
   - `make lint` exits 0 (or `CONFIG["lint_command"]`), run first as the cheapest failure;
   - `make test` exits 0 (or `CONFIG["test_command"]`);
   - every `make integration`/`make e2e` (or `CONFIG` command) exits 0, and the e2e run leaves a new or rewritten file in `output/`;
   - `make coverage` (or `CONFIG["coverage_command"]`) prints `TOTAL <n>%` or an `All files |…|` table at ≥ `coverage_min` (default 60%);
   - built-in `hygiene.probes`: no temporary probe tag in changed code;
   - every project rule passes.
2. **review** — attestation for the current fingerprint covers every changed code path, the full checklist, and only resolved findings with location + evidence + learning.
3. **docs** — attestation assesses file/method/inline for every changed production file, with a plausible alternative and an epistemically prefixed rationale.

A missing command (lint, test, e2e, coverage) or metric is `UNKNOWN` and fails closed, even with `CONFIG["layout"]=False`. Any code edit changes the fingerprint and reopens the gate.

## Why the verifier must exist and be wired

A gate that silently falls back to defaults verifies less than the project thinks: without `.agents/VERIFY.py` the template rules (e.g. no mocks) never run, and before this check only `doctor` noticed. `make verify` is what CI runs while the gate runs the verbs one by one, so a `verify` that skips e2e passed every local gate and only CI was hollow. The wiring check parses the Makefile statically instead of asking make (`make -pn`): `VERIFIED:` with GNU Make 3.81 a dry run still evaluates `$(shell …)` and spawns the recursive `$(MAKE)` line (probed), so reading the wiring would run project code. `UNKNOWN:` whether `make -p` database output is stable enough across make 3.81 and 4.x to parse. `UNKNOWN:` prerequisites built from functions (`$(foreach …)`, `$(wildcard …)`) or computed variables are not expanded, so such a `verify` reports a gap; list the verbs literally.

## Why e2e must leave evidence in `output/`

Whether e2e consumes the built artifact is not decidable from outside, so the review checks it. The gate checks a decidable floor: the layout's consumer contract is `input/` → `output/`, so a passing e2e that creates or rewrites no file in `output/` (an `@true` stand-in, a source-tree import that asserts nothing) fails. `HYPOTHESIS:` this catches most stand-ins at the cost of one directory snapshot (mtime_ns + size); falsifier: a stand-in that writes to `output/` without running the artifact, which only review catches. Web frontends point Playwright's `outputDir`/reporter at `output/`.

## Why a gate CLI and not only the Stop hook

`VERIFIED:` (Claude Code hooks reference, checked 2026-09-30) a Stop hook can block once per turn; `hookSpecificOutput.additionalContext` on Stop does not continue the turn. A hook-only gate therefore gets one forced continuation per turn, not a loop.

Therefore the Stop hook blocks once with the gate text, which carries the exact `vae.py gate --repo … --session …` command; the agent loops that command in-turn. The commit gate remains the hard backstop.

## Why no ecosystem autodiscovery

`VERIFIED:` the `layout` check already requires the Makefile verbs, so guessing runners per ecosystem (npm/pnpm/yarn/bun scripts, pytest, go, cargo, maven, gradle) duplicated the declared interface and could verify commands the project never committed to; removing it cut the core from 1118 to 942 lines. Commands run in the repo root, so they need no placeholders.

`VERIFIED:` the parser reads real output of bun 1.3.11 `bun test --coverage`, pytest-cov under `uv run`, c8 10 (istanbul text) and go 1.26 `go tool cover -func`. In `All files` tables it takes the last numeric column, which is % Lines for both bun (Funcs|Lines) and istanbul (Stmts|Branch|Funcs|Lines); the previous 4-column pattern returned nothing for bun.

## Why the toolchain check keys on `HEAD`

New projects start on bun (JS/TS) or uv (Python); existing repos keep their toolchain. A foreign lockfile that `HEAD` already tracks is an existing toolchain; one `HEAD` lacks is being introduced by this change, which is the new-(sub)project case. `VERIFIED:` lockfiles count as code, so adding one after a cached pass still changes the fingerprint and re-runs the check. `UNKNOWN:` an existing project whose lockfile was never committed looks new; the human commits it or sets `CONFIG["toolchain"]=False`.

## Why `.env.example` is checked but log format and structure are not

`.env.example` is the only committed record of required config; a missing key breaks the next checkout, and completeness is decidable from reads + keys, so the gate enforces it. `UNKNOWN:` indirect reads (destructuring, config libraries, dynamic names) are not seen, so the check is a floor, not a proof.

Separation of concerns and log format are review items, not gate checks: proxies such as file-size limits or log-line regexes would push agents to game the proxy (arbitrary file splits) instead of improving structure.

## Why gate commands get the installer dirs on PATH

`VERIFIED:` GNU make 3.81 (macOS) execs a recipe without shell metacharacters (`uv run pytest`) directly, looking it up on make's *own* `PATH`; a Makefile `export PATH` reaches only recipes that run through a shell. So after `make setup` installs uv into `~/.local/bin` mid-session, the gate's `make test` failed with `uv: No such file or directory` until the verifier began appending `~/.local/bin` and `~/.bun/bin` to every command's `PATH`. Appending, not prepending, keeps a tool already on `PATH` (e.g. Homebrew's uv) in charge: the installer dirs are only a fallback. A failure that still names a missing uv/bun yields `AGENT_CMD: RUN: make setup`.

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
