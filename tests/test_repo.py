"""vae_repo history archives: gzip + .meta.json chains, their check, and the per-day bundles. Real files, no mocks."""
from __future__ import annotations

import gzip
import json
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from vae_testkit import ROOT  # first: puts plugin/scripts on sys.path

# isort: split
from vae_repo import ARCHIVE_POINTER, archive_copy, bundle_old, history_problems

assert ROOT


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.src = self.dir / "MEMORY.md"
        self.folder = self.dir / "history"

    def tearDown(self):
        self.td.cleanup()

    def chain(self) -> list[dict]:
        out, cur = [], json.loads((self.folder / ARCHIVE_POINTER).read_text())["MEMORY.md"]
        while cur:
            meta = json.loads((self.folder / f"{cur}.meta.json").read_text())
            out.append({**meta, "name": cur, "text": gzip.decompress((self.folder / cur).read_bytes()).decode()})
            cur = meta["prev"]
        return out

    def test_every_version_is_kept_newest_first_through_prev(self):
        self.assertIsNone(archive_copy(self.src, self.folder, "snapshot"), "nothing to archive yet")
        for text in ("- one\n", "- one\n- two\n", "- two\n"):
            self.src.write_text(text)
            self.assertIsNotNone(archive_copy(self.src, self.folder, "alter"))
        self.assertIsNone(archive_copy(self.src, self.folder, "snapshot"), "identical content is archived once")
        chain = self.chain()
        self.assertEqual([c["text"] for c in chain], ["- two\n", "- one\n- two\n", "- one\n"])
        self.assertIsNone(chain[-1]["prev"])
        self.assertEqual({c["reason"] for c in chain}, {"alter"})
        self.assertEqual(history_problems(self.folder), [])

    def test_a_damaged_or_missing_archive_is_named(self):
        for text in ("a\n", "b\n"):
            self.src.write_text(text)
            archive_copy(self.src, self.folder, "alter")
        newest, older = self.chain()[0]["name"], self.chain()[1]["name"]
        (self.folder / newest).write_bytes(gzip.compress(b"edited later\n"))
        (self.folder / older).write_bytes(b"not gzip")
        problems = history_problems(self.folder)
        self.assertTrue(any("does not match its sha256" in p and newest in p for p in problems), problems)
        self.assertTrue(any("does not decompress" in p and older in p for p in problems), problems)
        (self.folder / older).unlink()
        self.assertTrue(any(f"{older} is missing" in p for p in history_problems(self.folder)))

    def test_bundles_week_old_archives_per_day_without_losing_a_byte(self):
        for text in ("x\n", "y\n"):
            self.src.write_text(text)
            archive_copy(self.src, self.folder, "overwrite")
        loose = sorted(p.name for p in self.folder.iterdir() if p.name.endswith((".gz", ".meta.json")))
        originals = {name: (self.folder / name).read_bytes() for name in loose}
        self.assertEqual(bundle_old(self.folder), [], "nothing is a week old yet")
        later = time.time() + 8 * 86400
        (self.folder / ".bundled").write_text(time.strftime("%Y-%m-%d", time.gmtime(later)) + "\n")
        self.assertEqual(bundle_old(self.folder, now=later), [], "one scan per day: that day's scan already ran")
        done = bundle_old(self.folder, now=later, force=True)
        self.assertEqual(len(done), 1, done)
        bundles = list(self.folder.glob("*.tar.gz"))
        self.assertEqual(len(bundles), 1)
        self.assertFalse(any((self.folder / name).exists() for name in loose), "loose copies go after bundling")
        with tarfile.open(bundles[0]) as tar:
            self.assertEqual({m.name: tar.extractfile(m).read() for m in tar.getmembers()}, originals)


if __name__ == "__main__":
    unittest.main()
