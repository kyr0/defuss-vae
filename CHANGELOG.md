# Changelog

## Unreleased

### Fixed

- Stop gate now actually continues the agent: it returns `decision: "block"` (once per turn, the Claude Code limit) instead of `additionalContext`, which ends the turn. The gate text carries a `vae.py gate --repo … --session …` command that the agent loops in-turn until `VERIFIED[gate]=true`.
- Verifier no longer crashes when a command times out after printing output (`TimeoutExpired.stdout` is bytes even with `text=True`); the crash made the Stop hook fail open.
- Hook adapter fails closed on internal errors: commits are denied and the stop is blocked with the error.
- Commit gate also catches `git -c …`, `git --no-pager …` and similar global-option forms.
- Release zip no longer ships `.DS_Store` files or runtime directories.

### Added

- Strict project layout: `vae.py init` scaffolds `.agents/{VERIFY.py,MEMORY.md,CLI_GIST.md,EPISODES.md}`, a Makefile with LSB-style `start stop restart status log` (own process group, redirected stdio, whole-group stop) and fail-closed `metrics bench test e2e`, plus gitignored `var/log/` and `tmp/`. New required `layout` check (`CONFIG["layout"]=False` opts out).
- Makefile-first discovery of `test`, `coverage`, `integration`, `e2e`.
- Evidence loop: temporary probes carry a tag, and the built-in `hygiene.probes` check fails while any remain in changed code.
- `glob` scope for text rules (every changed code file); template rule `tests.no-mocks`.
- Episodic log `.agents/EPISODES.md`: the gate appends `FAIL` (deduplicated), `DONE` and one `FINDING` per review finding, and keeps the last 100 entries.
- Session start injects MEMORY and CLI gist entries plus the last 3 episodes; `vae.py doctor --repo .` enforces memory budgets (4 KiB / 2 KiB), epistemic tags and layout.
- Verification cache keyed by code fingerprint + policy hashes, so review/docs loop turns skip re-running suites.
- Dogfood `make e2e` for this repo: installs the release zip and drives only its CLI and hook adapter; `make bench`, `make metrics`, `make coverage`.

### Changed

- Skills rewritten in Signan: evidence loop, layout, proof programs, memory hierarchy and gate loop added at +0.6% pack size. Signan gains `ELSE`, infix `REQUIRES`, `→`, `>`, `|`, `∅` and uppercase-only-for-operators; a test enforces it on the skills.
- Gate state and attestations moved from the plugin data dir into self-ignored `tmp/vae/<session>/`; command outputs go to `var/log/vae/<check>.log`, and passing checks report one line.
- `finalize-init` → `init` (old name kept as alias); `make package` builds `output/defuss-vae-<version>.zip` via `make dist`.
- Review checklist adds `e2e` and `observability`.

### Removed

- Dead code: `cmd_ok`, `DOC_EXT`, unused `shutil` import, unused `coverage_format` plumbing.

## 0.2.0 - 2026-09-29

- Rebuilt all four skill prompts from current Anthropic Claude prompting/code-review guidance, Ponytail, Superpowers verification/TDD patterns, and Conventional Commits.
- Added a real Ponytail operating prompt to `implement`: understand/trace first, root-cause + sibling-caller inspection, YAGNI→reuse→stdlib→native→installed dependency→one line→minimum code, plus RED→GREEN→REFACTOR for non-trivial behavior.
- Added a complete portable Signan grammar reference and a self-contained Signan core to every skill; Signan is no longer an undefined always-on dialect.
- Strengthened forced review: requirements/current diff/changed code/callers/tests context, correctness-first + Ponytail passes, changed-path coverage in the review attestation, and concrete location/evidence for every finding.
- Strengthened docs gate: file/method/inline assessment now requires a plausible alternative (or concrete non-applicability reason) so WHY-vs-alternative is explicit.
- Added `docs/PROMPT_DESIGN.md` with research provenance and deliberate exclusions.

## 0.1.0 - 2026-09-28

- Initial `plan`, `implement`, `review`, `finalize` human-only skills.
- Added deterministic Stop verification with 60% minimum coverage, learned project rules, review/docs gates, and pre-commit enforcement.
- Added portable, Claude Code, and Codex plugin manifests.
