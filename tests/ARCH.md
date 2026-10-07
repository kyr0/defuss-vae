# Architecture: tests

Unit tests per module (`test_<module>.py`, mirroring `plugin/scripts/`), a shared fixture kit (`vae_testkit.py`) and the dogfood e2e (`e2e.py`). `make verify` runs them all, with coverage at 60 % or more, measured in subprocesses too (`.coveragerc`), since the hook adapter, the CLI and swarm jobs run as their own processes.

## Why this design

Tests use real git repositories, real processes and real files in temporary directories; no mocks or monkeypatching, so a test fails for the reasons a user would. The e2e installs the built release zip into a fresh consumer project and drives only the shipped CLI and hook adapter through the same JSON the harness sends, so it catches a broken payload that source-tree tests cannot see.

## How it works

`RepoCase` creates a temporary git repository per test; `make_python_project` adds a tiny project with its own `.agents/VERIFY.py`, README and ARCH page. The e2e writes its evidence to `output/e2e.json`; `make bench` reuses it to time the gate cold and cached.

## Operations

- **Resources:** the suite runs in about 40 s plus the e2e; CI runs it on macOS and Ubuntu and once on Python 3.9.
- **Reliability:** service tests poll for readiness instead of sleeping, swarm tests stop any job still running in `tearDown`, and the e2e service uses a raw socket listener because `VERIFIED:` `http.server` performs a reverse DNS lookup that took more than 30 s on a CI runner.

## Security and privacy

Tests run only local commands in temporary directories and use `example.invalid` identities for git. No network access is needed beyond `make setup`; no personal data.
