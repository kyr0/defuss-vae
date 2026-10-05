# Architecture: hook adapter

`hooks.json` registers one command for three harness events, and `lifecycle.py` turns each event into a decision: SessionStart injects rules and memory, Stop runs the gate, PreToolUse denies `git commit` while the gate is open. Policy lives in [`../scripts/`](../scripts/ARCH.md); this folder only adapts.

## Why this design

A single entry point keeps the hook contract (JSON in on stdin, JSON out on stdout) in one place. It runs on plain `python3` instead of `uv run`: `VERIFIED:` `uv run` added 5.4 ms to a hook that fires before every Bash call, and a hook command that cannot start is a non-blocking error in Claude Code, which would open the gate.

## How it works

The adapter reads the event, dispatches on `hook_event_name`, and prints the result. Stop blocks once per turn with the gate text (`decision: "block"`); while `stop_hook_active` is true it returns only a `systemMessage`, because `VERIFIED:` (hooks reference, 2026-10-05) Stop `additionalContext` also continues the turn and would loop the model up to the harness's continuation cap. PreToolUse matches `git commit` in Bash and PowerShell commands, including `git -C <dir> commit`.

## Operations

- **Timeouts:** 10 s for SessionStart and PreToolUse, 600 s for Stop, which may run the project's suites.
- **Latency:** PreToolUse returns before loading the gate when a command does not contain `commit`: 19 ms per Bash call against 16 ms for Python startup alone; SessionStart 55 ms; Stop on a cached gate 86 ms (`make bench`, Apple M4).
- **Reliability:** fail closed. Any exception, including a gate module that fails to import, becomes a commit denial or one Stop block naming the error; input that is not JSON is ignored, since there is nothing to gate.
- **Observability:** the gate text the agent receives is the trace; the gate writes its own logs and state (see `../scripts/`).

## Security and privacy

Input is the harness's event JSON; the adapter reads only `cwd`, `session_id`, `stop_hook_active`, the event and tool names and the tool command, and resolves the repository through git. It never executes the tool command it inspects. No personal data beyond the session id.
