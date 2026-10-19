# Hook adapter

`lifecycle.py` is the interface between an agent harness and the gate. The harness runs it with one event as JSON on stdin; it answers with JSON on stdout, or nothing when there is nothing to decide. `hooks.json` registers it for Claude Code.

```bash
echo '{"hook_event_name": "Stop", "cwd": ".", "session_id": "s1", "stop_hook_active": false}' | python3 plugin/hooks/lifecycle.py
```

| Event | Registered for | Output |
|---|---|---|
| `SessionStart` | `startup`, `resume`, `clear`, `compact`, `fork` | `additionalContext`: the rules, the live sub-agents (`.vae/SWARM_STATUS.yaml`), `.vae/MEMORY.md`, `.vae/CLI_GIST.md`, the defaults of the stacks present and up to three open episodes, cut by priority at 9,000 characters; ahead of them the installed version and, when they apply, a pending move from `.agents/` to `.vae/`, a newer release and where the memory history is. Side effects: each changed memory file is archived, and a due release check starts detached |
| `Stop` | every stop | `decision: "block"` with the gate text while the gate is open; with `stop_hook_active` true, only a `systemMessage` so the turn ends |
| `PreToolUse` | `Bash`, `PowerShell` | `permissionDecision: "deny"` for a `git commit` while the gate is open |
| `PreToolUse` | `Edit`, `Write`, `MultiEdit` | nothing; before an edit of `.vae/MEMORY.md`, `CLI_GIST.md` or `EPISODES.md` it gzips the current version into `tmp/vae/memory/`. Any other path returns before an import (about 20 ms, `make bench`); a failed archive never blocks the edit, it only adds a `systemMessage` |

Input that is not JSON produces no output. Any error inside the gate denies the commit or blocks the stop once, naming the error. Why it is built this way: [`ARCH.md`](ARCH.md).
