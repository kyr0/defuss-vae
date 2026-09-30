# Repository agent instructions

This repository implements defuss-vae itself.

- Prefer stdlib and minimal files.
- After code changes run `make test`, `make doctor`, `make e2e` (dogfood: installs the release zip and drives only its CLI + hook adapter). When changing verifier/hook logic also run `make coverage PYTHON=<python with coverage.py>` and keep total ≥60%.
- Hook behavior is correctness-critical; test fail-closed behavior, fingerprint invalidation, and Claude Code's once-per-turn Stop block against the current hooks docs.
- Tests use real git repos, processes and files; no mocks or monkeypatching.
- Keep each runtime `SKILL.md` <5.5 KiB and the pack <18.5 KiB; move provenance/background/examples to `docs/` or `references/`.
- Skill changes must preserve human-only invocation and the self-contained Signan core; `references/SIGNAN.md` is canonical for the full dialect. Uppercase in skill prose is operators/tags only (tested).
- Plugin code never contains the literal probe tag (tested); build it from parts, as `vae_core.PROBE_TAG` does.
- `implement` must retain Ponytail's understand-first + root-cause + YAGNI/reuse/stdlib/native/dependency/minimum-code discipline.
- Documentation explains WHY, with `VERIFIED:`, `HYPOTHESIS:`, `UNKNOWN:` for material claims.
- Prompt research rationale lives in `docs/PROMPT_DESIGN.md`; update it when changing prompt principles materially.
