# Changelog

## Unreleased

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