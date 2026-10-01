# defuss-vae — verified agentic engineering

Human-triggered skills plus deterministic programs that verify, gate and remember.

```text
plan → implement → gate[verify → review → docs] → finalize → Conventional Commits
```

Skills never self-trigger; enforcement lives in programs, not prompts. `git commit` is denied until the current code fingerprint passes verify + review + docs; the hook adapter fails closed. Verification is content-addressed (cached per fingerprint); evidence stays out of the context window.

## Use

Requirements: `python3` ≥ 3.9, `git`, `make`. Hooks and CLI are stdlib-only.

- **Claude Code**: `claude --plugin-dir "$PWD/plugin"`, or `/plugin marketplace add <repo>` then install `defuss-vae@defuss-vae`.
- **Codex**: `plugin/plugin.json` (Agent Plugins 1.0) + `plugin/.codex-plugin/plugin.json`; trust hooks via `/hooks`.
- **Other hosts**: copy `plugin/skills/`; without hooks, run `python3 plugin/scripts/vae.py gate --repo .` before finishing. See `docs/COMPATIBILITY.md`.

Skills: `/defuss-vae:plan`, `:implement`, `:review`, `:finalize`.

## CLI

```bash
python3 plugin/scripts/vae.py init   --repo .   # scaffold layout (.agents/, Makefile, .env.example, …)
python3 plugin/scripts/vae.py gate   --repo .   # verify → review → docs; exit 0 when done
python3 plugin/scripts/vae.py verify --repo .   # verifier only
python3 plugin/scripts/vae.py doctor --repo .   # memory/layout hygiene
```

## Project layout (scaffolded by `init`)

`.agents/VERIFY.py` (executable policy: config + rules `command|file_exists|contains|regex|not_regex`), `.agents/MEMORY.md` ≤4 KiB, `.agents/CLI_GIST.md` ≤2 KiB, `.agents/EPISODES.md` (last 100), `Makefile` (`setup start stop test coverage lint e2e verify`), gitignored `.env`/`var/`/`tmp/`/`input/`/`output/`, committed `.env.example`.

Verifier requires: tests present and passing, integration/e2e passing, coverage ≥60% (`make coverage` prints `TOTAL <n>%`), no leftover `vae:probe` lines, no new foreign toolchain, complete `.env.example`, all VERIFY.py rules. A missing command or metric is `UNKNOWN`, which fails.

## Details

- Rules and design: `docs/PROMPT_DESIGN.md`, `plugin/references/SIGNAN.md` (canonical Signan dialect).
- Enforcement boundary: hooks prove command results, fingerprints, layout and commit gating; review *quality* remains a model property.
- Contributing: `AGENTS.md`; run `make setup && make verify`.

## License

MIT.
