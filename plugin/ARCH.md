# Architecture: plugin payload

Everything in this folder is what a user installs; nothing else ships. It holds three host manifests, the skills, the hook adapter ([`hooks/`](hooks/ARCH.md)), the programs ([`scripts/`](scripts/ARCH.md)), the templates `init` copies ([`templates/`](templates/ARCH.md)) and on-demand references for the skills.

## Why this design

One folder serves Claude Code (`.claude-plugin/plugin.json`, hooks auto-loaded from `hooks/hooks.json`), Codex (`.codex-plugin/plugin.json`) and Agent Plugins hosts (`plugin.json`). Separate payloads per host would drift; a test keeps the three versions equal because Claude Code caches installs by version and an unbumped manifest never reaches users. Skills are prompts and stay small (each `SKILL.md` under 5.5 KiB, all five under 21.5 KiB, both tested), because a skill loads whole on every invocation; long material lives in `references/` and is read only when a skill asks for it.

## How it works

Skills set `disable-model-invocation: true`, so only a human starts them. Each skill calls `scripts/vae.py` through the plugin root variable, and other hosts derive that root from the skill directory. Hooks run `hooks/lifecycle.py` with plain `python3`.

## Operations

- **Configuration:** none at the plugin level; projects configure the gate in their own `.agents/VERIFY.py`.
- **Deployment:** the release zip is this folder at the archive root (`make dist`); hosts unpack it into a per-version cache directory.
- **Compatibility:** `python3` 3.9 or newer, stdlib only, tested on 3.9 in CI.

## Security and privacy

The payload contains no credentials, makes no network requests and collects no telemetry. Its attack surface is the hook input (JSON from the harness) and the project files it reads; see [`hooks/ARCH.md`](hooks/ARCH.md) and [`scripts/ARCH.md`](scripts/ARCH.md). No personal data.
