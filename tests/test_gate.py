"""Gate state machine and host hooks: blocking, caching, attestations, episodes, fail-closed adapter."""
from __future__ import annotations

import json
import subprocess
import unittest

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    PY,
    ROOT,
    TEST_CMD,
    RepoCase,
)

# isort: split
from vae_gate import REVIEW_CHECKLIST, bounded, gate, validate_review
from vae_hooks import commit_gate, is_commit_command, session_start, stop_gate
from vae_project import init_project
from vae_repo import changed_since, code_fingerprint, read_json, write_json
from vae_state import (
    EPISODE_KEEP,
    append_episodes,
    attestation_path,
    episode_entries,
    init_session,
    state_path,
)


class GateTests(RepoCase):
    def stop(self, sid: str, active: bool = False):
        return stop_gate({"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": sid, "stop_hook_active": active}, ROOT)

    def commit(self, sid: str, command: str = "git commit -m x"):
        return commit_gate({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.repo), "session_id": sid})

    def attest(self, sid: str, fp: str, findings: list | None = None):
        write_json(attestation_path(self.repo, sid, "review"), {
            "schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": REVIEW_CHECKLIST,
            "reviewed_paths": ["calc.py"], "findings": findings or [],
        })

    def attest_docs(self, sid: str, fp: str):
        write_json(attestation_path(self.repo, sid, "docs"), {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [{
            "path": "calc.py", "status": "VERIFIED", "file": "updated", "method": "updated", "inline": "updated",
            "alternative": "wrapper/helper would add no semantic value",
            "why": "VERIFIED: direct addition preserves Python numeric semantics with less machinery.",
        }]})

    def test_sequence_blocks_once_then_review_docs_done_and_commit_gate(self):
        self.make_python_project()
        sid = "s1"
        init_session(self.repo, sid)
        self.write("calc.py", "def add(a, b):\n    # VERIFIED: direct addition preserves Python numeric semantics.\n    return a + b\n")
        out = self.stop(sid)
        self.assertEqual(out["decision"], "block")
        self.assertIn("GATE 2/3 review", out["reason"])
        self.assertIn(f"gate --repo {self.repo} --session {sid}", out["reason"])
        self.assertEqual(self.commit(sid)["hookSpecificOutput"]["permissionDecision"], "deny")
        fp = code_fingerprint(self.repo, ["calc.py"])
        finding = {"status": "VERIFIED", "resolved": True, "location": "calc.py:2", "evidence": "sign bug",
                   "learning": {"status": "VERIFIED", "kind": "test", "why": "subtraction slipped in; test_add pins it"}}
        self.attest(sid, fp, [finding])
        self.assertIn("GATE 3/3 docs", self.stop(sid)["reason"])
        self.attest_docs(sid, fp)
        self.assertIn("WARNS gitignore:", gate(self.repo, sid, ROOT).text, "a green gate still shows warnings with their fix")
        green = self.stop(sid)
        self.assertEqual(set(green), {"systemMessage"}, "warnings never block the stop")
        self.assertIn("warnings that block from 0.6.0: gitignore:", green["systemMessage"])
        self.assertIsNone(self.commit(sid))
        entries = episode_entries(self.repo)
        self.assertEqual(sum(" DONE " in e for e in entries), 1, entries)
        self.assertTrue(any("FINDING calc.py:2 learn=test: subtraction slipped in" in e for e in entries), entries)

    def test_docs_only_session_gates_prose_then_review_without_docs_step(self):
        self.make_python_project(test_command=f"echo run >> runs.txt && {TEST_CMD}")
        sid = "docs1"
        init_session(self.repo, sid)
        self.write("README.md", "Fast \u2014 really.\n")
        out = self.stop(sid)
        self.assertIn("GATE 1/3 verify: FAIL", out["reason"])
        self.assertIn("README.md:1 T02", out["reason"])
        self.assertIn("HARNESS: a FAIL is your work, NOT a reason to end the turn", out["reason"])
        self.assertEqual(self.commit(sid)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.write("README.md", "Fast, really.\n")
        reason = self.stop(sid)["reason"]
        self.assertIn("GATE 2/3 review", reason)
        self.assertIn('PAGES ["README.md"]', reason)
        self.assertIn(str(ROOT / "references/PROSE.md"), reason)
        fp = code_fingerprint(self.repo, ["README.md"])
        write_json(attestation_path(self.repo, sid, "review"), {
            "schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": REVIEW_CHECKLIST,
            "reviewed_paths": ["README.md"], "findings": [],
        })
        self.assertIsNone(self.stop(sid), "no production source changed, so no docs attestation is required")
        self.assertIsNone(self.commit(sid))
        self.assertFalse((self.repo / "runs.txt").exists())
        self.write("README.md", "Fast, really!\n")
        self.assertIn("GATE 2/3 review", self.stop(sid)["reason"], "a page edit invalidates the review")

    def test_repeat_stop_in_same_turn_does_not_block(self):
        self.make_python_project()
        init_session(self.repo, "s2")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        self.assertEqual(self.stop("s2")["decision"], "block")
        out = self.stop("s2", active=True)
        # Stop decision/additionalContext both continue the turn; only user-facing systemMessage may remain.
        self.assertEqual(set(out), {"systemMessage"})
        self.assertIn("GATE 2/3 review", out["systemMessage"])
        self.assertIn(f"gate --repo {self.repo} --session s2", out["systemMessage"])
        self.assertIn("Agent: fix the failing check yourself", out["systemMessage"])
        self.assertEqual(self.commit("s2")["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_verification_is_cached_by_fingerprint_and_policy(self):
        self.make_python_project(test_command=f"echo run >> runs.txt && {TEST_CMD}")
        init_session(self.repo, "s3")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        runs = lambda: (self.repo / "runs.txt").read_text().count("run")
        gate(self.repo, "s3", ROOT)
        gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 1)
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 2)
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("# policy edit\n")
        gate(self.repo, "s3", ROOT)
        self.assertEqual(runs(), 3)

    def test_page_edit_after_green_suite_reruns_only_page_checks(self):
        self.make_python_project(test_command=f"echo run >> runs.txt && {TEST_CMD}")
        init_session(self.repo, "s3b")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        runs = lambda: (self.repo / "runs.txt").read_text().count("run")
        gate(self.repo, "s3b", ROOT)
        self.assertEqual(runs(), 1)
        self.write("README.md", "# calc\n\nAdds numbers \u2014 fast.\n")
        g = gate(self.repo, "s3b", ROOT)
        self.assertIn("README.md:3 T02", g.text, "the page is still checked")
        self.assertEqual(runs(), 1, "but the suites do not rerun for it")
        self.write("README.md", "# calc\n\nAdds numbers, fast.\n")
        review = gate(self.repo, "s3b", ROOT).text
        self.assertIn("GATE 2/3 review", review)
        self.assertIn("WARNS gitignore:", review, "a page-only rerun keeps the warnings of checks it did not run")
        self.assertEqual(runs(), 1)
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        gate(self.repo, "s3b", ROOT)
        self.assertEqual(runs(), 2, "a code edit reruns the suites")

    def test_failures_logged_once_per_distinct_set(self):
        self.make_python_project()
        init_session(self.repo, "s4")
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        g = gate(self.repo, "s4", ROOT)
        self.assertFalse(g.done)
        self.assertIn("GATE 1/3 verify: FAIL", g.text)
        self.write("calc.py", "def add(a, b):\n    return a - b - 0\n")
        gate(self.repo, "s4", ROOT)
        fails = [e for e in episode_entries(self.repo) if " FAIL " in e]
        self.assertEqual(len(fails), 1, fails)
        self.assertTrue(fails[0].endswith("FAIL tests.unit"), fails)

    def test_source_edit_invalidates_attestations(self):
        self.make_python_project()
        init_session(self.repo, "s5")
        self.write("calc.py", "def add(a,b): return a+b\n")
        fp1 = code_fingerprint(self.repo, ["calc.py"])
        self.attest("s5", fp1)
        self.write("calc.py", "def add(a,b): return (a+b)\n")
        fp2 = code_fingerprint(self.repo, ["calc.py"])
        self.assertNotEqual(fp1, fp2)
        self.assertFalse(validate_review(attestation_path(self.repo, "s5", "review"), fp2)[0])

    def test_review_attestation_must_cover_changed_code_paths(self):
        self.make_python_project()
        init_session(self.repo, "s6")
        self.write("calc.py", "def add(a,b): return a+b\n")
        fp = code_fingerprint(self.repo, ["calc.py"])
        rp = attestation_path(self.repo, "s6", "review")
        write_json(rp, {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": REVIEW_CHECKLIST, "reviewed_paths": [], "findings": []})
        ok, reason = validate_review(rp, fp, ["calc.py"])
        self.assertFalse(ok)
        self.assertIn("reviewed_paths incomplete", reason)

    def test_oversized_gate_text_keeps_head_tail_and_full_copy(self):
        text = "HEAD" + "x" * 20000 + "LOOP: python3 vae.py gate"
        out = bounded(self.repo, "big", text)
        self.assertLess(len(out), 9000)
        self.assertTrue(out.startswith("HEAD") and out.endswith("LOOP: python3 vae.py gate"))
        self.assertEqual((self.repo / "tmp/vae/big/gate.txt").read_text(), text)
        self.assertEqual(bounded(self.repo, "big", "short"), "short")

    def test_commit_detection_covers_global_options(self):
        for cmd in ("git commit -m x", "git -C . commit", "git -c core.hooksPath=/dev/null commit -m x",
                    "git --no-pager commit", "npm test && git commit -am y"):
            self.assertTrue(is_commit_command(cmd), cmd)
        for cmd in ("git log --grep commit", "legit commit", "git status"):
            self.assertFalse(is_commit_command(cmd), cmd)

    def test_session_start_keeps_baseline_and_injects_memory(self):
        self.make_python_project()
        init_project(self.repo, ROOT)
        with (self.repo / ".agents/MEMORY.md").open("a") as f:
            f.write("- VERIFIED[db] migrations run via `make migrate` BC 2026-09-30 run\n")
        append_episodes(self.repo, "old", ["LESSON HYPOTHESIS[cache] falsified BC probe"], ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "same"}
        ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- VERIFIED[db] migrations run via `make migrate`", ctx)
        self.assertNotIn("VERIFIED[scope] fact", ctx)
        self.assertIn("LESSON HYPOTHESIS[cache]", ctx)
        self.assertIn(f"gate --repo {self.repo} --session same", ctx)
        self.assertIn("start on `bun` (JS/TS, `bun init`) or `uv` (Python, `uv init`)", ctx)
        self.assertIn("ISO-8601 UTC timestamp first", ctx)
        self.assertIn("`uv run --env-file .env`", ctx)
        self.assertLess(len(ctx), 9001)
        s1 = read_json(state_path(self.repo, "same"))
        self.write("calc.py", "def add(a,b): return a+b\n")
        session_start(event, ROOT)
        s2 = read_json(state_path(self.repo, "same"))
        self.assertEqual(s1["baseline"], s2["baseline"])
        self.assertIn("calc.py", changed_since(self.repo, s2["baseline"]))

    def test_episodes_stay_bounded(self):
        append_episodes(self.repo, "s", [f"FAIL x{i}" for i in range(EPISODE_KEEP + 5)], ROOT)
        text = (self.repo / ".agents/EPISODES.md").read_text()
        entries = episode_entries(self.repo)
        self.assertEqual(len(entries), EPISODE_KEEP)
        self.assertTrue(entries[0].endswith("FAIL x5") and text.startswith("# Episodes"))

    def test_adapter_fails_closed_when_gate_crashes(self):
        self.make_python_project()
        self.write("tmp", "a file where the runtime dir must go\n")
        self.write("calc.py", "def add(a,b): return a+b\n")

        def hook(event: dict) -> str:
            event = {"cwd": str(self.repo), "session_id": "crash", **event}
            return subprocess.run([PY, str(ROOT / "hooks/lifecycle.py")], input=json.dumps(event), text=True, capture_output=True, check=False).stdout

        denied = json.loads(hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("could not run", denied["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(json.loads(hook({"hook_event_name": "Stop", "stop_hook_active": False}))["decision"], "block")
        self.assertEqual(hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}), "")


if __name__ == "__main__":
    unittest.main()
