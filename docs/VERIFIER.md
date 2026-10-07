# Verifier and gate contract

## Required claims

For changes to code or doc pages (`*.md`, `*.mdx`, `*.markdown`; paths outside `.agents/`, `tmp/`, `var/`, `output/`), `VERIFIED[gate]=true` REQUIRES, in order:

1. **verify**: every required check passes:
   - `.agents/VERIFY.py` exists and loads (`init` writes it);
   - `prose` (unless `CONFIG["prose"]=False`): every changed doc page passes the static prose check (below);
   - `docs.pages` (strict only, see below): a `README.md` at the root and covering every folder that directly holds a changed interface (an executable script with a shebang, `__main__.py`, a package manifest with `bin` or a public API, OpenAPI, proto or GraphQL definitions), and an `ARCH.md` covering every folder that directly holds changed production source or deployment and schema definitions (Dockerfile, compose, `.tf`, `.sql`, `.proto`, GraphQL). A page covers its folder and every folder below it up to the next package manifest (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `setup.py`, Maven or Gradle build); a missing page is named at that boundary. Tests, fixtures, examples, docs, hidden folders, pure config and data need neither; `CONFIG["readme"|"arch"]` exempts more folders or drops a requirement;
   - `prose` on `README.md` and `ARCH.md` also rejects `HYPOTHESIS:`/`UNKNOWN:` labels (and VAE-DIALECT `HYPOTHESIS[x]` in prose): those pages state only verified facts;
   - `package` (strict only; `CONFIG["package"]=False` disables it): every changed `package.json` has `packageManager` (bun unless a foreign lockfile sits beside it), description, license, author and no template placeholder; a package new since `HEAD` also has `"type": "module"`, oxlint as a dependency (scripts calling it pass `--deny-warnings`) and, when it is a library (`exports`, `main` or `module` and not private), a pkgroll build;
   - `layout` (unless `CONFIG["layout"]=False`): Makefile verbs `setup start stop status log metrics bench test coverage lint e2e verify` exist and `var/`, `tmp/`, `.env` are gitignored (`init` writes these plus the other defaults);
   - `gitignore` (strict only, see below): `.gitignore` also covers `output/` and `dist/`, plus `node_modules/`, `coverage/`, `.cache/` when a `package.json` exists and `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.coverage` when Python is present, `target/` for Rust, `target/`, `build/`, `.gradle/` for the JVM and `bin/`, `obj/` for .NET (`init` appends them; `CONFIG["gitignore_exempt"]` lists lines a project may skip, e.g. a GitHub Action that commits `dist/`);
   - `toolchain` (unless `CONFIG["toolchain"]=False`): the change introduces no npm/yarn/pnpm/poetry/pipenv/pdm lockfile, and no pip `requirements.txt` without a `uv.lock`, that `HEAD` doesn't track, unless the manifest beside it (`package.json`, `pyproject.toml`, `Pipfile`) is tracked and no bun or uv lockfile is;
   - `env.example`: every env var that changed code reads directly (JS/TS `process.env`/`Bun.env`/`import.meta.env`, Python `os.environ`/`os.getenv`, Go `os.Getenv`, Rust `env::var`; OS-provided names exempt) or root `.env` sets is declared in the nearest `.env.example`, which is not gitignored;
   - `wiring` (always, also with `layout` off): `make verify` reaches every one of `lint test coverage e2e` the gate takes from the Makefile (prerequisites, transitively, or same-Makefile `$(MAKE) t` calls); verbs overridden in `CONFIG` are exempt;
   - repository has test files;
   - `make lint` exits 0 (or `CONFIG["lint_command"]`), run first as the cheapest failure;
   - `make test` exits 0 (or `CONFIG["test_command"]`);
   - every `make integration`/`make e2e` (or `CONFIG` command) exits 0, and the e2e run leaves a new or rewritten file in `output/`;
   - `make coverage` (or `CONFIG["coverage_command"]`) prints `TOTAL <n>%`, a `total:` or `| Total |` row, or an `All files |…|` table at ≥ `coverage_min` (default 60%);
   - built-in `hygiene.probes`: no temporary probe tag in changed code;
   - every project rule passes; a `glob` rule sees changed code files, or changed doc pages with `"docs": True`.
2. **review**: attestation for the current fingerprint covers every changed code path and doc page (pages against `plugin/references/PROSE.md`), the full checklist, and only resolved findings with location + evidence + learning.
3. **docs**: attestation assesses file/method/inline for every changed production file, with a plausible alternative and an epistemically prefixed rationale. With no production file changed, the step is complete: an attestation over zero files would prove nothing.

A session that changed only doc pages runs `verifier.config`, `docs.pages`, `prose` and the project rules, not lint, tests, coverage or e2e. The same holds for a page edit after the suites passed: suites are cached on code and policy, page checks on pages and policy, and with `CONFIG["e2e_paths"]` e2e on its scoped files and policy (see below).

`docs.pages`, `gitignore` and `package` arrived in 0.5.0 and fail existing repositories on their next change, so they block only where `CONFIG["strict"]=True`; elsewhere the report lists them under `WARNS:` with their fix, and the gate does not block on them. The template sets the key since 0.6.0, so new projects block from their first change, when no legacy needs fixing; this repository sets it too. Rejected: flipping the code default in 0.6.0, as 0.5.0 announced. `VERIFIED:` 0.5.x templates wrote `"strict": False` explicitly (commit f69b4f3) and `init` never overwrites `VERIFY.py`, so the flip would have caught only configs older than 0.5.0, and behavior would have depended on when a project was initialized. Unblocking takes one `vae.py init` for `.gitignore`, the missing `README.md` and `ARCH.md` pages, and the named `package.json` fields; `VERIFIED:` a new one-function JS library needed 3 ignore lines, an 18-line README, a 15-line ARCH page and 7 fields.

A missing command (lint, test, e2e, coverage) or metric is `UNKNOWN` and fails closed, even with `CONFIG["layout"]=False`. Any code or page edit changes the fingerprint and reopens the gate.

## Why doc pages are gated: a static check plus a catalog review

Pages enter the fingerprint, so a README-only session reaches the gate (`VERIFIED:` up to 0.3.1 `.md` files were not code, and nothing checked such a session). Two layers check them, because they catch different failures:

- `vae.py prose` (static, `vae_prose.py`) flags what a program can decide: machine-writing tells (em dash, spaced en dash used as a dash, curly quotes, `…`, a few high-precision English filler phrases), invisible and bidirectional control characters (checked inside code too, since they hide or reorder text), words mixing Latin, Cyrillic and Greek letters, glyph bullets that do not render as lists, unclosed fences, broken relative links, placeholders and Mermaid blocks without a known diagram type or with an unbalanced label quote. `--fix` applies only replacements that cannot change meaning (quotes, ellipsis, invisible characters, bullets); a dash needs a rewrite by meaning, so it stays a finding.
- The review reads each changed unit against `plugin/references/PROSE.md` (evidence, logic, precision, relevance, structure, style, typography, schematic content): the part no regex decides.

Why not normalize to ASCII, as tools like aslopcleaner do: non-ASCII is content (arrows and `∅` are VAE-DIALECT operators, `≥` and emoji are deliberate, other languages have their own quotation marks), and a blind swap can change meaning (an em dash becomes a hyphen inside a compound). `HYPOTHESIS:` a short list of tells plus per-page `CONFIG["prose"]["allow"]` gives fewer false positives than an ASCII allowlist; falsifier: a project needing more than a handful of `allow` entries for ordinary pages. The phrase list is English-only and deliberately short; `CONFIG["prose"]["phrases"]` adds a project's own (any language).

Why docs-only sessions skip the suites: a page edit cannot change what lint, tests or e2e prove, and rerunning them for a typo makes the gate slow enough that people route around it. `UNKNOWN:` pages that embed executable examples (doctest-style) are not run; such a project adds a `command` rule. Why `"docs": True` is opt-in for glob rules: existing `glob: "*"` code rules (no-mocks, probes) would otherwise fail on pages that explain them.

`VERIFIED:` the e2e drives a docs-only session through the installed release (`prose.fix`, `gate.docs_only.*`), and every page in this repository passes `vae.py prose`.

## Why README.md beside interfaces and ARCH.md beside production code

A README answers "how do I use this": it belongs where something can be used, a CLI or an API, and the root, whose `Makefile` is the developer interface every defuss-vae project has. Registries show a package folder's README, so a package without one ships undocumented. Interfaces are detected by how shells and registries find them (shebang plus executable bit, `__main__.py`, manifests, API definitions), not by guessing frameworks; an HTTP server without a spec gets its README through a `file_exists` rule.

An ARCH.md answers "why is it built this way and how does it behave in operation"; it is the wrong question for tests, fixtures, examples or a folder of config or data, so those need none. One page per package stays short enough to stay current, where one central document drifts and one page per leaf folder (every component directory) becomes sprawl nobody updates. A package is the unit that ships, deploys and is documented on its own, so coverage restarts at each manifest. Both pages state only verified facts, because readers act on them without the context to tell a guess from a measurement; open questions live in `docs/` until settled. `HYPOTHESIS:` per-folder pages stay more accurate than one central document because a change and its page are reviewed in the same gate; falsifier: ARCH.md pages that go stale while their folder changes. The check is existence plus the label floor; whether a page explains instead of repeating the README, and whether each claim is actually backed, is the catalog review's job. Like the glob rules it reaches a folder when a file in it changes, so an existing repository is not blocked all at once. `UNKNOWN:` whether every leaf folder of a deep package tree deserves its own page; `CONFIG["arch"]["exclude"]` exempts folders a parent page already covers.

## Why the package check splits new from existing packages

The defaults (bun, ESM, oxlint, pkgroll) are what a new JS/TS project starts on. For a package `HEAD` already tracks, switching the module type breaks CommonJS consumers, and swapping build tool or linter is a migration, so those stay proposals; metadata (`packageManager`, description, license, author) is additive and required everywhere. This mirrors the toolchain check: an existing toolchain is kept unless the human approves migrating.

## Why the gate text tells the agent not to hand off

The gate's job is to make the agent finish the work, so its text treats a failure as the agent's next task: apply the failing check's fix, rerun the gate, and never propose a bypass (`! git commit`, `--no-verify`, a `CONFIG` switch that disables a check) as the way out. Only a fix that names a human decision (keeping a foreign toolchain for a new project, for instance) may be asked for, once, after everything else is fixed. When a turn still ends with the gate open, the user-facing message names the failing checks, the exact gate command and what to reply, so the next turn resumes instead of stalling.

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

`VERIFIED:` the parser reads real output of bun 1.3.11 `bun test --coverage`, pytest-cov under `uv run`, c8 10 (istanbul text) and go 1.26 `go tool cover -func`. In `All files` tables it takes the last numeric column, which is % Lines for both bun (Funcs|Lines) and istanbul (Stmts|Branch|Funcs|Lines); the previous 4-column pattern returned nothing for bun. `VERIFIED:` (dotnet 9, xUnit) coverlet.msbuild prints `| Total | 100% | 100% | 100% |` with % Line first; the leading `|` used to hide it, which left the metric `UNKNOWN` and blocked every .NET project that printed it. `cargo llvm-cov` prints a `TOTAL` row whose first percentage is region coverage, stricter than lines. JaCoCo and Microsoft.Testing.Platform print no total, so `references/STACKS.md` gives one-liners that print `TOTAL <n>%`; `VERIFIED:` the JaCoCo one parses real Gradle 9.8 and Maven 3.9.16 reports.

`tests.exist` follows each ecosystem's naming, and when no file is named like a test it accepts a Rust source holding an inline `#[cfg(test)]` module. `VERIFIED:` `cargo new --lib` writes only such a module, and a root-level `Calc.Tests/MathTests.cs` (the usual .NET layout) was not recognized, so the gate blocked both with zero tests found. When a verb is missing, the hint cites the detected stack's line from `references/STACKS.md` instead of always suggesting `ruff` or `oxlint`.

## Why the toolchain check keys on `HEAD`

New projects start on bun (JS/TS) or uv (Python); existing repos keep their toolchain. A foreign lockfile that `HEAD` already tracks is an existing toolchain, and so is one whose manifest (`package.json`, `pyproject.toml`, `Pipfile`) `HEAD` tracks, unless a bun or uv lockfile is tracked beside that manifest: then the project is already on bun or uv and the foreign lockfile is a switch. Only a lockfile with an untracked manifest is the new-(sub)project case. `VERIFIED:` lockfiles count as code, so adding one after a cached pass still changes the fingerprint and re-runs the check.

`VERIFIED:` (a consumer session, 2026-10-05) keying on the lockfile alone deadlocked: an npm project with a never-committed `package-lock.json` failed `toolchain`, the fix was to commit the lockfile, and the commit gate denies every commit while the gate is open. The agent stopped and asked the human to commit by hand or disable the check. Keying on the manifest removes the deadlock, because the project's toolchain is already a fact of `HEAD`.

## Why `.env.example` is checked but log format and structure are not

`.env.example` is the only committed record of required config; a missing key breaks the next checkout, and completeness is decidable from reads + keys, so the gate enforces it. `UNKNOWN:` indirect reads (destructuring, config libraries, dynamic names) are not seen, so the check is a floor, not a proof.

Separation of concerns and log format are review items, not gate checks: proxies such as file-size limits or log-line regexes would push agents to game the proxy (arbitrary file splits) instead of improving structure.

## Why gate commands get the installer dirs on PATH

`VERIFIED:` GNU make 3.81 (macOS) execs a recipe without shell metacharacters (`uv run pytest`) directly, looking it up on make's *own* `PATH`; a Makefile `export PATH` reaches only recipes that run through a shell. So after `make setup` installs uv into `~/.local/bin` mid-session, the gate's `make test` failed with `uv: No such file or directory` until the verifier began appending `~/.local/bin` and `~/.bun/bin` to every command's `PATH`; the list now also holds mise's shims, `~/.cargo/bin`, `~/go/bin` and `~/.dotnet/tools`, so a Go, Rust, JDK or .NET tool installed by `mise install`, rustup, `go install` or `dotnet tool install` resolves the same way. Appending, not prepending, keeps a tool already on `PATH` (e.g. Homebrew's uv) in charge: the installer dirs are only a fallback. A failure that still names a missing uv/bun yields `AGENT_CMD: RUN: make setup`.

## Why CI installs the toolchain with the official setup actions

The scaffolded `.github/workflows/verify.yml` gets every tool `mise.toml` pins from `jdx/mise-action` (`VERIFIED:` it publishes floating major tags, `v5` included, and adds its shims to `PATH` for later steps), uv from `astral-sh/setup-uv` and bun from `oven-sh/setup-bun`, each guarded by `hashFiles` on its pin or lockfile, and only then runs the `CONFIG["ci"]` commands (default `make setup`, `make verify`). The earlier template relied on the project's `make setup` to install uv/bun and appended the installer dirs to `GITHUB_PATH` itself. That works only while the project keeps the template's `setup` recipe; a project that rewrote it, or an existing repo that never had it, failed CI with a missing tool. The actions put the tool on `PATH` for every later step and leave `make setup` the job of installing the locked dependencies. The lockfile guard means a uv-only project never downloads bun, and a toolchain added later needs no workflow edit. `VERIFIED:` (GitHub tags API, 2026-10-07) setup-uv publishes no floating major tag after `v7`, so `astral-sh/setup-uv@v10` resolved to nothing; the template pins the v10.2.0 release commit, as setup-uv's README pins a commit. actionlint works offline and cannot catch a missing ref. `VERIFIED:` `make lint` runs actionlint on the template, and `test_ci_scaffold_needs_a_github_remote_and_never_duplicates` asserts the default render is byte-identical to it. `UNKNOWN:` the template has not yet run on GitHub in a consumer project.

`init` writes no workflow when one already runs the project's verification. It parses the `make` targets of every invocation (comment lines dropped, `\` continuations joined, options and `VAR=value` skipped) and counts a workflow that runs `make verify`, all `CONFIG["ci"]` commands, or every verify verb either as `make <verb>` or as the command `CONFIG` declares for it. Matching the literal `make verify` would miss `make -j4 verify`, `make lint test coverage e2e` or a step running the project's own `uv run pytest`, and write a second, duplicate workflow next to them. `VERIFIED:` `test_existing_workflow_counts_when_it_runs_the_projects_verify_commands`. Parsing the text instead of the YAML keeps the module stdlib-only; `UNKNOWN:` a verb run through an expression (`make ${{ matrix.verb }}`) is not recognized, so such a repo should set `CONFIG["ci"] = False`.

## Why the gate is cheap enough for every turn

The Stop hook runs the gate at the end of every turn and PreToolUse runs before every Bash call, so their latency is paid constantly. `VERIFIED:` (`make bench`, 0.5.1 release installed into a small consumer project, medians of 7 runs, Apple M4, macOS 15.7.3, Python 3.14.3, 2026-10-05) PreToolUse on a non-commit command takes 19 ms, Python startup alone 16 ms; a cached gate takes 92 ms, a cold one 397 ms of which 307 ms are the consumer's own suites, so the gate's share is about 90 ms. Three choices keep it there: plain `python3` instead of `uv run` (5.4 ms more per call, see COMPATIBILITY.md); PreToolUse returns before importing the gate when a command does not contain `commit` (35 ms before that change); and the suites are cached per code fingerprint while page checks have their own key. `VERIFIED:` (this repository, 57 tracked files) a cached gate run spends 50 to 68 ms of its 53 to 80 ms in five `git` subprocesses, so further speed would come from fewer `git` calls, not from the Python. `UNKNOWN:` how the `git` cost grows in repositories with many thousands of changed files.

## Why a verification cache

`VERIFIED:` the fingerprint hashes every changed code file; `.agents/VERIFY.py` and `.gitignore` hashes join it in the cache key because they change verification results without being code. Identical key ⇒ identical inputs, so the review and docs loop turns skip re-running suites. `UNKNOWN:` environment drift (installed toolchain changes) is not part of the key; editing any code file or policy re-runs everything.

## Why e2e has its own cache key, and only on request

e2e is usually the slowest suite (a Playwright run builds the app and drives a browser), and without a scope any code edit reruns it, including a typo fix in a unit test. `CONFIG["e2e_paths"]` lists the globs (fnmatch, `*` crosses `/`) of the files the e2e depends on, e.g. `["web/*", "e2e/*"]`. The gate then keys e2e on those changed files, the build files (`Makefile`, manifests, lockfiles, bundler config) and the policy hashes. While that key matches the last passing e2e in the session, the report shows a passing `tests.e2e` check with `reused key=…` instead of running the commands. Lint, unit tests and coverage still run on every code edit, and an e2e that passed next to a failing unit test stays cached.

Why opt-in instead of a default heuristic such as "unit test files never affect e2e": e2e consumes the built artifact, so nearly any source file can change its result, and Playwright's default spec location `tests/*.spec.ts` looks like a unit test path. A guessed scope would skip e2e exactly where it should run; only the project knows which files cannot reach the artifact. A list that is not a non-empty list of strings counts as unset, so a mistake there makes the gate slower, never weaker. Build files always count because they decide what the artifact is.

`VERIFIED:` `test_e2e_paths_rerun_e2e_only_for_scoped_or_build_files` drives this through the gate: an unscoped edit reuses e2e, while a scoped file, a build file and a policy edit each rerun it. `test_without_e2e_paths_every_code_edit_reruns_e2e` covers the default. `UNKNOWN:` a scope that misses a real dependency (a shared module outside the globs) skips e2e wrongly; review is what catches a scope that is too narrow.

## Why state lives in `tmp/vae/` and logs in `var/log/vae/`

The agent and the hook must agree on attestation paths without environment plumbing, and writing inside the project avoids permission prompts that an out-of-project data directory triggers. Both directories write a `.gitignore` containing `*`, so they never reach `git status` even before the project adopts the layout.

Each session gets the folder `tmp/vae/<yyyy-mm-dd>_<hh_mm_ss>_<n>/`, named by its UTC start time; `n` is 1, or the next free number when two sessions start in the same second, which an atomic `mkdir` settles. The harness's session id is a UUID, and a directory of UUIDs reads like leaked keys to a person and to tools that scan paths for secrets, while a start time sorts and reads at a glance. `tmp/vae/sessions.tsv` maps each session id to its folder, one line per session, and the first line for an id wins if two processes of one session register at once. A line that names no dated folder is ignored, so a damaged index cannot send a write outside `tmp/vae/`. The gate prints the full attestation path in its instructions, so the agent never derives it. `VERIFIED:` `test_session_folders_are_named_by_start_time_and_keep_their_session` covers the naming, the next number in an occupied second, the mapping across processes and the damaged line; the e2e reads the path from the gate text, as an agent does. Folders made before this change keep their session-id names until removed, and a session that started under the old naming falls back to a fresh baseline in which every dirty file counts as changed.

## Learning

`VERIFIED:` a deterministic failure already represented by a failing test or rule needs no duplicate rule.

IF review finds a deterministic recurrence class with a VERIFIED root cause that is not yet encoded THEN the agent adds a regression test OR a `.agents/VERIFY.py` rule; IF encoding is not feasible THEN the finding's learning is `UNKNOWN` with a reason. Rules encode invariants, not taste. A failure caused by the environment, configuration or an external system gets a fix and startup validation, not a test: one that pins the values that worked once fails on every other correct setup. `glob` rules apply only to changed files so policy covers new work without blocking on untouched legacy code.

## Why the episode log never repeats a finding

The gate appends a `FINDING` line only if the same line is not already in `.agents/EPISODES.md`. A review attestation carried from one fingerprint to the next re-lists its earlier findings, and logging them again flooded the 100-entry window: `VERIFIED:` in this repository 71 of 100 entries were such repeats, which pushed older, distinct history out. `FAIL` lines are not deduplicated, because a failure that keeps recurring is exactly the signal wrap uses to promote a lesson into a test or rule.

## Why project-local Python

`VERIFIED:` the Python stdlib covers process execution, matching, JSON, git state and rule loading; hooks need no package installs.

`VERIFIED:` `.agents/VERIFY.py` is versioned and agent-editable, so learned constraints survive sessions and harnesses.

`UNKNOWN:` hosts without Python 3 cannot run the gates; they need an equivalent adapter. The skills themselves still work.
