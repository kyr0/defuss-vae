"""Swarm registry: detached jobs, owner-only writes, disjoint claims, liveness from the process table, drift repair."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

from vae_testkit import ROOT, RepoCase, sh  # first: puts plugin/scripts on sys.path

# isort: split
from vae_swarm import SWARM_FILE, dump, parse, read, resources, size_bytes, swarm_root

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

    def test_dump_round_trips_and_keeps_unknown_fields(self):
        entries = [{"name": "a", "pid": 1, "goal": 'Quotes " and: colons.', "target_focus_paths": ["x", "y"], "note": "kept"}]
        self.assertEqual([{k: v for k, v in e.items() if v is not None} for e in parse(dump(entries))], entries)
        self.assertEqual(parse(dump([])), [])


if __name__ == "__main__":
    unittest.main()
