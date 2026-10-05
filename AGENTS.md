# Repository agent instructions

This repository implements defuss-vae itself. `plugin/` is exactly what users install; keep maintainer-only files (tests, docs, tooling) at the repo root.

- Prefer stdlib and minimal files.
- Fresh clone: `make setup` (installs uv if missing). After code changes run `make verify` (lint with pinned ruff + actionlint, test, compat on Python 3.9, coverage ≥60%, doctor, e2e; e2e installs the release zip and drives only its CLI + hook adapter). Hooks stay stdlib-only on plain `python3` ≥ 3.9: never `uv run` in hooks (fail-open if uv is missing).
- Hook behavior is correctness-critical; test fail-closed behavior, fingerprint invalidation, and Claude Code's once-per-turn Stop block against the current hooks docs.
- Tests use real git repos, processes and files; no mocks or monkeypatching. `plugin/scripts/` modules import only lower layers (`vae_repo` → `vae_prose` → `vae_verify` → `vae_state` → `vae_gate` → `vae_hooks` → `vae_project`); tests mirror them.
- Keep each runtime `SKILL.md` <5.5 KiB and the pack <21.5 KiB; move provenance/background/examples to `docs/` or `plugin/references/`.
- Skill changes must preserve human-only invocation and the self-contained VAE-DIALECT core; `plugin/references/VAE-DIALECT.md` is canonical for the full dialect. Uppercase in skill prose is operators/tags only (tested).
- Plugin code never contains the literal probe tag (tested); build it from parts, as `vae_verify.PROBE_TAG` does.
- `implement` must retain Ponytail's understand-first + root-cause + YAGNI/reuse/stdlib/native/dependency/minimum-code discipline.
- Documentation explains WHY, with `VERIFIED:`, `HYPOTHESIS:`, `UNKNOWN:` for material claims.
- Prompt research rationale lives in `docs/PROMPT_DESIGN.md`; update it when changing prompt principles materially.
