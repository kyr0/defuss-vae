# vae.py CLI

The command-line interface behind the skills and hooks. Every command takes `--repo` (default `.`, resolved to the enclosing git root) and exits 0 when its claim holds and 2 when it does not, so scripts and CI can branch on it.

```bash
python3 plugin/scripts/vae.py gate --repo .
```

| Command | What it does | Exit 0 when |
|---|---|---|
| `init` | Scaffolds what's missing: `.agents/`, `Makefile`, `.gitignore` lines, the managed `AGENTS.md` block and, on a GitHub remote, the CI workflow. Never overwrites. | always |
| `gate [--session ID]` | Runs verify → review → docs for a session (default: the latest under `tmp/vae/`) and prints what's missing. | `VERIFIED[gate]=true` |
| `verify [--json] [--changed PATH]...` | The verifier alone, on the current changes or the given paths. | every required check passes |
| `prose [--fix] [PAGE]...` | Static prose check of doc pages (default: every Markdown page). `--fix` first applies the replacements that cannot change meaning. | no findings |
| `doctor [--repo]` | Without `--repo`: the plugin's own files. With it: the project's memory budgets, epistemic tags and layout. | nothing missing |

Check results print as `VERIFIED[check]=true|false BC evidence`, so the reason is on the same line as the result; `gate` adds the next instruction for the agent. Architecture and module layering: [`ARCH.md`](ARCH.md).
