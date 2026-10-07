# Memory consolidation

`MEMORY.md` and `CLI_GIST.md` are loaded into every session (of `EPISODES.md` only the three newest open entries; agents grep the rest by path or symptom), so an outdated entry misleads every agent after it. Consolidation keeps it true and small, without losing what still holds: removing a valid lesson costs as much as keeping a wrong one.

## Reflect first

Before the audit, capture what this work taught that nothing records yet: a falsified hypothesis, a dead end, a root cause, a command that worked. Each one takes the strongest form that fits, at the narrowest scope its evidence supports: a test, a `.agents/VERIFY.py` rule, one MEMORY line with its reason after `BC`, or a CLI_GIST line for a command. A lesson a test or rule already enforces needs no line.

## Audit every entry

Scope: every line in `.agents/MEMORY.md`, `.agents/CLI_GIST.md` and `.agents/EPISODES.md`, and every rule an agent added to `AGENTS.md` outside the managed block.

1. **Check it against the current repo:** does the file, symbol, command or behavior it names still exist and act as stated? Read the code, run the command or test. `vae.py doctor --repo .` lists entries that cite paths which no longer exist; those are candidates to look at, not verdicts.
2. **Decide only on evidence:**

   | Evidence | Action |
   |---|---|
   | Contradicted by current code, a test or command output | Rewrite to the current fact, or delete |
   | Superseded: a test or `.agents/VERIFY.py` rule now enforces it | Delete, naming the test or rule |
   | Derivable from code, git or docs | Delete |
   | Cites a moved path or symbol | Rewrite to the new location |
   | Cites something removed, and the fact went with it | Delete |
   | Duplicates another entry | Keep one |
   | Scope wider than its evidence | Narrow the `[scope]` to what the evidence covers |
   | No evidence either way | Keep it and retag `UNKNOWN[...]` with what would settle it |

3. **Never delete on age alone.** An old entry that still holds is the most valuable kind.
4. **Human-written content is never deleted.** `AGENTS.md` outside the managed block belongs to the human unless an agent demonstrably added the line; propose the change instead.
5. **When unsure, keep.** A wrong deletion is silent; an `UNKNOWN` entry invites the next check.

## Scope

The `[scope]` in `VERIFIED[scope]` is the narrowest boundary the evidence supports (a path, module, command or condition), not a topic label. An entry decides nothing outside it, however relevant it looks. Promote a lesson at that smallest scope: `FAIL` lines recurring in `src/db/` become a test there or a `VERIFIED[src/db]` line, not a repo-wide rule. Widen a scope only with evidence from the wider area; repetition, recency, detail and confident wording widen nothing.

## Per file

- **MEMORY.md:** one tagged line per durable decision, invariant, constraint or promoted lesson that code, git and docs do not already state, with its reason after `BC`, at most 240 characters; at most 4 KiB in all. `doctor --repo .` fails on a line without `BC` or over the limit.
- **EPISODES.md:** the gate writes `FAIL`, `DONE` and `FINDING` (each distinct finding once). Agents add at most one `LESSON` line per falsified hypothesis, dead end or root cause. Past 100 entries the gate trims only its own noise, oldest first: `DONE`, `FAIL`, and a `FINDING` already learned as a test, rule or memory line. Leads (`LESSON` lines, `FINDING learn=none`, any other kind) stay until wrap settles each one: promote it to the strongest form that fits (a test, a `.agents/VERIFY.py` rule, or one MEMORY line with `BC`), then delete it; or delete it with evidence from the table above. A lead with no evidence either way stays. `doctor --repo .` fails at more than 30 leads. Repeated `FAIL` lines for one failure signal a lesson worth a test or rule.
- **CLI_GIST.md:** the shortest non-Makefile commands for setup, development, data and release; `VERIFIED` only after an observed success, otherwise `UNKNOWN`; at most 2 KiB.
- **AGENTS.md:** `vae.py init` regenerates the managed block. Agents add only rules that prevent a repeated mistake, and remove an agent-added rule once a test or rule enforces it.

## Report

List every change with its evidence, one line each: `REMOVED <file>: <entry> BC <evidence>`, `REWROTE <file>: <old> → <new> BC <evidence>`, `RETAGGED <file>: <entry> UNKNOWN BC <what would settle it>`. If nothing changed, say so.
