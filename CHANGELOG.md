# Changelog

## 0.3.1

- Stop hook: blocks once per turn, then ends the turn with a user-facing `systemMessage`. 0.3.0 answered repeat stops with `additionalContext`, which Claude Code also treats as a continuation, so a gate waiting on the human re-invoked the model up to the 8-continuation cap.

## 0.3.0

- Rules: a web frontend's e2e drives the served build in a real Playwright browser with WebGL2, network and permissions enabled.
- Gate (breaking for projects missing these): fails closed when `.agents/VERIFY.py` is missing, when `make verify` does not reach `lint test coverage e2e`, when no `lint` or `e2e` command exists (even with `CONFIG["layout"]=False`), and when a passing e2e leaves no fresh file in `output/`. The `verify` wiring check stays on with `CONFIG["layout"]=False`.
- Layout hint for libraries without a service: `init` for the ignores plus one `∅` stub line for `start stop restart status log`, instead of disabling `layout`.

## 0.1.0

First public release.