# Architecture: plugin payload

Everything in this folder is what a user installs; nothing else ships. It holds three host manifests, the skills, the hook adapter ([`hooks/`](hooks/ARCH.md)), the programs ([`scripts/`](scripts/ARCH.md)), the templates `init` copies ([`templates/`](templates/ARCH.md)) and the references: the prose catalog, the dialect and the consolidation rules that skills read on demand, and the stack defaults that session start and the gate draw from.

## Why this design

One folder serves Claude Code (`.claude-plugin/plugin.json`, hooks auto-loaded from `hooks/hooks.json`), Codex (`.codex-plugin/plugin.json`) and Agent Plugins hosts (`plugin.json`). Separate payloads per host would drift; a test keeps the three versions equal because Claude Code caches installs by version and an unbumped manifest never reaches users. Skills are prompts and stay small (each `SKILL.md` under 6 KiB, all seven under 28 KiB, both tested), because a skill loads whole on every invocation; long material lives in `references/` and is read only when a skill asks for it.

## How it works

Skills set `disable-model-invocation: true` for Claude Code and ship `agents/openai.yaml` with `allow_implicit_invocation: false` for Codex, whose skill docs name only that switch, so only a human starts them; `vae.py doctor` checks both. Each skill calls `scripts/vae.py` through the plugin root variable, and other hosts derive that root from the skill directory. Hooks run `hooks/lifecycle.py` with plain `python3`.

## Operations

- **Configuration:** none at the plugin level; projects configure the gate in their own `.agents/VERIFY.py`.
- **Deployment:** the release zip is this folder at the archive root (`make dist`); hosts unpack it into a per-version cache directory.
- **Compatibility:** `python3` 3.9 or newer, stdlib only, tested on 3.9 in CI; the sub-agent registry needs a POSIX host (`ps`, `flock`).

## Security and privacy

The payload contains no credentials, makes no network requests and collects no telemetry. Its attack surface is the hook input (JSON from the harness) and the project files it reads; see [`hooks/ARCH.md`](hooks/ARCH.md) and [`scripts/ARCH.md`](scripts/ARCH.md). No personal data.
