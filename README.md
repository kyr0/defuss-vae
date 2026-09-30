# defuss-vae — verified agentic engineering

Human-triggered skills plus deterministic programs that verify, gate and remember.

```text
plan → implement → gate[verify → review → docs] → finalize → Conventional Commits
```

`VAE` = verified agentic engineering. Design bias: evidence before assumption, Ponytail/YAGNI, reuse → stdlib → native → installed dependency before new code, real tests instead of mocks, minimal persistent context.

## Guarantees

- Skills never self-trigger (`disable-model-invocation: true`); automatic enforcement lives in programs, not prompts.
- **Gate** = one state machine (`verify → review → docs`) shared by the Stop hook and `vae.py gate`. `VERIFIED:` Claude Code honors one Stop `decision: "block"` per turn and `additionalContext` alone ends the turn, so the first unfinished stop blocks with the exact gate command and the agent re-runs it in-turn until `VERIFIED[gate]=true`; a later stop in the same turn leaves the gate text for the next prompt.
- `git commit` (including `git -c …`/`--no-pager` forms) is denied while the current code fingerprint lacks verify + review + docs. If the gate itself crashes, the hook adapter fails closed (commit denied, stop blocked).
- Verifier requires: loadable `.agents/VERIFY.py`, layout, tests present, test command passing, integration/e2e commands passing, coverage ≥60%, no leftover probes, every project rule. A missing command or metric is `UNKNOWN`, which fails.
- Verification is content-addressed: cached per code fingerprint + policy-file hashes, so review/docs loop turns never re-run suites (`make bench`: 0.27 s cold vs 0.09 s cached on the e2e fixture).
- Evidence stays out of the context window: a passing check reports one line; a failing check reports a bounded tail plus its full log at `var/log/vae/<check>.log`. Gate text stays below Claude Code's 10,000-char hook cap.

## Layout (strict)

```text
.agents/VERIFY.py     executable project policy (config + deterministic rules)
.agents/MEMORY.md     tagged durable facts          ≤4 KiB, injected at session start
.agents/CLI_GIST.md   verified non-Makefile commands ≤2 KiB, injected at session start
.agents/EPISODES.md   gate-written FAIL|DONE|FINDING + agent LESSON lines, last 100
Makefile              start stop restart status log metrics bench test e2e
var/log/<svc>.stdout|.stderr, tmp/<svc>.pid   (gitignored)
input/ → program → output/
```

`vae.py init` scaffolds whatever is missing and never overwrites. The template Makefile implements LSB-style service control: `start` runs `RUN` in its own process group with redirected stdio (no hanging agent shells), `stop` reaps the whole group, and `status` returns 0 running / 1 dead with stale pid / 3 stopped. `test`, `e2e`, `metrics` and `bench` exit 2 (`UNKNOWN`) until defined. The verifier discovers `make test|coverage|integration|e2e` first, so the Makefile is the one interface for humans, agents and CI.

- **test** = real subsystems in isolation (real fs/process/db/port under `tmp/`), no mocks. The template rule `tests.no-mocks` rejects mock frameworks in changed files.
- **e2e** = build the publishable artifact, install it into a clean consumer, run `input/` → `output/`. Never import from the source tree.

## Evidence loop

When a runtime fact is unknown, observe it before editing: read → existing test/command → the smallest probe that discriminates the live hypotheses → ask. Temporary probe lines carry the comment `vae:probe`; the built-in `hygiene.probes` check fails while any remain in changed code, so probing is cheap and pollution is impossible to forget. Logs are read bounded (`make log N=80`, `tail`, `grep`). Permanent logging only at boundaries and errors.

## Memory

Hierarchy: test or `.agents/VERIFY.py` rule (memory that runs) > tagged `MEMORY.md` line > `EPISODES.md` line.

- The gate appends `FAIL` (deduplicated per failure set), `DONE` (fingerprint, coverage, paths) and one `FINDING` per review finding. The file is trimmed on write, so it stays bounded without agent tokens.
- SessionStart injects MEMORY/CLI_GIST entries and the last 3 episodes. `VERIFIED:` memory that is not loaded is not used; the budgets bound the per-session cost.
- `finalize` consolidates: a lesson recurring ≥2 becomes a test, rule or MEMORY line and its episode lines are deleted. `vae.py doctor --repo .` enforces budgets, epistemic tags and layout.

## Skills

```text
/defuss-vae:plan  /defuss-vae:implement  /defuss-vae:review  /defuss-vae:finalize
```

Written in Signan (see below); each is <5.5 KiB, the pack <18.5 KiB, and both are tested.

- `plan`: trace, probe unknowns, prior-art ladder, emit exact change points where every acceptance invariant is an executable check.
- `implement`: Ponytail operating mode, evidence loop, RED→GREEN→REFACTOR, layout, gate loop.
- `review`: correctness/contract pass (incl. mocks, dogfood e2e, observability), then Ponytail deletion pass; behavioral findings are demonstrated by a failing test or probe; repeatable defects become tests or rules.
- `finalize`: fresh gate proof → coherent Conventional Commits → CHANGELOG + memory consolidation.

Research provenance and trade-offs: `docs/PROMPT_DESIGN.md`.

## Signan

Every skill embeds a self-contained Signan core; `references/SIGNAN.md` is canonical. Uppercase words are operators only (`NOT AND OR IF … THEN … ELSE WHEN CAUSES SAYS BC EVERY SOME ONE REQUIRES MAY`); `WHEN` is equivalence, never a conditional; tags are `VERIFIED` / `HYPOTHESIS` / `UNKNOWN`. A test enforces the operator discipline on the skills. Outside skills, hook text stays plain concise prose.

## Hosts

- **Claude Code**: `claude --plugin-dir "$PWD"` for local use; the marketplace layout is included (`/plugin marketplace add`, then install `defuss-vae@defuss-vae`). Skills get `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_SESSION_ID}` substituted, so they run the gate directly.
- **Codex**: root `plugin.json` (Agent Plugins 1.0) plus `.codex-plugin/plugin.json` declaring hooks; review/trust them via `/hooks`.
- **Other Agent Skills hosts**: copy `skills/`; without hooks, run `python3 scripts/vae.py gate --repo .` before finishing. See `docs/COMPATIBILITY.md`.

## CLI

```bash
python3 scripts/vae.py gate   --repo . [--session ID]   # verify → review → docs; exit 0 when done
python3 scripts/vae.py verify --repo . [--json]         # verifier only
python3 scripts/vae.py init   --repo .                  # scaffold layout + .agents (alias: finalize-init)
python3 scripts/vae.py doctor [--repo .]                # plugin files, or project memory/layout hygiene
make test | make e2e | make coverage PYTHON=… | make bench | make metrics | make dist
```

Custom rules in `.agents/VERIFY.py`: kinds `command`, `file_exists`, `contains`, `regex`, `not_regex`; scope `path` (one file) or `glob` (every changed code file):

```python
RULES = [{"id": "api.no-legacy", "kind": "not_regex", "glob": "src/*.ts", "pattern": r"legacyCall\(", "claim": "legacy API absent"}]
```

## Enforcement boundary

`VERIFIED:` programs prove command results, fingerprint freshness, attestation schema and path coverage, layout, probe hygiene and commit gating.

`UNKNOWN:` a deterministic hook cannot prove that a review was *semantically good* rather than a valid attestation. defuss-vae forces the review step, requires changed-path coverage, and invalidates it on any later source edit; review quality remains a model property. An independent model reviewer would reduce this uncertainty at substantially higher token cost and is deliberately not the default.

## License

MIT.
