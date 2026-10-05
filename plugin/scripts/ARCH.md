# Architecture: gate programs

The CLI (`vae.py`) and the modules behind the hooks: repository facts, static prose checks, the verifier, session state, the gate state machine, host adapters and project scaffolding. The full verify, review and docs contract is in [`docs/VERIFIER.md`](../../docs/VERIFIER.md).

## Why this design

Modules form a strict layer order, `vae_repo` → `vae_prose` → `vae_verify` → `vae_state` → `vae_gate` → `vae_hooks` → `vae_project`, and import only lower layers (tested). Pure checks (`vae_prose`, path classification in `vae_repo`) take text and paths, so tests drive them with real inputs and no mocks; I/O sits in `vae_verify` and above. The Makefile is the only command source: guessing runners per ecosystem would verify commands the project never committed to.

## How it works

`gate()` computes the changed paths since the session baseline, fingerprints the gated ones (code and doc pages), and walks verify → review → docs. Verify runs the project's `make` verbs and `.agents/VERIFY.py` rules, or only the page checks and rules when nothing but pages changed since the last green suite. Review and docs are JSON attestations keyed to the fingerprint, so any edit reopens the gate.

## Operations

- **Configuration and policy:** `.agents/VERIFY.py` (`CONFIG`, `RULES`), loaded fresh on every gate run and hashed into the verification cache key.
- **Complexity and resources:** a cached gate run takes about 90 ms, most of it five `git` subprocesses; a cold run adds the project's commands (`timeout_s`, default 180 s each); repository walks stop at 50,000 files; the suites are cached until code or policy change, the page checks until pages or policy change, so a README fix after a green suite reruns only the page checks.
- **Reliability:** a missing command or metric is `UNKNOWN` and fails; gate text over 9,000 characters is cut to head and tail, with the full text in `tmp/vae/<session>/gate.txt`.
- **Observability:** each command's output goes to `var/log/vae/<check>.log`; the gate appends `FAIL`, `DONE` and `FINDING` lines to `.agents/EPISODES.md` (last 100 kept).

## Security and privacy

Trust boundary: everything in the project is trusted as the user's own code. `VERIFY.py` is imported, Makefile verbs and `command` rules run in a shell. Nothing here opens a network connection. State and logs stay in gitignored `tmp/vae/` and `var/log/vae/`, which ignore themselves even before the project's `.gitignore` does. No personal data beyond what the project's own commands print.
