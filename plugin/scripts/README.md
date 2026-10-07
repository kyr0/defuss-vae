# vae.py CLI

The command-line interface behind the skills and hooks. Every command takes `--repo` (default `.`, resolved to the enclosing git root) and exits 0 when its claim holds and 2 when it does not (`swarm status`: 1 when an agent needs the orchestrator), so scripts and CI can branch on it.

```bash
python3 plugin/scripts/vae.py gate --repo .
```

| Command | What it does | Exit 0 when |
|---|---|---|
| `init` | Scaffolds what's missing: `.agents/`, `Makefile`, `.gitignore` lines, the managed `AGENTS.md` block and, on a GitHub remote, the CI workflow (unless `CONFIG["ci"]` is `False` or a workflow already runs the verification). Never overwrites. | always |
| `gate [--session ID]` | Runs verify → review → docs for a session (default: the one whose state changed last) and prints what's missing. | `VERIFIED[gate]=true` |
| `verify [--json] [--changed PATH]...` | The verifier alone, on the current changes or the given paths. | every required check passes |
| `prose [--fix] [PAGE]...` | Static prose check of doc pages (default: every Markdown page). `--fix` first applies the replacements that cannot change meaning. | no findings |
| `prose --walk PAGE [--part N]` | One step of the page review: part N with part N-1 as context, every catalog rule (the whole catalog at part 1), the static hits in part N and the command for step N+1. Past the last part it runs the static check. | `VERIFIED[walk]=true` |
| `doctor [--repo]` | Without `--repo`: the plugin's own files. With it: the project's memory budgets, epistemic tags and layout. | nothing missing |
| `swarm ACTION` | The sub-agent registry `.agents/SWARM_STATUS.yaml` at the main worktree root. `spawn --name --goal --workdir --targets [--eta --ram --vram --disk --gpu --container] -- CMD` starts `CMD` detached, logs each line with a timestamp to `var/log/swarm/<name>.log` and registers it; `set` updates or registers an entry the caller owns; `rm` reaps one; `stop` ends a spawned job (SIGTERM, SIGKILL after 5 s); `status [--fix]` prints free resources and each agent's state and records drift. Every write is re-read after `--settle` seconds (default 3). | the claim holds; `status`: 0 when no agent needs the orchestrator, 1 when one does |

Check results print as `VERIFIED[check]=true|false BC evidence`, so the reason is on the same line as the result; `gate` adds the next instruction for the agent. Architecture and module layering: [`ARCH.md`](ARCH.md).
