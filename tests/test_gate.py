"""Gate state machine and host hooks: blocking, caching, attestations, episodes, fail-closed adapter."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from vae_testkit import (  # first: puts plugin/scripts on sys.path
    E2E_CMD,
    PY,
    ROOT,
    TEST_CMD,
    RepoCase,
    sh,
)

# isort: split
from vae_gate import REVIEW_CHECKLIST, bounded, gate, validate_docs, validate_review
from vae_hooks import commit_gate, is_commit_command, session_start, stop_gate
from vae_project import init_project
from vae_repo import changed_since, code_fingerprint, read_json, write_json
from vae_state import (
    EPISODE_KEEP,
    append_episodes,
    attestation_path,
    episode_entries,
    init_session,
    latest_session,
    session_dir,
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
        # A learned regression test needs a verified root cause, never a pinned environment.
        self.assertIn("IF root cause VERIFIED AND recurrence mechanically checkable THEN regression test", out["reason"])
        self.assertIn("NOT a test pinning env|config values that worked once", out["reason"])
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
        self.assertIn("warnings (CONFIG['strict']=True blocks them): gitignore:", green["systemMessage"])
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

    def test_a_finding_carried_across_fingerprints_is_logged_once(self):
        self.make_python_project()
        sid = "dup"
        init_session(self.repo, sid)
        finding = {"status": "VERIFIED", "resolved": True, "location": "calc.py:2", "evidence": "sign bug",
                   "learning": {"status": "VERIFIED", "kind": "test", "why": "pinned by test_add"}}
        for body in ("return a + b + 0", "return a + b + 1 - 1"):
            self.write("calc.py", f"def add(a, b):\n    # VERIFIED: direct addition.\n    {body}\n")
            gate(self.repo, sid, ROOT)
            fp = code_fingerprint(self.repo, ["calc.py"])
            self.attest(sid, fp, [finding])
            self.attest_docs(sid, fp)
            self.assertTrue(gate(self.repo, sid, ROOT).done)
        entries = episode_entries(self.repo)
        self.assertEqual(sum(" DONE " in e for e in entries), 2)
        self.assertEqual(sum("FINDING calc.py:2" in e for e in entries), 1, "the second DONE re-lists the same finding")

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

    def test_e2e_paths_rerun_e2e_only_for_scoped_or_build_files(self):
        self.make_python_project(e2e=f"echo run >> e2e.txt && {E2E_CMD}", config=", 'e2e_paths':['web/*']")
        init_session(self.repo, "s3c")
        runs = lambda: (self.repo / "e2e.txt").read_text().count("run")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        self.assertIn("GATE 2/3 review", gate(self.repo, "s3c", ROOT).text)
        self.assertEqual(runs(), 1, "no green e2e yet in this session")
        self.write("test_calc.py", (self.repo / "test_calc.py").read_text() + "\n# unit test edit\n")
        self.assertIn("GATE 2/3 review", gate(self.repo, "s3c", ROOT).text)
        self.assertEqual(runs(), 1, "a file outside e2e_paths reuses the green e2e")
        self.write("web/app.py", "PAGE = 'sum'\n")
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        self.assertIn("GATE 1/3 verify: FAIL", gate(self.repo, "s3c", ROOT).text)
        self.assertEqual(runs(), 2, "a scoped file reruns e2e")
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        self.assertIn("GATE 2/3 review", gate(self.repo, "s3c", ROOT).text)
        self.assertEqual(runs(), 2, "e2e that passed beside a failing unit test stays cached")
        self.write("pyproject.toml", "[project]\nname = 'calc'\n")
        gate(self.repo, "s3c", ROOT)
        self.assertEqual(runs(), 3, "a build file always reruns e2e")
        with (self.repo / ".agents/VERIFY.py").open("a") as f:
            f.write("# policy edit\n")
        gate(self.repo, "s3c", ROOT)
        self.assertEqual(runs(), 4, "a policy edit reruns e2e")

    def test_without_e2e_paths_every_code_edit_reruns_e2e(self):
        self.make_python_project(e2e=f"echo run >> e2e.txt && {E2E_CMD}")
        init_session(self.repo, "s3d")
        runs = lambda: (self.repo / "e2e.txt").read_text().count("run")
        self.write("calc.py", "def add(a, b):\n    return b + a\n")
        gate(self.repo, "s3d", ROOT)
        self.write("test_calc.py", (self.repo / "test_calc.py").read_text() + "\n# unit test edit\n")
        gate(self.repo, "s3d", ROOT)
        self.assertEqual(runs(), 2)

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
        self.assertEqual((session_dir(self.repo, "big") / "gate.txt").read_text(), text)
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
        with (self.repo / ".agents/CLI_GIST.md").open("a") as f:
            f.write("- VERIFIED[deploy] `fly deploy --app demo`\n")
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "same"}
        ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CI runs async: after a push report the run URL and finish, NOT wait for it", ctx)
        self.assertIn("- VERIFIED[db] migrations run via `make migrate`", ctx)
        self.assertIn("- VERIFIED[deploy] `fly deploy --app demo`", ctx)
        self.assertNotIn("VERIFIED[scope] fact", ctx)
        self.assertNotIn(".agents/EPISODES.md (", ctx)  # no open episode, no section
        for rule in ("IF a runtime fact is unknown or contested THEN observe", "Never promote or widen by rhetoric|repetition|recency|detail",
                     "an entry binds only in its evidenced `[scope]`, below the current request"):
            self.assertIn(rule, ctx)
        self.assertIn(f"gate --repo {self.repo} --session same", ctx)
        self.assertIn("start on `bun` (JS/TS, `bun init`) or `uv` (Python, `uv init`)", ctx)
        self.assertIn("ISO-8601 UTC timestamp first", ctx)
        for rule in ("tests assert VERIFIED requirements only", "HYPOTHESIS → probe, UNKNOWN → ask, neither gets a test",
                     "test the untested public behaviors + main error paths, not lines",
                     "a regression test only for a VERIFIED code defect, never pinning env|config values that worked once"):
            self.assertIn(rule, ctx)
        self.assertIn("`uv run --env-file .env`", ctx)
        self.assertLess(len(ctx), 9001)
        s1 = read_json(state_path(self.repo, "same"))
        self.write("calc.py", "def add(a,b): return a+b\n")
        session_start(event, ROOT)
        s2 = read_json(state_path(self.repo, "same"))
        self.assertEqual(s1["baseline"], s2["baseline"])
        self.assertIn("calc.py", changed_since(self.repo, s2["baseline"]))

    def test_session_folders_are_named_by_start_time_and_keep_their_session(self):
        a = session_dir(self.repo, "2f5972a7-7149-4ace-8d4b-ea4c5a52b8b4")
        b = session_dir(self.repo, "other")
        self.assertRegex(a.name, r"^\d{4}-\d\d-\d\d_\d\d_\d\d_\d\d_\d+$")
        self.assertNotEqual(a, b)
        self.assertNotIn("2f5972a7", "".join(p.name for p in a.parent.iterdir()), "no folder carries the id")
        # A session starting in an occupied second takes the next number (seconds ahead are taken too: the clock ticks).
        for t in (time.time(), time.time() + 1, time.time() + 2):
            (a.parent / (time.strftime("%Y-%m-%d_%H_%M_%S", time.gmtime(t)) + "_1")).mkdir(exist_ok=True)
        c = session_dir(self.repo, "third")
        self.assertNotIn(c, (a, b))
        self.assertGreater(int(c.name.rsplit("_", 1)[1]), 1, c.name)
        # The mapping survives a new process: the hooks run one per event.
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from vae_state import session_dir; "
                "print(session_dir(__import__('pathlib').Path(sys.argv[2]), 'other'))")
        p = subprocess.run([PY, "-c", code, str(ROOT / "scripts"), str(self.repo)], text=True, capture_output=True, check=True)
        self.assertEqual(p.stdout.strip(), str(b))
        init_session(self.repo, "other")
        self.assertEqual(latest_session(self.repo), "other", "the CLI default resolves the id, not the folder name")
        # A damaged index line must not redirect writes outside tmp/vae/.
        with open(a.parent / "sessions.tsv", "a") as fh:
            fh.write("../../escape\tevil\n")
        self.assertEqual(session_dir(self.repo, "evil").parent, a.parent)

    def test_session_start_injects_only_the_newest_open_episodes(self):
        init_project(self.repo, ROOT)
        append_episodes(self.repo, "a", ["LESSON oldest open lesson", "FAIL tests.unit", "DONE fp=1 cov=90.0% paths=a.py",
                                         "FINDING a.py:f learn=test: pinned by test_f"], ROOT)
        append_episodes(self.repo, "b", ["FINDING b.py:g learn=none: UNKNOWN whether g needs a lock", "FAIL lint",
                                         "LESSON HYPOTHESIS[cache] falsified BC probe", "LESSON HYPOTHESIS[cache] falsified BC probe"], ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "c"}
        tail = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"].split("Open .agents/EPISODES.md")[1]
        # Open = unencoded lesson|finding or a FAIL its session never turned green; newest 3, each once, as leads.
        self.assertIn("leads to re-check, not rules", tail)
        for line in ("learn=none: UNKNOWN whether g needs a lock", "s=b FAIL lint", "LESSON HYPOTHESIS[cache]"):
            self.assertIn(line, tail)
        self.assertEqual(tail.count("LESSON HYPOTHESIS[cache]"), 1)
        for line in ("oldest open lesson", "FAIL tests.unit", "DONE fp=1", "learn=test"):
            self.assertNotIn(line, tail)

    def test_session_start_injects_no_gate_noise_as_a_lead(self):
        init_project(self.repo, ROOT)
        append_episodes(self.repo, "a", ["FAIL lint", "DONE fp=1 cov=90.0% paths=a.py", "FINDING a.py:f learn=test: pinned"], ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "c"}
        self.assertNotIn("Open .agents/EPISODES.md", session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"])

    def test_full_memory_is_cut_between_entries_and_the_rest_is_named(self):
        # The probe that found the silent cut: rules + a MEMORY.md within its 4 KiB budget overflowed 9000 chars, so the
        # last entry was split mid-line and CLI_GIST and the leads vanished without a trace.
        self.make_python_project()
        init_project(self.repo, ROOT)
        memory = [f"- VERIFIED[m{i:02d}] fact {i} " + "x" * 150 + " BC probe" for i in range(22)]
        self.write(".agents/MEMORY.md", "# Agent memory\n" + "\n".join(memory) + "\n")
        self.write(".agents/CLI_GIST.md", "# CLI gist\n- VERIFIED[deploy] `fly deploy --app demo`\n")
        append_episodes(self.repo, "s", ["LESSON HYPOTHESIS[cache] falsified BC probe"], ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "c"}
        ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 9000)
        shown = [m for m in memory if m in ctx]
        self.assertTrue(shown and all(m in ctx.splitlines() for m in shown), "entries arrive whole or not at all")
        self.assertIn("… context full; read the rest: ", ctx.splitlines()[-1])
        for source in (".agents/EPISODES.md",) + (() if "fly deploy" in ctx else (".agents/CLI_GIST.md",)):
            self.assertIn(source, ctx.splitlines()[-1])
        # A small repo state fits whole: no cut line, and the detected stack's defaults arrive.
        self.write(".agents/MEMORY.md", "# Agent memory\n" + memory[0] + "\n")
        ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("context full", ctx)
        self.assertIn("python lint: `uv run ruff check .`", ctx)
        self.assertNotIn("golangci-lint", ctx, "only the stacks present")

    def test_session_start_shows_the_live_swarm_first(self):
        init_project(self.repo, ROOT)
        (self.repo / ".agents/SWARM_STATUS.yaml").write_text(
            "agents:\n- name: crashed\n  pid: 999999\n  goal: Port the parser.\n  workdir: ../c\n  target_focus_paths: [src/p]\n"
            "  start_timestamp: 2026-01-01T00:00:00Z\n")
        with (self.repo / ".agents/MEMORY.md").open("a") as f:
            f.write("- VERIFIED[db] pool size 4 BC load test\n")
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "c"}
        ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LOST crashed: Port the parser. (workdir ../c)", ctx)
        self.assertLess(ctx.index("Swarm .agents/SWARM_STATUS.yaml"), ctx.index(".agents/MEMORY.md:"), "agents before memory")

    def test_attestations_fail_closed_on_every_incomplete_field(self):
        # One mutation per field of an otherwise valid attestation; each is rejected with its own reason.
        fp, path = "f" * 64, self.repo / "attestation.json"
        finding = {"status": "VERIFIED", "resolved": True, "location": "calc.py:1", "evidence": "e",
                   "learning": {"status": "VERIFIED", "kind": "test", "why": "w"}}
        review = {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "checklist": REVIEW_CHECKLIST,
                  "reviewed_paths": ["calc.py"], "findings": [finding]}
        write_json(path, review)
        self.assertEqual(validate_review(path, fp, ["calc.py"]), (True, "review attestation VERIFIED"))
        for reason, change in {
            "review checklist incomplete": {"checklist": REVIEW_CHECKLIST[:-1]},
            "reviewed_paths missing/invalid": {"reviewed_paths": [1]},
            "reviewed_paths incomplete: calc.py": {"reviewed_paths": []},
            "review findings must be list": {"findings": {}},
            "review finding invalid": {"findings": ["x"]},
            "all review findings must be VERIFIED+resolved": {"findings": [dict(finding, resolved=False)]},
            "each review finding needs location+evidence": {"findings": [dict(finding, evidence=" ")]},
            "each finding needs learning status/action": {"findings": [dict(finding, learning={})]},
            "learning.kind invalid": {"findings": [dict(finding, learning={"status": "VERIFIED", "kind": "vibes"})]},
        }.items():
            write_json(path, {**review, **change})
            self.assertEqual(validate_review(path, fp, ["calc.py"]), (False, reason), reason)
        entry = {"path": "calc.py", "status": "VERIFIED", "file": "updated", "method": "updated", "inline": "updated",
                 "alternative": "a", "why": "VERIFIED: w"}
        docs = {"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [entry]}
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        for reason, change in {
            "docs attestation stale or status!=VERIFIED": {"code_fingerprint": "0" * 64},
            "docs files must be list": {"files": {}},
            "docs missing file assessment: calc.py": {"files": []},
            "docs assessment not VERIFIED: calc.py": {"files": [dict(entry, status="UNKNOWN")]},
            "docs why missing epistemic label: calc.py": {"files": [dict(entry, why="because")]},
            "docs alternative missing: calc.py": {"files": [dict(entry, alternative=" ")]},
            "docs method not assessed: calc.py": {"files": [dict(entry, method="maybe")]},
            "all-not-applicable docs rationale must be VERIFIED: calc.py": {"files": [dict(
                entry, file="not-applicable", method="not-applicable", inline="not-applicable", why="UNKNOWN: w")]},
            "updated docs lack epistemic prefix in source: calc.py": {},
        }.items():
            write_json(path, {**docs, **change})
            self.assertEqual(validate_docs(path, fp, ["calc.py"], self.repo), (False, reason), reason)
        self.write("calc.py", "def add(a, b):\n    # VERIFIED: plain addition.\n    return a + b\n")
        self.assertTrue(validate_docs(path, fp, ["calc.py"], self.repo)[0])
        self.assertEqual(validate_docs(path, fp, ["README.md"]), (True, "docs ∅ production source changed"))

    def test_hooks_pass_unrelated_events_but_deny_a_commit_without_a_baseline(self):
        self.make_python_project()
        self.write("calc.py", "def add(a, b):\n    return b + a\n")  # dirty code, and no SessionStart ever ran

        def event(**kw):
            return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
                    "cwd": str(self.repo), "session_id": "nobase", **kw}

        self.assertIsNone(commit_gate(event(tool_name="Read")), "only shell tools commit")
        self.assertIsNone(commit_gate(event(tool_input={"command": "git status"})), "not a commit")
        outside = str(self.repo.parent)
        self.assertIsNone(commit_gate(event(cwd=outside)), "outside a repository nothing is gated")
        self.assertIsNone(stop_gate({"hook_event_name": "Stop", "cwd": outside, "session_id": "x"}, ROOT))
        self.assertIsNone(session_start({"hook_event_name": "SessionStart", "cwd": outside, "session_id": "x"}, ROOT))
        # Fail closed: without a recorded baseline every dirty file counts as this session's change.
        self.assertEqual(commit_gate(event())["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(read_json(state_path(self.repo, "nobase"))["baseline_status"], "HYPOTHESIS")
        sh("git checkout -- calc.py", self.repo)
        self.assertIsNone(commit_gate(event()), "nothing changed since the baseline: commit allowed")

    def test_adapter_ignores_non_json_and_unknown_events(self):
        self.make_python_project()

        def adapter(stdin: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, str(ROOT / "hooks/lifecycle.py")], input=stdin, text=True, capture_output=True, check=False)

        for stdin in ("not json", json.dumps({"hook_event_name": "Notification", "cwd": str(self.repo), "session_id": "a"})):
            self.assertEqual((adapter(stdin).returncode, adapter(stdin).stdout), (0, ""), stdin)
        start = json.loads(adapter(json.dumps({"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "a"})).stdout)
        self.assertIn("gate --repo", start["hookSpecificOutput"]["additionalContext"])

    def test_cli_gate_uses_the_latest_session_and_commits_do_not_escape_it(self):
        self.make_python_project()
        init_session(self.repo, "quiet")
        p = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "gate", "--repo", str(self.repo)], text=True, capture_output=True, check=False)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("∅ code|doc changes since session baseline", p.stdout)
        # A commit made during the session (by a human, say) still counts as changed since the baseline.
        self.write("calc.py", "def add(a, b):\n    return a + b + 0\n")
        sh("git commit -qam change", self.repo)
        self.assertIn("calc.py", changed_since(self.repo, read_json(state_path(self.repo, "quiet"))["baseline"]))

    def test_open_episodes_share_the_section_budget_newest_first(self):
        init_project(self.repo, ROOT)
        event = {"hook_event_name": "SessionStart", "cwd": str(self.repo), "session_id": "c"}

        def section(entries: list[str]) -> list[str]:
            (self.repo / ".agents/EPISODES.md").unlink()
            append_episodes(self.repo, "s", entries, ROOT)
            ctx = session_start(event, ROOT)["hookSpecificOutput"]["additionalContext"]
            body = ctx.split("Open .agents/EPISODES.md (leads to re-check, not rules):\n")[1]
            self.assertLessEqual(len(body), 1024)
            return body.split("\n")

        long = "x" * 2000
        # An older oversized LESSON must not erase newer leads: each gets an equal share, newest first.
        lines = section([f"LESSON a {long}", f"LESSON b {long}", f"LESSON c {long}"])
        self.assertEqual([ln.split(" ")[3] for ln in lines], ["c", "b", "a"])
        self.assertTrue(all(ln.endswith("…") for ln in lines))
        # Fewer leads use the unused budget; a short one stays whole.
        newest, short = section(["LESSON short lead", f"LESSON b {long}"])
        self.assertTrue(short.endswith("LESSON short lead"))
        self.assertTrue(newest.endswith("…") and len(newest) > 1024 // 3)
        self.assertEqual(len(section([f"LESSON a {long}"])[0]), 1024)

    def test_episodes_stay_bounded(self):
        append_episodes(self.repo, "s", [f"FAIL x{i}" for i in range(EPISODE_KEEP + 5)], ROOT)
        text = (self.repo / ".agents/EPISODES.md").read_text()
        entries = episode_entries(self.repo)
        self.assertEqual(len(entries), EPISODE_KEEP)
        self.assertTrue(entries[0].endswith("FAIL x5") and text.startswith("# Episodes"))

    def test_trim_drops_oldest_gate_noise_and_keeps_every_lead(self):
        leads = ["LESSON oldest lesson BC probe", "FINDING a.py:f learn=none: UNKNOWN lock", "NOTE unknown kind"]
        append_episodes(self.repo, "s", leads + ["FINDING b.py:g learn=test: pinned", "DONE fp=1"], ROOT)
        append_episodes(self.repo, "s", [f"FAIL x{i}" for i in range(EPISODE_KEEP)], ROOT)
        bodies = [e.split(" ", 2)[2] for e in episode_entries(self.repo)]
        self.assertEqual(len(bodies), EPISODE_KEEP)
        self.assertEqual(bodies[:3], leads, "leads are never trimmed by age")
        self.assertNotIn("DONE fp=1", bodies)
        self.assertNotIn("FINDING b.py:g learn=test: pinned", bodies)
        self.assertEqual(bodies[3], "FAIL x3", "the oldest noise goes first")
        # Leads alone may exceed the window; doctor's lead cap, not the trim, bounds them.
        append_episodes(self.repo, "s", [f"LESSON l{i} BC x" for i in range(EPISODE_KEEP)], ROOT)
        bodies = [e.split(" ", 2)[2] for e in episode_entries(self.repo)]
        self.assertEqual(len(bodies), EPISODE_KEEP + 3)
        self.assertFalse(any(b.startswith("FAIL ") for b in bodies))

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


    def test_adapter_fails_closed_when_the_install_is_broken(self):
        # A copy of the payload whose gate modules cannot import: commits are still denied, other commands pass.
        broken = Path(self.td.name) / "broken-plugin"
        shutil.copytree(ROOT, broken, ignore=shutil.ignore_patterns("__pycache__"))
        (broken / "scripts/vae_gate.py").write_text("this is not python\n")
        self.make_python_project()

        def hook(command: str) -> str:
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(self.repo), "session_id": "b"}
            return subprocess.run([PY, str(broken / "hooks/lifecycle.py")], input=json.dumps(event), text=True, capture_output=True, check=False).stdout

        denied = json.loads(hook("git -C . commit -m x"))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("could not run", denied["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(hook("ls -la"), "", "a command that cannot be a commit never loads the gate")

if __name__ == "__main__":
    unittest.main()
