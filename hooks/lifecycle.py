#!/usr/bin/env python3
"""Claude/Codex lifecycle adapter; all policy logic stays in scripts/vae_core.py."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from vae_core import commit_gate, deny, is_commit_command, session_start, stop_gate  # noqa: E402


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    name = event.get("hook_event_name")
    try:
        out = None
        if name == "SessionStart":
            out = session_start(event, ROOT)
        elif name == "Stop":
            out = stop_gate(event, ROOT)
        elif name == "PreToolUse":
            out = commit_gate(event)
    except Exception as e:
        # Fail closed: a crashing gate must not silently allow a commit or end the turn as if verified.
        reason = f"defuss-vae gate could not run ({type(e).__name__}: {e}); resolve the cause or report it to the human."
        tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        if name == "PreToolUse" and is_commit_command(str(tool_input.get("command") or "")):
            out = deny(reason)
        elif name == "Stop" and not event.get("stop_hook_active"):
            out = {"decision": "block", "reason": reason}
        else:
            out = None
    if out:
        print(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
