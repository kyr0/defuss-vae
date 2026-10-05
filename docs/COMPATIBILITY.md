# Compatibility

| Host | Skills | Forced hooks | Notes |
|---|---:|---:|---|
| Claude Code | VERIFIED | VERIFIED | `plugin/.claude-plugin/plugin.json` + auto-loaded `plugin/hooks/hooks.json`; Stop blocks once per turn, the agent loops `vae.py gate` in-turn. |
| Codex plugin runtime | VERIFIED | HYPOTHESIS | Portable + legacy Codex manifests included; hook trust required. Stop `decision: "block"` parity with Claude Code is untested. |
| GitHub Copilot CLI / other plugin hosts accepting Claude-style packages | HYPOTHESIS | UNKNOWN | Skills are portable; lifecycle-hook parity depends on host version. |
| Generic Agent Skills hosts (Cursor/Gemini/etc.) | VERIFIED structure | UNKNOWN | Copy `plugin/skills/`; run `python3 plugin/scripts/vae.py gate --repo .` manually; `${CLAUDE_PLUGIN_ROOT}` is not substituted there (plugin root = skill dir/../..). |

## Python

`VERIFIED:` the unit suite (including the hook adapter run as a subprocess) passes on Python 3.8, 3.9, 3.10 (uv-managed) and 3.14, and the gate ran end to end under Apple's `/usr/bin/python3` 3.9.6. `make compat` keeps 3.9, the macOS Command Line Tools version, tested.

Hooks run on plain `python3`, not `uv run`. `VERIFIED:` `uv run` adds 5.4 ms (+17%) to a hook that fires before every Bash call, and a hook command that cannot start (uv missing) is a non-blocking error in Claude Code, i.e. fail-open. uv belongs in tooling (`make setup|compat|coverage|lint`), not in the enforcement path.

## Stop-hook semantics

`VERIFIED:` Claude Code hooks reference (checked 2026-10-05): Stop `decision: "block"` + `reason` and `hookSpecificOutput.additionalContext` both continue the turn, under one shared cap (8 consecutive continuations, then the turn ends). So the hook blocks only while `stop_hook_active` is false, and otherwise returns just a user-facing `systemMessage`. Before this, answering `stop_hook_active` with `additionalContext` (the 2026-09-30 reading said that ended the turn) looped the model up to the cap whenever the gate waited on a human; hook strings over 10,000 characters are replaced by a file path plus a 2,000-character preview. Gate text is capped well below that.

## Template Makefile process groups

`VERIFIED:` macOS `/bin/sh` (bash 3.2): `set -m` puts the background service in its own process group and `kill -s TERM -- -PGID` reaps grandchildren (`MakefileTemplateTests`, e2e `service.lifecycle`).

`VERIFIED:` `dash` refuses `set -m` without a tty ("can't access tty; job control turned off"), so the template prefers `setsid` when available.

`HYPOTHESIS:` Linux with util-linux or busybox `setsid` gives the same group semantics. Falsifier: the `ubuntu-latest` job of `.github/workflows/verify.yml`, whose `make test` runs the template test on whichever branch the platform takes; `UNKNOWN` until that workflow has run on GitHub.

## WHY two Codex hook declarations?

`VERIFIED:` OpenAI's Codex plugin docs keep `.codex-plugin/plugin.json` supported as a compatibility fallback next to the Agent Plugins root manifest ([Package your plugin](https://developers.openai.com/codex/plugins/build)).

`UNKNOWN:` whether every current Codex build loads hooks declared under `extensions.com.openai` in the root manifest.

Therefore the portable root manifest (`plugin/plugin.json`) stays free of the OpenAI extension and the overlay declares hooks explicitly, which works either way.
