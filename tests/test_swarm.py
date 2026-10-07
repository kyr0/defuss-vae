"""Swarm registry: detached jobs, owner-only writes, disjoint claims, liveness from the process table, drift repair."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from vae_testkit import ROOT, RepoCase, sh  # first: puts plugin/scripts on sys.path

# isort: split
from vae_swarm import (
    SWARM_FILE,
    dump,
    heal,
    parse,
    read,
    resources,
    size_bytes,
    status,
    stop,
    summary,
    swarm_root,
    upsert,
)

CLI = [sys.executable, str(ROOT / "scripts/vae.py"), "swarm"]


class SwarmTests(RepoCase):
    def swarm(self, *args: str, cwd: Path | None = None, settle: str = "0.2") -> subprocess.CompletedProcess[str]:
        cmd = [*CLI, args[0], "--repo", str(cwd or self.repo), "--settle", settle, *args[1:]]
        return subprocess.run(cmd, cwd=cwd or self.repo, text=True, capture_output=True, check=False)

    def spawn(self, name: str, targets: str, *job: str, workdir: str = ".") -> subprocess.CompletedProcess[str]:
        return self.swarm("spawn", "--name", name, "--goal", f"{name} goal.", "--workdir", workdir, "--targets", targets,
                          "--eta", "5", "--", *job)

    def entry(self, name: str) -> dict:
        return next(e for e in read(self.repo) if e["name"] == name)

    def wait_for(self, name: str, field: str, timeout: float = 15) -> object:
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = next((e.get(field) for e in read(self.repo) if e.get("name") == name), None)
            if value is not None:
                return value
            time.sleep(0.1)
        self.fail(f"{name}.{field} stayed unset")

    def tearDown(self):
        for e in read(self.repo):
            if e.get("exit_code") is None:
                self.swarm("stop", "--name", str(e["name"]))
        super().tearDown()

    def test_spawned_job_is_detached_logged_per_line_and_reports_its_exit(self):
        p = self.spawn("hello", "src/a", "sh", "-c", "echo hi; sleep 0.5; exit 3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        pid = self.entry("hello")["pid"]
        self.assertEqual(os.getpgid(pid), pid, "own session and process group: a dying parent shell cannot take it down")
        self.assertEqual(self.wait_for("hello", "exit_code"), 3)
        log = (self.repo / "var/log/swarm/hello.log").read_text().splitlines()
        self.assertTrue(all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", ln) for ln in log), log)
        self.assertEqual([ln.split(" ", 1)[1] for ln in log[1:]], ["hi", "EXIT 3"])
        st = self.swarm("status")
        self.assertEqual(st.returncode, 1, "an exited agent needs the orchestrator: merge, then reap")
        self.assertIn("EXITED hello pid=", st.stdout)
        self.assertEqual(self.swarm("rm", "--name", "hello").returncode, 0)
        self.assertEqual(read(self.repo), [])

    def test_claims_must_be_disjoint(self):
        self.assertEqual(self.spawn("a", "src/a", "sleep", "30").returncode, 0)
        clash = self.spawn("b", "src", "sleep", "30")
        self.assertEqual(clash.returncode, 2)
        self.assertIn("CONFLICT", clash.stdout)
        self.assertIn("workdir", clash.stdout, "the same workdir collides too")
        self.assertIn("target src", clash.stdout)
        (self.repo.parent / "wt-b").mkdir()
        self.assertIn("target src", self.spawn("b", "src", "sleep", "30", workdir="../wt-b").stdout, "nested targets collide")
        self.assertEqual(self.spawn("b", "src/b", "sleep", "30", workdir="../wt-b").returncode, 0)
        self.assertEqual({e["name"] for e in read(self.repo)}, {"a", "b"})

    def test_only_the_owner_writes_a_live_entry(self):
        self.spawn("job", "src/a", "sleep", "30")
        denied = self.swarm("set", "--name", "job", "--eta", "1")
        self.assertEqual(denied.returncode, 2)
        self.assertIn("only its owner writes it", denied.stdout)
        self.assertIn("running", self.swarm("rm", "--name", "job").stdout)
        # Registering needs a pid the caller owns: this test process is an ancestor of the CLI, a stranger is not.
        mine = self.swarm("set", "--name", "me", "--pid", str(os.getpid()), "--goal", "Own entry.", "--workdir", "../me",
                          "--targets", "docs")
        self.assertEqual(mine.returncode, 0, mine.stdout)
        stranger = subprocess.Popen(["sleep", "30"])
        try:
            other = self.swarm("set", "--name", "x", "--pid", str(stranger.pid), "--goal", "Stolen.", "--workdir", "../x",
                               "--targets", "lib")
            self.assertIn("neither this process nor an ancestor", other.stdout)
            self.assertIn("not started by `vae.py swarm spawn`", self.swarm("stop", "--name", "me").stdout,
                          "stop never signals a process group it did not create")
        finally:
            stranger.kill()
            stranger.wait()
        stopped = self.swarm("stop", "--name", "job")
        self.assertIn("stopped (exit 143)", stopped.stdout, "SIGTERM reaches the job; the wrapper records 128+15")
        self.assertEqual(self.swarm("rm", "--name", "job").returncode, 0, "a finished entry is anyone's to reap")

    def test_status_finds_dead_and_reused_pids_and_fix_records_them(self):
        gone = subprocess.run(["sh", "-c", "echo $$"], text=True, capture_output=True, check=True)
        reused = subprocess.Popen(["sleep", "30"])  # alive, but started long after the entry claims
        try:
            # Hand-written block YAML, unquoted, with a block list: the reader must take what agents write.
            (self.repo / ".agents").mkdir(exist_ok=True)
            (self.repo / SWARM_FILE).write_text(
                "agents:\n"
                f"  - name: crashed\n    pid: {gone.stdout.strip()}\n    goal: Crashed before reporting.\n    workdir: ../c\n"
                "    target_focus_paths:\n      - src/c\n    start_timestamp: 2026-01-01T00:00:00Z\n    exit_code: null\n"
                f"  - name: reused\n    pid: {reused.pid}\n    goal: Its pid now belongs to another program.\n"
                "    workdir: ../r\n    target_focus_paths: [src/r]\n    start_timestamp: 2026-01-01T00:00:00Z\n")
            self.assertEqual(self.entry("crashed")["target_focus_paths"], ["src/c"])
            st = self.swarm("status")
            self.assertEqual(st.returncode, 1)
            self.assertIn("LOST crashed", st.stdout)
            self.assertIn("LOST reused", st.stdout)
            fixed = self.swarm("status", "--fix")
            self.assertIn("FIXED crashed exit_code=lost", fixed.stdout)
            self.assertEqual({e["name"]: e["exit_code"] for e in read(self.repo)}, {"crashed": "lost", "reused": "lost"})
            self.assertTrue((self.repo / SWARM_FILE).read_text().startswith("# Live sub-agents"), "rewritten by the CLI")
        finally:
            reused.kill()
            reused.wait()

    def test_a_write_lost_to_a_racing_editor_is_reapplied(self):
        job = subprocess.Popen([*CLI, "set", "--repo", str(self.repo), "--settle", "1.5", "--name", "me", "--pid",
                                str(os.getpid()), "--goal", "Survive a race.", "--workdir", "../me", "--targets", "docs"],
                               text=True, stdout=subprocess.PIPE)
        deadline = time.time() + 10
        while time.time() < deadline and not any(e.get("name") == "me" for e in read(self.repo)):
            time.sleep(0.02)
        (self.repo / SWARM_FILE).write_text("agents: []\n")  # another agent's stale copy lands within the settle delay
        out, _ = job.communicate(timeout=30)
        self.assertEqual(job.returncode, 0, out)
        self.assertEqual(self.entry("me")["goal"], "Survive a race.")

    def test_worktrees_share_the_main_checkouts_registry(self):
        sh("git commit -q --allow-empty -m init", self.repo)
        wt = self.repo.parent / "repo.wt" / "a"
        sh(f"git worktree add -q {wt} -b swarm/a", self.repo)
        self.assertEqual(swarm_root(wt), self.repo)
        p = self.swarm("set", "--name", "a", "--pid", str(os.getpid()), "--goal", "Work in a worktree.", "--workdir",
                       "../repo.wt/a", "--targets", "src/a", cwd=wt)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertTrue((self.repo / SWARM_FILE).exists())
        self.assertFalse((wt / SWARM_FILE).exists(), "no divergent copy inside the worktree")

    def test_resource_estimates_beyond_free_capacity_are_refused(self):
        res = resources(self.repo)
        self.assertGreater(res["disk_free"], 0)
        self.assertGreaterEqual(res["cpus"], 1)
        p = self.swarm("spawn", "--name", "big", "--goal", "Too big.", "--workdir", ".", "--targets", "data", "--disk", "999999T",
                       "--", "true")
        self.assertEqual(p.returncode, 2)
        self.assertIn("RESOURCES disk", p.stdout)
        self.assertEqual((size_bytes("512M"), size_bytes("2G"), size_bytes("300B"), size_bytes(4)), (2**29, 2**31, 300, 2**22))

    def test_status_names_every_state(self):
        live = subprocess.Popen(["sleep", "60"])  # a real process the entries point at
        try:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            base = {"pid": live.pid, "goal": "G.", "start_timestamp": now}
            entries = [
                dict(base, name="malformed", goal=None, workdir="../m", target_focus_paths=["m"]),
                dict(base, name="foreign", host="elsewhere.invalid", workdir="../f", target_focus_paths=["f"]),
                dict(base, name="nopid", pid="n/a", workdir="../n", target_focus_paths=["n"]),
                dict(base, name="overdue", workdir="../o", target_focus_paths=["o"], eta_in_mins=0.01),
                dict(base, name="stalled", workdir="../s", target_focus_paths=["s"], estimated_ram_usage="999T"),
                dict(base, name="running", workdir="../r", target_focus_paths=["r"], start_timestamp="not a time", eta_in_mins=30),
            ]
            (self.repo / ".agents").mkdir(exist_ok=True)
            (self.repo / SWARM_FILE).write_text(dump(entries))
            log = self.repo / "var/log/swarm/stalled.log"
            log.parent.mkdir(parents=True)
            log.write_text("2026-01-01T00:00:00Z last words\n")
            silent = time.time() - 20 * 60
            os.utime(log, (silent, silent))
            time.sleep(1.2)  # past the 0.6 s eta
            code, lines = status(self.repo, delay=0)
            self.assertEqual(code, 1)
            for line in ("MALFORMED malformed", "UNKNOWN foreign", "UNKNOWN nopid", "OVERDUE overdue", "STALLED stalled",
                         "RUNNING running", "OVERCOMMIT live RAM claims exceed total RAM"):
                self.assertTrue(any(ln.startswith(line) for ln in lines), (line, lines))
            self.assertEqual([ln.split()[0] for ln in summary(self.repo)],
                             ["MALFORMED", "UNKNOWN", "UNKNOWN", "OVERDUE", "STALLED", "RUNNING"])
            (self.repo / SWARM_FILE).write_text("agents: []\n")
            self.assertEqual(summary(self.repo), [], "an empty registry costs a session nothing")
        finally:
            live.kill()
            live.wait()

    def test_stop_kills_a_job_that_ignores_sigterm(self):
        self.assertEqual(self.spawn("stubborn", "src/s", "sh", "-c", "trap '' TERM; sleep 30").returncode, 0)
        time.sleep(0.3)  # the trap is installed
        self.assertEqual(stop(self.repo, "stubborn", grace=0.5), (0, "stubborn: killed (SIGKILL after 0.5 s)"))
        self.assertEqual(self.entry("stubborn")["exit_code"], 137, "the wrapper died with it, so stop records the kill")
        self.assertEqual(stop(self.repo, "stubborn"), (0, "stubborn: not running"))
        self.assertEqual(stop(self.repo, "ghost")[0], 2)

    def test_bad_requests_are_refused_and_a_missing_command_exits_127(self):
        self.assertEqual(self.spawn("typo", "src/t", "no-such-command-xyz").returncode, 0)
        self.assertEqual(self.wait_for("typo", "exit_code"), 127)
        self.assertIn(" EXIT 127 ", (self.repo / "var/log/swarm/typo.log").read_text())
        me = ("--pid", str(os.getpid()))
        refused = {
            "name taken": self.spawn("typo", "src/u", "true"),
            "missing: `git worktree add": self.spawn("nowt", "src/w", "true", workdir="../missing-wt"),
            "nogoal: missing goal": self.swarm("spawn", "--name", "nogoal", "--workdir", ".", "--targets", "src/g", "--", "true"),
            "no command": self.swarm("spawn", "--name", "nocmd", "--goal", "G.", "--workdir", ".", "--targets", "src/c"),
            "--name is required": self.swarm("rm"),
            "half: missing goal": self.swarm("set", "--name", "half", *me, "--workdir", "../h", "--targets", "src/h"),
            "RESOURCES disk": self.swarm("set", "--name", "huge", *me, "--goal", "G.", "--workdir", "../hg", "--targets", "src/hg",
                                         "--disk", "999999T"),
        }
        for text, p in refused.items():
            self.assertEqual(p.returncode, 2, (text, p.stdout))
            self.assertIn(text, p.stdout)
        self.assertEqual(self.swarm("set", "--name", "mine", *me, "--goal", "G.", "--workdir", "../mine", "--targets", "src/mine").returncode, 0)
        clash = self.swarm("set", "--name", "clash", *me, "--goal", "G.", "--workdir", "../c2", "--targets", "src/mine/sub")
        self.assertIn("CONFLICT mine: target src/mine/sub", clash.stdout)
        stranger = subprocess.Popen(["sleep", "30"])
        try:
            moved = self.swarm("set", "--name", "mine", "--pid", str(stranger.pid))
            self.assertIn("an entry moves only to a pid you own", moved.stdout)
        finally:
            stranger.kill()
            stranger.wait()
        self.assertIn("ghost: no entry", self.swarm("rm", "--name", "ghost").stdout, "reaping is idempotent")

    def test_a_spawned_session_never_runs_wrap(self):
        # wrap commits; a headless `claude -p "/defuss-vae:wrap"` or `codex exec '$wrap'` would run it without the human.
        for i, prompt in enumerate(("/defuss-vae:wrap", "run(/defuss-vae:wrap)", "then `/wrap`", "$wrap now", "$defuss-vae:wrap")):
            p = self.spawn(f"closer{i}", f"src/z{i}", "echo", prompt)
            self.assertEqual(p.returncode, 2, prompt)
            self.assertIn("wrap is human-only", p.stdout)
        self.assertEqual(self.spawn("lib", "src/lib", "echo", "/usr/lib/wrap.py").returncode, 0, "a path ending in wrap calls no skill")

    def test_a_running_wrapper_restores_its_dropped_entry(self):
        code, _ = upsert(self.repo, "self", {"pid": os.getpid(), "goal": "Heal.", "workdir": "../self",
                                             "target_focus_paths": ["src/self"]}, delay=0)
        self.assertEqual(code, 0)
        done = threading.Event()
        healer = threading.Thread(target=heal, args=(self.repo, "self", done, 0.05), daemon=True)
        healer.start()
        try:
            time.sleep(0.3)  # the heal loop has seen the entry and kept a copy
            (self.repo / SWARM_FILE).write_text("agents: []\n")  # another editor's stale copy drops it
            deadline = time.time() + 5
            while time.time() < deadline and not read(self.repo):
                time.sleep(0.05)
            self.assertEqual(self.entry("self")["goal"], "Heal.")
        finally:
            done.set()
            healer.join(2)

    def test_dump_round_trips_and_keeps_unknown_fields(self):
        entries = [{"name": "a", "pid": 1, "goal": 'Quotes " and: colons.', "target_focus_paths": ["x", "y"], "note": "kept"}]
        self.assertEqual([{k: v for k, v in e.items() if v is not None} for e in parse(dump(entries))], entries)
        self.assertEqual(parse(dump([])), [])
        self.assertEqual(parse("agents:\n- name: 'quoted'\n  goal: plain words\n")[0], {"name": "quoted", "goal": "plain words"})
        self.assertIsNone(size_bytes("lots"))


if __name__ == "__main__":
    unittest.main()
