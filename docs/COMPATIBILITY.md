# Compatibility

| Host | Skills | Forced hooks | Notes |
|---|---:|---:|---|
| Claude Code | VERIFIED | VERIFIED | `.claude-plugin/plugin.json` + `hooks/hooks.json`; Stop blocks once per turn, the agent loops `vae.py gate` in-turn. |
| Codex plugin runtime | VERIFIED | HYPOTHESIS | Portable + legacy Codex manifests included; hook trust required. Stop `decision: "block"` parity with Claude Code is untested. |
| GitHub Copilot CLI / other plugin hosts accepting Claude-style packages | HYPOTHESIS | UNKNOWN | Skills are portable; lifecycle-hook parity depends on host version. |
| Generic Agent Skills hosts (Cursor/Gemini/etc.) | VERIFIED structure | UNKNOWN | Copy `skills/`; run `python3 scripts/vae.py gate --repo .` manually; `${CLAUDE_PLUGIN_ROOT}` is not substituted there (plugin root = skill dir/../..). |

## Stop-hook semantics

`VERIFIED:` Claude Code hooks reference (checked 2026-09-30): Stop `decision: "block"` + `reason` continues the turn once; `hookSpecificOutput.additionalContext` on Stop leaves the turn finished; hook strings over 10,000 characters are replaced by a file path plus a 2,000-character preview. Gate text is capped well below that.

## Template Makefile process groups

`VERIFIED:` macOS `/bin/sh` (bash 3.2): `set -m` puts the background service in its own process group and `kill -s TERM -- -PGID` reaps grandchildren (`MakefileTemplateTests`, e2e `service.lifecycle`).

`VERIFIED:` `dash` refuses `set -m` without a tty ("can't access tty; job control turned off"), so the template prefers `setsid` when available.

`HYPOTHESIS:` Linux with util-linux or busybox `setsid` gives the same group semantics; falsifier: run `make test` on Linux (the template test executes whichever branch the platform takes).

## WHY two Codex hook declarations?

`VERIFIED:` OpenAI's current plugin docs retain `.codex-plugin/plugin.json` as the compatibility source when root `extensions.com.openai` is absent.

`VERIFIED:` an open Codex issue dated September 2026 reports builds where Agent Plugins 1.0 root hook declarations are not loaded.

Therefore the portable root manifest intentionally omits the OpenAI extension and the compatibility overlay declares hooks until that runtime defect is resolved.
