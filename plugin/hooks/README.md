# Hook adapter

`lifecycle.py` is the interface between an agent harness and the gate. The harness runs it with one event as JSON on stdin; it answers with JSON on stdout, or nothing when there is nothing to decide. `hooks.json` registers it for Claude Code.

```bash
echo '{"hook_event_name": "Stop", "cwd": ".", "session_id": "s1", "stop_hook_active": false}' | python3 plugin/hooks/lifecycle.py
```

| Event | Registered for | Output |
|---|---|---|
| `SessionStart` | `startup`, `resume`, `clear`, `compact`, `fork` | `additionalContext`: the rules, `.agents/MEMORY.md`, `.agents/CLI_GIST.md` and recent episodes |
| `Stop` | every stop | `decision: "block"` with the gate text while the gate is open; with `stop_hook_active` true, only a `systemMessage` so the turn ends |
| `PreToolUse` | `Bash`, `PowerShell` | `permissionDecision: "deny"` for a `git commit` while the gate is open |

Input that is not JSON produces no output. Any error inside the gate denies the commit or blocks the stop once, naming the error. Why it is built this way: [`ARCH.md`](ARCH.md).
