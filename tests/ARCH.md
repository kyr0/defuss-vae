# Architecture: tests

Unit tests per module (`test_<module>.py`, mirroring `plugin/scripts/`), a shared fixture kit (`vae_testkit.py`), the dogfood e2e (`e2e.py`) and the browser e2e of the website (`site/`, a bun project). `make verify` runs them all, with coverage at 60 % or more, measured in subprocesses too (`.coveragerc`), since the hook adapter, the CLI and swarm jobs run as their own processes.

## Why this design

Tests use real git repositories, real processes and real files in temporary directories; no mocks or monkeypatching, so a test fails for the reasons a user would. The e2e installs the built release zip into a fresh consumer project and drives only the shipped CLI and hook adapter through the same JSON the harness sends, so it catches a broken payload that source-tree tests cannot see.

The website needs a real browser, because its layout and its component behavior exist only there. `VERIFIED:` during the site's development (2026-10-09) the hero widened the page past a 390 px viewport while `scrollWidth` stayed clean, since the page clips horizontal overflow; the site e2e measures elements against the viewport instead and fails when that bug is put back. It drives the installed Google Chrome through `playwright-core`, so no run downloads a browser.

## How it works

`RepoCase` creates a temporary git repository per test; `make_python_project` adds a tiny project with its own `.agents/VERIFY.py`, README and ARCH page. The e2e writes its evidence to `output/e2e.json`; `make bench` reuses it to time the gate cold and cached.

`site/e2e.mjs` serves `docs/` with `Bun.serve` on a free port of 127.0.0.1 and checks the page at desktop width in light and dark and at phone width: clean loading (no console error, failed request or HTTP error), in-page anchors and icons that resolve, the diagram playing to its last step, both scroll scenes showing their six phrases in order, each install tab and copy button, the dark-mode switch across a reload, the phone menu and the scroll-to-top button. It writes `output/site-e2e.json` and screenshots to `output/site/`.

## Operations

- **Resources:** the suite runs in about 40 s plus the e2e, and the site e2e in about 18 s; CI runs them on macOS and Ubuntu, the suite once more on Python 3.9. The site e2e needs Google Chrome installed and `make setup` for its locked dependencies.
- **Reliability:** service tests poll for readiness instead of sleeping, swarm tests stop any job still running in `tearDown`, and the e2e service uses a raw socket listener because `VERIFIED:` `http.server` performs a reverse DNS lookup that took more than 30 s on a CI runner.

## Security and privacy

The Python tests run only local commands in temporary directories and use `example.invalid` identities for git. The site e2e serves `docs/` on 127.0.0.1 only and loads defuss-shadcn from `cdn.jsdelivr.net`, as a visitor's browser does; beyond that and `make setup`, no test needs the network. No personal data.
