"""Shared fixtures: real temp git repos, a real shell, the plugin on sys.path (no mocks)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "plugin"  # the installable payload; repo root holds maintainer files only
sys.path.insert(0, str(ROOT / "scripts"))

from vae_verify import Check, VerifyReport

PY = sys.executable
TEST_CMD = f"{PY} -m unittest discover -p 'test_*.py'"
LINT_CMD = f"{PY} -m py_compile calc.py"
E2E_CMD = "mkdir -p output && echo 5 > output/sum.txt"  # the gate requires fresh output/ evidence


def sh(cmd: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=cwd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if check and p.returncode:
        raise AssertionError(f"cmd failed {cmd}: {p.stdout}")
    return p


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = Path(self.td.name) / "repo"
        self.repo.mkdir()
        self.repo = self.repo.resolve()  # macOS: /var → /private/var; git reports resolved paths
        sh("git init -q", self.repo)
        sh("git config user.email test@example.invalid", self.repo)
        sh("git config user.name Test", self.repo)

    def tearDown(self):
        self.td.cleanup()

    def write(self, rel: str, text: str) -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit_all(self, msg="init"):
        sh("git add -A && git commit -qm " + msg, self.repo)

    def make_python_project(self, test_command: str = TEST_CMD, coverage: str = "75", rules: str = "[]", layout: bool = False):
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        self.write("test_calc.py", "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n")
        self.write(".agents/VERIFY.py", (
            f"CONFIG={{'coverage_min':60, 'test_command':{test_command!r}, 'lint_command':{LINT_CMD!r}, 'e2e_commands':[{E2E_CMD!r}],\n"
            f" 'coverage_command':\"{PY} -c \\\"print('TOTAL {coverage}%')\\\"\", 'timeout_s':60, 'layout':{layout}}}\n"
            f"RULES={rules}\n"
        ))
        self.commit_all()

    def check(self, report: VerifyReport, cid: str) -> Check:
        return next(c for c in report.checks if c.id == cid)
