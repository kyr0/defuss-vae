# Changelog

## 0.5.5

- Memory: a memory entry binds only inside its evidenced `[scope]` and below the current request; a contested fact (memory against the repo, say) is observed before editing, a disproved entry is narrowed, rewritten or dropped, and rhetoric, repetition, recency or detail never promote or widen a claim. The `MEMORY.md` template and `references/CONSOLIDATION.md` define `[scope]` as the narrowest boundary the evidence supports; finalize promotes a lesson at that scope and narrows entries wider than their evidence. The always-on rules stay the same size.
- Session start injects at most three open episodes instead of the last three lines: `LESSON` lines, findings learned nowhere else (`learn=none`) and `FAIL`s their session never turned green, newest first, labeled as leads and sharing 1024 characters equally. `MEMORY.md` and `CLI_GIST.md` injection is unchanged.
- Skills: `implement` greps `.agents/EPISODES.md` for touched paths, symbols or symptoms; `review` reads `AGENTS.md`, `.agents/MEMORY.md`, `.agents/CLI_GIST.md` and `.agents/VERIFY.py` and greps `EPISODES.md` for changed paths, symbols or symptoms instead of reading all of `.agents/`; `finalize` still reads every episode. `implement` step 8 now points to the gate's docs step, which states the same rule.

## 0.5.4

- Gate: `CONFIG["e2e_paths"]` (globs, e.g. `["web/*"]`) reruns e2e only when a matching file, a build file (`Makefile`, manifests, lockfiles) or the policy changed since e2e last passed in the session; lint, unit tests and coverage still run on every code edit. Without it, any code edit reruns e2e as before.

## 0.5.3

- finalize: audits every memory, episode and agent-added AGENTS.md entry against the current repo and removes or rewrites one only with evidence; unsure entries stay, retagged `UNKNOWN`; human-written content is only proposed for change. Procedure in `references/CONSOLIDATION.md`.
- doctor: non-blocking `state.stale` lists memory entries that cite repo paths which no longer exist.
- Fix: the gate logs each distinct finding once; a review carried across fingerprints had re-logged its findings (71 of 100 episode entries in this repository).

## 0.5.2

- Hooks: PreToolUse returns before loading the gate for a command that cannot be a commit, cutting the cost per Bash call from 35 ms to 19 ms (Apple M4). A gate module that fails to import now denies the commit instead of crashing the hook.
- `make bench` reports medians for every hook and gate path, plus the consumer's own suite time, so the gate's overhead is visible; the numbers are in the README.

## 0.5.1

- Rules: CI runs async; after a push the agent reports the run URL and finishes instead of waiting for CI.

## 0.5.0

- Renamed the engineering dialect from Signan to VAE-DIALECT; its spec moved from `references/SIGNAN.md` to `references/VAE-DIALECT.md`, and each skill's core section is now `## VAE-DIALECT core`.
- Gate, warning until 0.6.0 (`CONFIG["strict"]=True` blocks now): `docs.pages` requires `README.md` at the root and for every package with a changed CLI or API (executable scripts, `__main__.py`, package manifests, API definitions), and `ARCH.md` for every package with changed production code or deployment and schema definitions. A page covers the folders below it up to the next package manifest. Templates `README.md.tmpl` (with badges) and `ARCH.md.tmpl`; exempt folders via `CONFIG["readme"|"arch"]["exclude"]`.
- Prose check: `README.md` and `ARCH.md` state only verified facts; `HYPOTHESIS:`/`UNKNOWN:` labels there are findings.
- Gate, warning until 0.6.0: `package` checks every changed `package.json`: `packageManager` (bun), description, license, author; new packages also `"type": "module"`, oxlint with `--deny-warnings`, and pkgroll for libraries. Template `package.json.tmpl`; configure or disable via `CONFIG["package"]`.
- Gate, warning until 0.6.0: a new `gitignore` check wants `output/` and `dist/`, and the cache and package folders of each toolchain present (`node_modules/`, `coverage/`, `.cache/` for JS; `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.coverage` for Python). `init` appends them; `CONFIG["gitignore_exempt"]` skips a line.
- Fix: an existing npm, poetry or pipenv project with a never-committed lockfile no longer fails `toolchain`; a tracked manifest beside it marks the toolchain as existing, while a project with a tracked bun or uv lockfile still fails on a new foreign one. This removes a deadlock where the fix (commit the lockfile) was blocked by the commit gate.
- Gate text: tells the agent to fix a failing check itself instead of ending the turn or proposing a bypass; the user-facing message on a repeat stop names the failing checks, the gate command and what to reply.
- Rules: tests use throwaway real systems, never live or production data, with Pareto coverage; e2e reaches every UI page, route or component and every CLI command or API endpoint at least once.
- Gate: suites are cached on code and policy, page checks on pages and policy, so a page edit after a green suite reruns only the page checks.
- Session rules carry principles only; the failing check's hint carries the exact gaps and template.
- Prose check: badge links (`[![alt](img)](target)`) have their outer target checked too.

## 0.4.0

- Docs skill (`/defuss-vae:docs`): grounded claims, the universal prose catalog (`references/PROSE.md`), Mermaid for schematic content, page rules declared before writing.
- Gate: doc pages (`*.md`, `*.mdx`, `*.markdown`) are gated. A docs-only session runs the static prose check, project rules and a catalog review, and skips the test suites. The docs attestation step is skipped when no production source changed.
- `vae.py prose [--fix]`: flags machine-writing tells, invisible or look-alike characters, broken links, fences and Mermaid blocks; `--fix` applies only meaning-preserving replacements. Configure per page with `CONFIG["prose"]`; `glob` rules reach pages with `"docs": True`.

## 0.3.1

- Stop hook: blocks once per turn, then ends the turn with a user-facing `systemMessage`. 0.3.0 answered repeat stops with `additionalContext`, which Claude Code also treats as a continuation, so a gate waiting on the human re-invoked the model up to the 8-continuation cap.

## 0.3.0

- Rules: a web frontend's e2e drives the served build in a real Playwright browser with WebGL2, network and permissions enabled.
- Gate (breaking for projects missing these): fails closed when `.agents/VERIFY.py` is missing, when `make verify` does not reach `lint test coverage e2e`, when no `lint` or `e2e` command exists (even with `CONFIG["layout"]=False`), and when a passing e2e leaves no fresh file in `output/`. The `verify` wiring check stays on with `CONFIG["layout"]=False`.
- Layout hint for libraries without a service: `init` for the ignores plus one `∅` stub line for `start stop restart status log`, instead of disabling `layout`.

## 0.1.0

First public release.