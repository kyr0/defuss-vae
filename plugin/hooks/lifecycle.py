#!/usr/bin/env python3
"""Claude/Codex lifecycle adapter; event logic lives in scripts/vae_hooks.py, policy in the modules it imports."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# The memory files whose every version is archived before an edit (vae_state.MEMORY_FILES under vae_repo.STATE_DIR;
# test_gate checks the two agree). Spelled out here so the fast path below needs no import.
MEMORY_PATHS = (".vae/MEMORY.md", ".vae/CLI_GIST.md", ".vae/EPISODES.md")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:  # not JSON: nothing to gate
        return 0
    name = event.get("hook_event_name")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or "")
    memory_edit = name == "PreToolUse" and event.get("tool_name") in EDIT_TOOLS
    # WHY before importing the gate: PreToolUse fires before every Bash call and every edit; a command without
    # "commit" cannot be one, and an edit of any other file needs no archive. VERIFIED: (make bench, Apple M4) skipping
    # the gate imports cut that hook from 35 ms to 19 ms; python3 startup alone is 16 ms.
    if memory_edit and not str(tool_input.get("file_path") or "").replace("\\", "/").endswith(MEMORY_PATHS):
        return 0
    if name == "PreToolUse" and not memory_edit and "commit" not in command:
        return 0
    try:
        # Imported inside the try: a broken install must fail closed below, not crash the hook (a crashed hook is a
        # non-blocking error in Claude Code, which would let the commit through).
        from vae_hooks import commit_gate, memory_guard, session_start, stop_gate

        out = None
        if name == "SessionStart":
            out = session_start(event, ROOT)
        elif name == "Stop":
            out = stop_gate(event, ROOT)
        elif memory_edit:
            out = memory_guard(event)
        elif name == "PreToolUse":
            out = commit_gate(event)
    except Exception as e:  # noqa: BLE001 - fail closed on ANY gate crash, see below
        # Fail closed: a crashing gate must not silently allow a commit or end the turn as if verified.
        reason = f"defuss-vae gate could not run ({type(e).__name__}: {e}); resolve the cause or report it to the human."
        if memory_edit:  # the archive protects history; it never blocks the edit itself
            out = {"systemMessage": f"defuss-vae could not archive the memory file before this edit ({type(e).__name__}: {e})."}
        elif name == "PreToolUse":  # only commands containing "commit" get here; deny without the (maybe broken) modules
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}
        elif name == "Stop" and not event.get("stop_hook_active"):
            out = {"decision": "block", "reason": reason}
        else:
            out = None
    if out:
        print(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
