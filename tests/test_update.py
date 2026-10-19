"""vae_update: the plugin's version, the `.agents/` → `.vae/` move and the cached release check. Real git repos, a real
HTTP server on 127.0.0.1 and real detached processes; no mocks."""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from vae_testkit import PY, ROOT, RepoCase, sh
from vae_update import (
    check,
    check_due,
    check_enabled,
    installed_version,
    migrate,
    migrate_notice,
    start_check,
    update_notice,
)


class Server:
    """A real HTTP server whose answers the test sets per path: (status, body, delay seconds)."""

    def __init__(self):
        routes: dict[str, tuple[int, str, float]] = {}
        self.routes = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body, delay = routes.get(self.path, (404, "missing", 0.0))
                time.sleep(delay)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class MigrateTests(RepoCase):
    def test_moves_only_defuss_vae_files_keeps_others_and_rewrites_the_ignore(self):
        self.write(".agents/VERIFY.py", "CONFIG = {}\nRULES = []\n")
        self.write(".agents/MEMORY.md", "# memory\n")
        self.write(".agents/skills/other-tool/SKILL.md", "---\nname: other\n---\n")
        self.write(".gitignore", "tmp/*\n.agents/SWARM_STATUS.yaml\n")
        self.commit_all()
        self.write(".agents/EPISODES.md", "# episodes\n")  # untracked
        self.write(".agents/__pycache__/VERIFY.cpython-314.pyc", "x")
        code, lines = migrate(self.repo)
        self.assertEqual(code, 0, lines)
        for name in ("VERIFY.py", "MEMORY.md", "EPISODES.md"):
            self.assertTrue((self.repo / ".vae" / name).is_file(), name)
            self.assertFalse((self.repo / ".agents" / name).exists(), name)
        self.assertTrue((self.repo / ".agents/skills/other-tool/SKILL.md").is_file(), "another tool's file stays")
        self.assertFalse((self.repo / ".agents/__pycache__").exists())
        self.assertIn(".vae/SWARM_STATUS.yaml", (self.repo / ".gitignore").read_text().splitlines())
        self.assertNotIn(".agents/SWARM_STATUS.yaml", (self.repo / ".gitignore").read_text())
        staged = sh("git status --porcelain", self.repo).stdout
        self.assertIn("R  .agents/VERIFY.py -> .vae/VERIFY.py", staged, "tracked files move as staged renames")
        self.assertTrue(any("kept .agents/" in ln for ln in lines), lines)
        self.assertEqual(migrate(self.repo), (0, ["∅ no defuss-vae state in .agents/"]), "a second run changes nothing")

    def test_an_empty_legacy_folder_is_removed_and_a_folder_ignore_gains_a_twin(self):
        self.write(".agents/MEMORY.md", "# memory\n")
        self.write(".gitignore", ".agents/\n")
        code, lines = migrate(self.repo)
        self.assertEqual(code, 0, lines)
        self.assertFalse((self.repo / ".agents").exists())
        self.assertEqual((self.repo / ".gitignore").read_text().splitlines(), [".agents/", ".vae/"])

    def test_stops_without_change_when_both_folders_hold_state(self):
        self.write(".agents/MEMORY.md", "old\n")
        self.write(".vae/MEMORY.md", "new\n")
        code, lines = migrate(self.repo)
        self.assertEqual(code, 2)
        self.assertIn("STOP", lines[0])
        self.assertEqual((self.repo / ".agents/MEMORY.md").read_text(), "old\n")
        self.assertEqual((self.repo / ".vae/MEMORY.md").read_text(), "new\n")

    def test_session_notice_names_the_command_or_asks_the_human(self):
        self.assertIsNone(migrate_notice(self.repo))
        self.write(".agents/VERIFY.py", "")
        self.assertIn("vae.py update migrate --repo", migrate_notice(self.repo, ROOT))
        self.write(".vae/VERIFY.py", "")
        self.assertIn("ask the human which to keep", migrate_notice(self.repo, ROOT))

    def test_cli_migrate_refreshes_an_existing_agents_block_only(self):
        self.write(".agents/MEMORY.md", "# memory\n")
        self.write("AGENTS.md", "# Project\n\n<!-- defuss-vae:start -->\nold `.agents/` rules\n<!-- defuss-vae:end -->\n")
        out = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "update", "migrate", "--repo", str(self.repo)],
                             capture_output=True, text=True, check=False)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("VERIFIED[update.migrate]=true", out.stdout)
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertIn("Read `.vae/MEMORY.md`", agents)
        self.assertNotIn("old `.agents/` rules", agents)


class ReleaseCheckTests(RepoCase):
    def setUp(self):
        super().setUp()
        self.server = Server()
        self.cache = self.repo / "cache" / "update.json"
        self.installed = installed_version(ROOT)

    def tearDown(self):
        self.server.close()
        super().tearDown()

    def test_newer_release_is_cached_and_announced_once_found(self):
        self.server.routes["/m.json"] = (200, json.dumps({"version": "99.0.0"}), 0.0)
        self.assertTrue(check_due(self.cache))
        self.assertEqual(check(self.server.base + "/m.json", self.cache), "99.0.0")
        self.assertFalse(check_due(self.cache))
        self.assertTrue(check_due(self.cache, now=time.time() + 25 * 3600))
        self.assertIn("defuss-vae 99.0.0 is released", update_notice(self.installed, self.cache))
        self.server.routes["/m.json"] = (200, json.dumps({"version": self.installed}), 0.0)
        check(self.server.base + "/m.json", self.cache)
        self.assertIsNone(update_notice(self.installed, self.cache), "the same version is not news")

    def test_a_failed_check_keeps_the_last_version_and_records_the_attempt(self):
        self.server.routes["/m.json"] = (200, json.dumps({"version": "99.0.0"}), 0.0)
        check(self.server.base + "/m.json", self.cache)
        for route in ((404, "gone", 0.0), (200, "not json", 0.0), (200, json.dumps({"name": "x"}), 0.0), (200, "{}", 1.5)):
            self.server.routes["/m.json"] = route
            self.assertEqual(check(self.server.base + "/m.json", self.cache, timeout=0.5), "99.0.0", route)
            record = json.loads(self.cache.read_text())
            self.assertIn("error", record, route)
            self.assertFalse(check_due(self.cache), "an offline machine retries daily, not every session")

    def test_opt_out_and_a_detached_check_that_never_blocks(self):
        old = os.environ.get("VAE_UPDATE_CHECK")
        try:
            os.environ["VAE_UPDATE_CHECK"] = "0"
            self.assertFalse(check_enabled())
            os.environ["VAE_UPDATE_CHECK"] = "1"
            self.assertTrue(check_enabled())
        finally:
            os.environ["VAE_UPDATE_CHECK"] = old if old is not None else "0"
        self.server.routes["/slow.json"] = (200, json.dumps({"version": "99.1.0"}), 1.0)
        env_keys = {"VAE_UPDATE_URL": self.server.base + "/slow.json", "XDG_CACHE_HOME": str(self.repo / "xdg")}
        saved = {k: os.environ.get(k) for k in env_keys}
        try:
            os.environ.update(env_keys)
            t = time.time()
            start_check(ROOT)
            self.assertLess(time.time() - t, 0.5, "the session start must not wait for the network")
            cache = self.repo / "xdg" / "defuss-vae" / "update.json"
            deadline = time.time() + 10
            while time.time() < deadline and not cache.exists():
                time.sleep(0.1)
            self.assertEqual(json.loads(cache.read_text())["latest"], "99.1.0")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_cli_version_and_check(self):
        out = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "update", "version"], capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), self.installed)
        manifest = self.repo / "plugin.json"
        manifest.write_text(json.dumps({"version": "99.2.0"}))
        env = {**os.environ, "VAE_UPDATE_URL": manifest.as_uri(), "XDG_CACHE_HOME": str(self.repo / "xdg2")}
        out = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "update", "check"], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(out.returncode, 0)
        self.assertIn("defuss-vae 99.2.0 is released", out.stdout)
        self.assertTrue(Path(self.repo / "xdg2/defuss-vae/update.json").is_file())
