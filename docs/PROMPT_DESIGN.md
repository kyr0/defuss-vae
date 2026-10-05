# Prompt design provenance

Research checked: 2026-09-29; hook/skill runtime semantics re-checked 2026-09-30.

This document is provenance only; skills do not load it during normal use.

## Sources

- Anthropic, Prompting best practices: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables
- Anthropic Claude Code, feature-dev plugin: https://github.com/anthropics/claude-code/tree/main/plugins/feature-dev
- Anthropic Claude Code, feature-dev code reviewer: https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/agents/code-reviewer.md
- Anthropic Claude Code, plugin-dev skill reviewer: https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/agents/skill-reviewer.md
- Dietrich Gebert, Ponytail: https://github.com/DietrichGebert/ponytail
- Ponytail core skill: https://github.com/DietrichGebert/ponytail/blob/main/skills/ponytail/SKILL.md
- Ponytail review skill: https://github.com/DietrichGebert/ponytail/blob/main/skills/ponytail-review/SKILL.md
- obra/superpowers, writing plans: https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md
- obra/superpowers, executing plans: https://github.com/obra/superpowers/blob/main/skills/executing-plans/SKILL.md
- obra/superpowers, TDD: https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md
- obra/superpowers, verification before completion: https://github.com/obra/superpowers/blob/main/skills/verification-before-completion/SKILL.md
- Conventional Commits 1.0.0: https://www.conventionalcommits.org/en/v1.0.0/
- Keep a Changelog: https://keepachangelog.com/
- Claude Code hooks reference (Stop decision control, output caps): https://code.claude.com/docs/en/hooks
- Claude Code skills (string substitutions, `disable-model-invocation` loading): https://code.claude.com/docs/en/skills
- Editorial prose rule catalog (56 rules in seven groups plus comment/rewrite policy), supplied by the maintainer; condensed into `plugin/references/PROSE.md`
- defuss aslopcleaner (prior art for character-level slop replacement, not a dependency): https://github.com/kyr0/defuss/tree/main/packages/aslopcleaner
- Mermaid diagram syntax: https://mermaid.js.org/intro/syntax-reference.html

## Derived prompt principles

### Shared

1. Explicit role + ordered workflow + output contract. Anthropic recommends direct instructions and sequential steps when order/completeness matters.
2. Investigate before claims. Read referenced code and trace the relevant path instead of filling gaps from filenames or assumptions.
3. Prompt for minimalism explicitly. Current Claude prompting guidance calls out over-engineering, speculative abstractions, unnecessary files, and defensive code as behaviors worth constraining.
4. Evidence before completion claims. Fresh commands/results are stronger than confidence or reviewer prose.
5. Keep skills lean and move provenance/background out of the runtime prompt. Skill bodies contain only behavior needed during execution.
6. Human-only skills; deterministic hooks own automatic enforcement. Mechanical policy belongs in code rather than repeated prose.
7. VAE-DIALECT is fully specified rather than named implicitly; conventional prose is the fallback when compression would make meaning ambiguous.
8. Skills are written *in* VAE-DIALECT, not only about it. `VERIFIED:` tests cap each skill at 5.5 KiB and the pack at 21.5 KiB, so the evidence loop, layout, proof programs, memory hierarchy and gate loop must pay for themselves in compression. Uppercase is reserved for operators; a test rejects other uppercase prose words and any `WHEN` outside the core, because `WHEN … THEN` reads naturally as a conditional although the grammar defines `WHEN` as equivalence.
9. Evidence loop outranks everything else in `implement`: observe before editing, cheapest observation first, one discriminating probe per open hypothesis split. `HYPOTHESIS:` acting on unverified runtime assumptions is the dominant avoidable agent failure; a probe at the divergence point settles it for one run's cost. The counter-risk, log pollution, is handled mechanically rather than by exhortation: probe lines carry a tag and the verifier fails while any remain; reads are bounded (`make log`, `tail`, `grep`); passing checks report one line.
10. Proof as programs: every acceptance invariant becomes a test, a `make e2e` step or a `.agents/VERIFY.py` rule that the gate executes. Prose memory is the fallback for what cannot run. Hierarchy: executable check > MEMORY line > EPISODES line.
11. Real tests, dogfood e2e. `HYPOTHESIS:` mocks mostly verify the mock; a template rule rejects mock frameworks in changed files, and e2e must consume the built artifact. `VERIFIED:` this repo's e2e installs the release zip (byte-for-byte the `plugin/` payload marketplace users get) and drives only the shipped CLI and hook adapter through the same Stop/PreToolUse JSON contract Claude Code uses. A web frontend's user is a browser, so its e2e drives the served build in real Playwright browsers rather than jsdom/happy-dom or HTTP-only checks, which cannot see rendering, WebGL, permission prompts or blocked requests. Capabilities the app uses (WebGL2, network, `grantPermissions`) are enabled explicitly, because a headless default that silently lacks one turns the e2e into a false pass or a false fail. `VERIFIED:` on macOS (Playwright 1.63, bundled headless Chromium) `canvas.getContext("webgl2")` returns WebGL 2.0 without extra launch args (probed). `HYPOTHESIS:` GPU-less Linux CI needs `--use-angle=swiftshader --enable-unsafe-swiftshader`, since recent Chromium no longer falls back to SwiftShader for WebGL automatically; falsifier: `webgl2` is non-null on a GPU-less runner without those args.
12. One interface: the Makefile verbs (`start stop restart status log metrics bench test coverage e2e`) remove per-project discovery cost for agents; LSB status codes and process-group control make services safe to run from an agent shell.
13. Toolchain: new projects and subprojects start on `bun` (JS/TS) or `uv` (Python); the gate enforces it by rejecting newly introduced foreign lockfiles, because a rule agents only read is a rule they sometimes skip. `VERIFIED:` agent shells (Claude Code's Bash tool) don't keep environment state between calls, so an activated venv is gone by the next command; `uv run` resolves the project environment per command. `VERIFIED:` bun ships runtime, package manager, test runner and coverage in one binary, so `make test`/`make coverage` need no extra dev dependencies, and `bun pm pack` → `bun add <tgz>` / `uv build` → `uv run --isolated --with <wheel>` give dogfood e2e installs (all probed). Existing repos keep their toolchain and get a migration proposal: silently switching package managers mid-task is scope creep. Installing a missing uv/bun happens through `make setup` or the agent's own permissioned shell (official installers), never silently from a hook: hooks run without permission prompts, so a hook that pipes a remote installer into `sh` would bypass the human's consent.
14. Habits as one always-on text, enforced where decidable. Pure core + I/O at the edges is the structural half of principle 11: a core that takes data in and returns data out is testable with real inputs, so mocks become unnecessary. ISO-8601 UTC timestamps first make every log sortable and correlatable across services and with `.agents/EPISODES.md`, which uses the same format. `.env.example` completeness is decidable, so the gate checks it; structure and log format are not, so the review checks them. `VERIFIED:` bun loads `.env` by itself while `uv run` needs `--env-file .env` (probed), so the rule names the mechanism instead of saying "load .env".
15. CI is dogfooding, not a second definition: the workflow runs `make setup` then `make verify`, the same verbs a developer and the gate use, so a green CI means the documented local path works on a fresh machine. `VERIFIED:` plain `oxlint` exits 0 on findings (probed), which is why every lint hint carries `--deny-warnings`. A lint check that cannot fail is not evidence.

### plan

Best elements combined:

- Anthropic: investigate before answering; clear success criteria; structured research with competing hypotheses only when useful.
- Feature-dev: understand codebase before design and make implementation mapping concrete.
- Superpowers: exact files/symbols/tests, but defuss-vae removes the expensive "zero-context engineer + complete code in plan" verbosity.
- Ponytail: understand the flow first, then minimize via YAGNI/reuse/stdlib/native/dependency/minimum code.

Result: research is causal and ordered; a plan contains exact change points + proof, not a tutorial.

### implement

Best elements combined:

- Ponytail full-mode reasoning, including "understand first", root-cause repair, sibling-caller inspection, deletion/reuse first, and no speculative abstractions.
- Anthropic anti-overengineering guidance: only requested/necessary changes; no one-use helpers or hypothetical flexibility; trust internal guarantees and validate boundaries.
- Anthropic anti-test-gaming guidance: solve the general contract, not the fixture.
- Superpowers TDD: non-trivial behavior uses a RED→GREEN→REFACTOR proof; bug fixes prefer regression tests.
- defuss-vae gate: the Stop hook blocks once per turn (the host limit), and the agent loops `vae.py gate` in-turn until verify → review → docs clear for the current fingerprint; commits stay denied until then.

### review

Best elements combined:

- Anthropic code-reviewer: high precision, concrete path/line evidence, bugs/correctness/project conventions before low-value style commentary.
- Feature-dev: correctness + simplicity/DRY + project abstraction/convention review.
- Ponytail-review: explicit deletion/reuse/YAGNI pass after correctness; shortest correct diff is the target.
- defuss-vae: actionable defects are fixed by default; behavioral findings are demonstrated (failing test or probe) when cheap, otherwise labeled `HYPOTHESIS` with a falsifier; repeatable defect classes become tests or verifier rules.

Result: review is not "find something to say"; it is a high-precision attempt to falsify correctness and remove unnecessary complexity.

### docs

Best elements combined:

- The prose catalog: universal, language-independent rules, each with a problem, an instruction, and a guard against overcorrection (e.g. "length alone is no defect"). Kept in `references/PROSE.md` and read on demand, because at 14 KB it would triple the skill; the skill carries only the workflow.
- Comment/rewrite policy from the same catalog: a rewrite only when every reported problem is solvable from the page or repo, otherwise a concrete question. This is the prose form of "evidence > assumption": polishing never stands in for a missing fact.
- Proof before edits (from `plan`): a page with its own invariant gets its `.agents/VERIFY.py` rule before it is written, and every page is under the built-in static check from creation, so checks never arrive after the docs.
- Schematic content → Mermaid (catalog T07), rendered and inspected, because a diagram that fails to render is worse than none and GitHub shows the error box to every reader.
- aslopcleaner's lesson, inverted: a fixed replacement table is the right tool for characters whose replacement cannot change meaning; dashes and phrases need a rewrite by meaning, so they are findings, not fixes.

Result: documentation gets the same treatment as code: a deterministic floor in the gate and a reviewed ceiling.

### finalize

Best elements combined:

- Verification-before-completion: fresh evidence before commit/completion claims.
- Conventional Commits: split commits when one diff contains multiple intents; use machine-readable type/scope/description.
- Keep a Changelog: maintain `Unreleased` notable changes rather than dumping commit history.
- Anthropic long-horizon guidance: filesystem/git are persistent state; durable agent state should be structured and corrected, not an append-only diary.
- Episodic → semantic consolidation: the gate writes a bounded episode log for free; finalize promotes recurring lessons to tests, rules or MEMORY lines and deletes the episodes. Memory and CLI gist are injected at session start under byte budgets that `doctor --repo` enforces.

Result: finalize creates a reconstructable history plus minimal durable state, without pushing or rewriting history implicitly.

## Deliberate exclusions

- No mandatory subagents: token cost conflicts with defuss-vae's goal; hook-enforced fresh evidence gives a cheaper baseline.
- No numeric reviewer confidence score: it looks precise without calibrated probabilities. Findings instead require direct evidence or an explicit falsifiable hypothesis.
- No huge implementation plans containing full code: implementation skills/models already know language syntax; plans lock decisions, interfaces, files, proof, and risks only.
- No mandatory comments on obvious syntax: docs must answer WHY. Each changed production file/method/inline level is assessed; `not-applicable` requires rationale.
- No memory without budget: unbounded memory files become per-session token tax; MEMORY ≤4 KiB, CLI gist ≤2 KiB, episodes ≤100 entries.
