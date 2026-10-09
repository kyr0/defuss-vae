# Architecture: plugin payload

Everything in this folder is what a user installs; nothing else ships. It holds three host manifests, the skills, the hook adapter ([`hooks/`](hooks/ARCH.md)), the programs ([`scripts/`](scripts/ARCH.md)), the templates `init` copies ([`templates/`](templates/ARCH.md)) and the references: the prose catalog, the dialect and the consolidation rules that skills read on demand, and the stack defaults that session start and the gate draw from.

## Why this design

One folder serves Claude Code (`.claude-plugin/plugin.json`, hooks auto-loaded from `hooks/hooks.json`), Codex (`.codex-plugin/plugin.json`) and Agent Plugins hosts (`plugin.json`). Separate payloads per host would drift; a test keeps the three versions equal because Claude Code caches installs by version and an unbumped manifest never reaches users. Skills are prompts and stay small (each `SKILL.md` under 6 KiB, all seven under 28 KiB, both tested), because a skill loads whole on every invocation; long material lives in `references/` and is read only when a skill asks for it.

## How it works

Only the human starts `wrap`, which commits: it sets `disable-model-invocation: true` for Claude Code and ships `agents/openai.yaml` with `allow_implicit_invocation: false` for Codex, whose skill docs name only that switch. The other six ship `allow_implicit_invocation: true` without the frontmatter switch, and each description says when the agent may start it; `vae.py doctor` fails when either host's switch of any skill deviates from this policy. `vae.py swarm spawn` refuses a command that names `wrap`, since a headless session runs a skill its prompt names. Each skill calls `scripts/vae.py` through the plugin root variable, and other hosts derive that root from the skill directory. Hooks run `hooks/lifecycle.py` with plain `python3`.

## Operations

- **Configuration:** none at the plugin level; projects configure the gate in their own `.agents/VERIFY.py`.
- **Deployment:** the release zip is this folder at the archive root (`make dist`); hosts unpack it into a per-version cache directory.
- **Compatibility:** `python3` 3.9 or newer, stdlib only, tested on 3.9 in CI; the sub-agent registry needs a POSIX host (`ps`, `flock`).

## Security and privacy

The payload contains no credentials and collects no telemetry. The hook adapter and the programs open no network connection. Their attack surface is the hook input (JSON from the harness) and the project files they read; see [`hooks/ARCH.md`](hooks/ARCH.md) and [`scripts/ARCH.md`](scripts/ARCH.md). The Makefile template that `init` copies has a `setup` verb that downloads installers, pinned tools and locked dependencies for the toolchains a project declares ([`templates/ARCH.md`](templates/ARCH.md)). Data the payload writes can identify the user: the sub-agent registry's host name and absolute paths can contain the user's name, and logs keep whatever the project's commands print.
