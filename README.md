# defuss-vae — verified agentic engineering

Human-triggered skills plus deterministic programs that verify, gate and remember.

```text
plan → implement → gate[verify → review → docs] → finalize → Conventional Commits
```

Skills never self-trigger; enforcement lives in programs, not prompts. `git commit` is denied until the current code fingerprint passes verify + review + docs; the hook adapter fails closed. Verification is content-addressed (cached per fingerprint); evidence stays out of the context window.

## Install

Requirements: `python3` ≥ 3.9, `git`, `make`. Hooks and CLI are stdlib-only.

defuss-vae ships four Agent Skills (`plan`, `implement`, `review`, `finalize`) plus the hooks and CLI that enforce the gate. Skills alone are prompts; the hooks are what deny `git commit` and block finishing until the gate passes. Install as a **plugin** wherever the host supports it, so you get both.

### Claude Code (recommended: plugin, includes hooks)

Inside Claude Code:

```text
/plugin marketplace add kyr0/defuss-vae
/plugin install defuss-vae@defuss-vae
```

Or from your shell:

```bash
claude plugin marketplace add kyr0/defuss-vae
claude plugin install defuss-vae@defuss-vae
```

Start a new session afterwards. Update with `/plugin marketplace update defuss-vae`. Local checkout instead: `claude --plugin-dir "$PWD/plugin"`.

### Codex (plugin)

`plugin/plugin.json` (Agent Plugins 1.0) + `plugin/.codex-plugin/plugin.json`; trust the hooks via `/hooks`. Hook parity with Claude Code is untested, see `docs/COMPATIBILITY.md`.

### Codex, Cursor, Gemini CLI, Copilot, Windsurf, … (skills only)

The skills install into any Agent Skills host with the open [`skills`](https://www.npmjs.com/package/skills) CLI. It detects your agents and asks interactively:

```bash
npx skills add kyr0/defuss-vae --skill '*'
```

Pick agents with repeated `--agent` (`claude-code`, `codex`, `cursor`, `gemini-cli`, `github-copilot`, `windsurf`, or `'*'` for all) and skills with repeated `--skill`:

```bash
npx skills add kyr0/defuss-vae --skill plan --skill implement --agent codex --agent cursor
```

Skills install into the current project by default; add `--global` (`-g`) for all projects, `--yes` (`-y`) for bootstrap scripts, or `--list` to see what is available:

```bash
npx skills add kyr0/defuss-vae --skill '*' --agent codex --global --yes
npx skills update
```

**No hooks, no CLI.** This route copies only `plugin/skills/`: nothing blocks `git commit` or finishing, and the skills' `vae.py` path does not resolve. Clone the repo once and run the gate yourself before finishing:

```bash
git clone https://github.com/kyr0/defuss-vae ~/defuss-vae
python3 ~/defuss-vae/plugin/scripts/vae.py gate --repo .
```

In Claude Code prefer the plugin: its skills are namespaced (`/defuss-vae:plan`), skills-CLI installs are not (`/plan`).

### Using the skills

Skills are human-triggered only: the agent never invokes them on its own. Call them explicitly:

- Claude Code plugin: `/defuss-vae:plan`, `/defuss-vae:implement`, `/defuss-vae:review`, `/defuss-vae:finalize`
- Codex: `$plan add rate limiting to the upload endpoint`, then `$implement`, `$review`, `$finalize`

## CLI

```bash
python3 plugin/scripts/vae.py init   --repo .   # scaffold layout (.agents/, Makefile, .env.example, …)
python3 plugin/scripts/vae.py gate   --repo .   # verify → review → docs; exit 0 when done
python3 plugin/scripts/vae.py verify --repo .   # verifier only
python3 plugin/scripts/vae.py doctor --repo .   # memory/layout hygiene
```

## Project layout (scaffolded by `init`)

`.agents/VERIFY.py` (executable policy: config + rules `command|file_exists|contains|regex|not_regex`), `.agents/MEMORY.md` ≤4 KiB, `.agents/CLI_GIST.md` ≤2 KiB, `.agents/EPISODES.md` (last 100), `Makefile` (`setup start stop test coverage lint e2e verify`), gitignored `.env`/`var/`/`tmp/`/`input/`/`output/`, committed `.env.example`.

Verifier requires: `.agents/VERIFY.py` present, `make verify` wired to lint test coverage e2e, lint passing, tests present and passing, integration/e2e passing with fresh evidence in `output/`, coverage ≥60% (`make coverage` prints `TOTAL <n>%`), no leftover `vae:probe` lines, no new foreign toolchain, complete `.env.example`, all VERIFY.py rules. A missing command or metric is `UNKNOWN`, which fails.

## Details

- Rules and design: `docs/PROMPT_DESIGN.md`, `plugin/references/SIGNAN.md` (canonical Signan dialect).
- Enforcement boundary: hooks prove command results, fingerprints, layout and commit gating; review *quality* remains a model property.
- Contributing: `AGENTS.md`; run `make setup && make verify`.

## License

MIT.
