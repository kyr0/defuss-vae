# Verifier and gate contract

## Required claims

For changes to code or doc pages (`*.md`, `*.mdx`, `*.markdown`; paths outside `.agents/`, `tmp/`, `var/`, `output/`), `VERIFIED[gate]=true` REQUIRES, in order:

1. **verify**: every required check passes:
   - `.agents/VERIFY.py` exists and loads (`init` writes it);
   - `prose` (unless `CONFIG["prose"]=False`): every changed doc page passes the static prose check (below);
   - `layout` (unless `CONFIG["layout"]=False`): Makefile verbs `setup start stop status log metrics bench test coverage lint e2e verify` exist and `var/`, `tmp/`, `.env` are gitignored (`init` writes these plus the other defaults);
   - `toolchain` (unless `CONFIG["toolchain"]=False`): the change introduces no npm/yarn/pnpm/poetry/pipenv/pdm lockfile, and no pip `requirements.txt` without a `uv.lock`, that `HEAD` doesn't track;
   - `env.example`: every env var that changed code reads directly (JS/TS `process.env`/`Bun.env`/`import.meta.env`, Python `os.environ`/`os.getenv`, Go `os.Getenv`, Rust `env::var`; OS-provided names exempt) or root `.env` sets is declared in the nearest `.env.example`, which is not gitignored;
   - `wiring` (always, also with `layout` off): `make verify` reaches every one of `lint test coverage e2e` the gate takes from the Makefile (prerequisites, transitively, or same-Makefile `$(MAKE) t` calls); verbs overridden in `CONFIG` are exempt;
   - repository has test files;
   - `make lint` exits 0 (or `CONFIG["lint_command"]`), run first as the cheapest failure;
   - `make test` exits 0 (or `CONFIG["test_command"]`);
   - every `make integration`/`make e2e` (or `CONFIG` command) exits 0, and the e2e run leaves a new or rewritten file in `output/`;
   - `make coverage` (or `CONFIG["coverage_command"]`) prints `TOTAL <n>%` or an `All files |…|` table at ≥ `coverage_min` (default 60%);
   - built-in `hygiene.probes`: no temporary probe tag in changed code;
   - every project rule passes; a `glob` rule sees changed code files, or changed doc pages with `"docs": True`.
2. **review**: attestation for the current fingerprint covers every changed code path and doc page (pages against `plugin/references/PROSE.md`), the full checklist, and only resolved findings with location + evidence + learning.
3. **docs**: attestation assesses file/method/inline for every changed production file, with a plausible alternative and an epistemically prefixed rationale. With no production file changed, the step is complete: an attestation over zero files would prove nothing.

A session that changed only doc pages runs `verifier.config`, `prose` and the project rules, not lint, tests, coverage or e2e.

A missing command (lint, test, e2e, coverage) or metric is `UNKNOWN` and fails closed, even with `CONFIG["layout"]=False`. Any code or page edit changes the fingerprint and reopens the gate.

## Why doc pages are gated: a static check plus a catalog review

Up to 0.3.1, `.md` files were not code, so a README-only session never reached the gate and nothing checked it. Pages now enter the fingerprint, and two layers check them, because they catch different failures:

- `vae.py prose` (static, `vae_prose.py`) flags what a program can decide: machine-writing tells (em dash, spaced en dash used as a dash, curly quotes, `…`, a few high-precision English filler phrases), invisible and bidirectional control characters (checked inside code too, since they hide or reorder text), words mixing Latin, Cyrillic and Greek letters, glyph bullets that do not render as lists, unclosed fences, broken relative links, placeholders and Mermaid blocks without a known diagram type or with an unbalanced label quote. `--fix` applies only replacements that cannot change meaning (quotes, ellipsis, invisible characters, bullets); a dash needs a rewrite by meaning, so it stays a finding.
- The review reads each changed unit against `plugin/references/PROSE.md` (evidence, logic, precision, relevance, structure, style, typography, schematic content): the part no regex decides.

Why not normalize to ASCII, as tools like aslopcleaner do: non-ASCII is content (arrows and `∅` are Signan operators, `≥` and emoji are deliberate, other languages have their own quotation marks), and a blind swap can change meaning (an em dash becomes a hyphen inside a compound). `HYPOTHESIS:` a short list of tells plus per-page `CONFIG["prose"]["allow"]` gives fewer false positives than an ASCII allowlist; falsifier: a project needing more than a handful of `allow` entries for ordinary pages. The phrase list is English-only and deliberately short; `CONFIG["prose"]["phrases"]` adds a project's own (any language).

Why docs-only sessions skip the suites: a page edit cannot change what lint, tests or e2e prove, and rerunning them for a typo makes the gate slow enough that people route around it. `UNKNOWN:` pages that embed executable examples (doctest-style) are not run; such a project adds a `command` rule. Why `"docs": True` is opt-in for glob rules: existing `glob: "*"` code rules (no-mocks, probes) would otherwise fail on pages that explain them.

`VERIFIED:` the e2e drives a docs-only session through the installed release (`prose.fix`, `gate.docs_only.*`), and every page in this repository passes `vae.py prose`.

## Why the verifier must exist and be wired

A gate that silently falls back to defaults verifies less than the project thinks: without `.agents/VERIFY.py` the template rules (e.g. no mocks) never run, and before this check only `doctor` noticed. `make verify` is what CI runs while the gate runs the verbs one by one, so a `verify` that skips e2e passed every local gate and only CI was hollow. The wiring check parses the Makefile statically instead of asking make (`make -pn`): `VERIFIED:` with GNU Make 3.81 a dry run still evaluates `$(shell …)` and spawns the recursive `$(MAKE)` line (probed), so reading the wiring would run project code. `UNKNOWN:` whether `make -p` database output is stable enough across make 3.81 and 4.x to parse. `UNKNOWN:` prerequisites built from functions (`$(foreach …)`, `$(wildcard …)`) or computed variables are not expanded, so such a `verify` reports a gap; list the verbs literally.

`wiring` is its own check rather than part of `layout` because `layout` is the one check projects legitimately turn off, and turning it off must not make CI hollow again.

## Why a library keeps the layout instead of disabling it

`VERIFIED:` an agent on a library with no service hit `layout` gaps (`start stop status log metrics`, `var/*` and `.env` ignores) and proposed `CONFIG["layout"]=False` as the fix, because the gate's hint named no cheaper option. Disabling trades away the `var/`, `tmp/`, `.env` ignore checks, the part that protects secrets and runtime state, to avoid four Makefile verbs. The cheaper fix keeps both: `init` appends the missing ignore lines, and one line `start stop restart status log: ; @echo "∅ $@: no service"` covers the service verbs, as this repo's own Makefile does. The uniform interface survives too: `make status` answers "no service" definitively, where a missing target leaves the next agent guessing. The layout hint now names exactly the missing steps and ranks `layout=False` last.

## Why e2e must leave evidence in `output/`

Whether e2e consumes the built artifact is not decidable from outside, so the review checks it. The gate checks a decidable floor: the layout's consumer contract is `input/` → `output/`, so a passing e2e that creates or rewrites no file in `output/` (an `@true` stand-in, a source-tree import that asserts nothing) fails. `HYPOTHESIS:` this catches most stand-ins at the cost of one directory snapshot (mtime_ns + size); falsifier: a stand-in that writes to `output/` without running the artifact, which only review catches. Web frontends point Playwright's `outputDir`/reporter at `output/`.

## Why a gate CLI and not only the Stop hook

`VERIFIED:` (Claude Code hooks reference, checked 2026-10-05) `decision: "block"` and `hookSpecificOutput.additionalContext` on Stop both continue the turn, and `stop_hook_active` marks a continuation. The hook blocks only when `stop_hook_active` is false, so it forces one continuation per turn and never loops. A loop could not tell a fixable failure from one that waits on the human, and Claude Code cuts it off at 8 continuations anyway.

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
