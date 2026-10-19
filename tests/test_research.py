"""vae_research: whole runs on a scripted LLM, the stdio MCP fixture and a decision server (all real local processes and
sockets), budgets, secrets, the doctor checks and `research init`."""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess

from research_kit import HttpFixture, llm, stdio_server, typesafe, write_pairs
from vae_annotate import page_data
from vae_project import doctor_repo
from vae_research import endpoint_checks, init, run_research
from vae_testkit import PY, ROOT, RepoCase

SECRET = "fixture-secret-123456"
CAPS = {"websearch": [{"server": "fx", "tool": "echo", "args": {"text": "results for $query: https://example.org/$query"}}],
        "paper_search": [{"server": "fx", "tool": "fail"},
                         {"server": "fx", "tool": "echo", "args": {"text": "paper on $query https://doi.example/1"}}],
        "repo_search": [{"server": "fx", "tool": "token"}]}  # a server that echoes a secret back


def phase(body) -> str:
    system = body["messages"][0]["content"]
    for marker, name in (("find evidence", "evidence"), ("verify evidence", "reconcile"), ("Split and re-print", "split"),
                         ("Assume the thoughts provided are wrong", "deduce"), ("compares reasoning paths", "synthesize")):
        if marker in system:
            return name
    return "other"


class RunTests(RepoCase):
    def setUp(self):
        super().setUp()
        self.calls: list[str] = []

    def serve(self, script, mcp=True, decide=True) -> None:
        def recorded(body):
            self.calls.append(phase(body))
            return script(body)
        srv = HttpFixture(llm(recorded, key=SECRET))
        self.addCleanup(srv.close)
        self.dec = dec = HttpFixture(typesafe(key=SECRET))
        self.addCleanup(dec.close)
        write_pairs(self.write, llm_url=srv.url, decide_url=dec.url if decide else None, secret=SECRET,
                    mcp={"servers": {"fx": stdio_server()}, "capabilities": CAPS, "timeout_s": 10} if mcp else None)

    def run_it(self, **kw):
        out: list[str] = []
        code, folder = run_research(self.repo, "Does alpha imply A?", out=out.append, **kw)
        return code, folder, out

    def test_a_whole_run_cites_its_sources_routes_closed_questions_and_keeps_secrets_out(self):
        def script(body):
            p, system, user = phase(body), body["messages"][0]["content"], body["messages"][1]["content"]
            if p == "evidence":
                assert "paper_search: FIRST for an academic question" in system and "not academic" in system, system
                return ('Premises. <tool_use_required>[{"tool": "websearch", "query": "alpha"}, '
                        '{"tool": "experiment", "code": "print(6*7)"}, {"tool": "nope"}, '
                        '{"tool": "repo_search", "repo": "o/r", "query": "q"}]</tool_use_required>')
            if p == "reconcile":
                assert "results for alpha" in system and "exit 0\\n42" in system, system[-2000:]
                return 'Consistent. <tool_use_required>\n```json\n[{"tool": "paper_search", "query": "beta"}]\n```\n</tool_use_required>'
            if p == "split":
                assert "typed-decision" in system
                return json.dumps([{"question": "Is alpha closed?", "labels": ["yes", "no"]}, "Open A?", "Open B?"])
            if p == "deduce":
                if user == "Open A?" and "results for gamma" not in system:
                    return '<tool_use_required>[{"tool": "websearch", "query": "gamma"}]</tool_use_required>'
                return f"<deduction_1>...</deduction_1><deductive_conclusion>{user[5]} holds</deductive_conclusion>"
            if p == "synthesize":
                assert 'Typed decision on "Is alpha closed?": yes (confidence 0.95)' in system
                return "4.1. Final Answer: A. P(correct): 0.91"
            return "?"

        self.serve(script)
        code, folder, out = self.run_it()
        self.assertEqual(code, 0, out)
        self.assertTrue(out[-1].startswith("VERIFIED[research]=true BC report=tmp/vae/research/"), out[-1])
        report = (folder / "report.md").read_text()
        self.assertIn("Final Answer: A", report)
        self.assertIn("### 1. Open A?\n\nA holds", report)
        self.assertIn("### 2. Open B?\n\nB holds", report)
        self.assertIn("- Is alpha closed?: yes (confidence 0.95)", report, "the closed question went to the decision model")
        self.assertRegex(report, r"1\. `websearch` via fx\.echo \(query=alpha\), \S+: <https://example\.org/alpha>")
        self.assertRegex(report, r"`paper_search` via fx\.echo \(query=beta\).*<https://doi\.example/1>", "fell back after fx.fail")
        self.assertEqual(sorted(p.name for p in (folder / "chains").iterdir()), ["01.md", "02.md"], "no chain for the decided question")
        self.assertEqual((folder / "experiments" / "02.py").read_text(), "print(6*7)")
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual((meta["status"], meta["tool_calls"], meta["exhausted"]), ("done", 5, False))
        self.assertIn('"text": "***"', (folder / "evidence.jsonl").read_text(), "the echoed secret is scrubbed")
        self.assertEqual(meta["tools"][-3:], ["experiment", "decide", "ask"])
        decision = self.dec.requests[-1][3]
        self.assertEqual(decision["questions"]["answer"]["instructions"], "Is alpha closed?")
        self.assertIn("results for alpha", decision["state"], "the decision reads the run's evidence")
        self.assertEqual(meta["decision_usage"], {"calls": 1, "input_tokens": 100, "output_tokens": 7})
        self.assertEqual(self.calls.count("deduce"), 3, "Open A? took one tool round")
        for f in folder.rglob("*"):
            if f.is_file():
                self.assertNotIn(SECRET, f.read_text(errors="replace"), f)
        self.assertEqual((self.repo / "tmp/vae/research/.gitignore").read_text(), "*\n")
        page = page_data(folder / "report.html")
        rel = str(folder.relative_to(self.repo))
        self.assertEqual((page["markdown"], page["comments"]), (report, []), "the human comments on the report itself")
        self.assertEqual(page["meta"], {"kind": "research", "run": rel, "source": f"{rel}/report.md", "snapshot": f"{rel}/report.md", "findings": {}})
        self.assertEqual(out[-2], f"PAGE: {rel}/report.html (the human's review page)")

    def test_budgets_bound_the_tool_calls_and_the_rounds(self):
        def script(body):
            if phase(body) == "deduce":
                return '<tool_use_required>[{"tool": "websearch", "query": "more"}]</tool_use_required>'
            return '["Only question?"]' if phase(body) == "split" else "nothing to request"

        self.serve(script, decide=False)
        code, folder, out = self.run_it(max_rounds=3, max_tool_calls=1)
        self.assertEqual(code, 2)
        self.assertTrue(out[-1].startswith("UNKNOWN[research.budget] BC the 1 tool calls ran out; report="), out[-1])
        self.assertTrue((folder / "report.md").is_file())
        self.assertEqual(self.calls, ["evidence", "reconcile", "split"] + ["deduce"] * 4 + ["synthesize"])
        self.assertEqual(json.loads((folder / "meta.json").read_text())["tool_calls"], 1)
        self.calls.clear()
        code, folder, out = self.run_it(max_rounds=2, max_tool_calls=50)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls.count("deduce"), 3)
        self.assertEqual(json.loads((folder / "meta.json").read_text())["tool_calls"], 2, "the last round requests no tool it cannot read")

    def test_only_configured_systems_are_offered_and_used(self):
        def script(body):
            if phase(body) == "evidence":
                assert "websearch" not in body["messages"][0]["content"].split("These tools exist")[1]
                return '<tool_use_required>[{"tool": "websearch", "query": "x"}]</tool_use_required>'
            if phase(body) == "split":
                assert "typed-decision" not in body["messages"][0]["content"]
                return '["Q?"]'
            return "<deductive_conclusion>done</deductive_conclusion>"

        self.serve(script, mcp=False, decide=False)
        code, folder, out = self.run_it(experiments=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((folder / "meta.json").read_text())["tools"], ["ask"])
        self.assertFalse((folder / "evidence.jsonl").exists(), "an unknown tool is an error result, never a call")
        self.assertIn("(no tool result was used)", (folder / "report.md").read_text())

    def test_missing_config_and_empty_answers_stop_with_a_reason(self):
        code, folder, out = self.run_it()
        self.assertEqual((code, folder), (2, None))
        self.assertIn(".vae/.env.openai.json is missing", out[-1])
        self.serve(lambda b: {"text": "", "finish_reason": "length"}, mcp=False, decide=False)
        code, folder, out = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("empty answer (finish_reason=length", out[-1])
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual(meta["status"], "failed")
        self.assertIn("raise max_tokens", meta["error"])

    def test_the_cli_relays_questions_for_the_human_without_a_terminal(self):
        def script(body):
            if phase(body) == "evidence":
                return '<tool_use_required>[{"tool": "ask", "question": "Which alpha do you mean?"}]</tool_use_required>'
            return '["Q?"]' if phase(body) == "split" else "<deductive_conclusion>done</deductive_conclusion>"

        self.serve(script, mcp=False, decide=False)
        p = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "research", "run", "--repo", str(self.repo), "--no-experiments",
                            "What", "is", "alpha?"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ASK: Which alpha do you mean?", p.stdout)
        report = next((self.repo / "tmp/vae/research").glob("*/report.md")).read_text()
        self.assertTrue(report.startswith("# Research: What is alpha?\n"))
        self.assertIn("## Questions for the human\n\n- Which alpha do you mean?", report)


class DoctorTests(RepoCase):
    def ids(self, checks):
        return {c.id: c for c in checks}

    def test_nothing_configured_adds_no_checks(self):
        self.assertEqual(endpoint_checks(self.repo), [])
        self.assertFalse(any(c.id.startswith("research.") for c in doctor_repo(self.repo)))

    def test_live_endpoints_pass_and_each_fault_fails_its_check(self):
        blind = []

        def sees(b):
            """Answers the attachment probes only from what the attachment holds, and nothing once blind."""
            content = b["messages"][-1]["content"]
            for part in content if isinstance(content, list) and not blind else []:
                if part["type"] == "image_url":
                    png = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                    return "left=red, right=blue" if png.startswith(b"\x89PNG") else "a blur"
                if part["type"] == "file":
                    return "4711" if b"4711" in base64.b64decode(part["file"]["file_data"].split(",", 1)[1]) else "nothing"
            return "ok"
        srv = HttpFixture(llm(sees, key=SECRET))
        self.addCleanup(srv.close)
        dec = HttpFixture(typesafe(key=SECRET))
        self.addCleanup(dec.close)
        caps = {"websearch": [{"server": "fx", "tool": "echo"}], "vision": [{"model": "vision"}]}
        write_pairs(self.write, llm_url=srv.url, decide_url=dec.url, secret=SECRET, vision_caps=["vision", "pdf", "video"],
                    mcp={"servers": {"fx": stdio_server()}, "capabilities": caps})
        self.write(".gitignore", ".vae/.env.*\n")
        checks = self.ids(endpoint_checks(self.repo))
        self.assertEqual(sorted(checks), ["research.capability.pdf", "research.capability.video", "research.capability.vision",
                                          "research.capability.websearch", "research.config.mcp",
                                          "research.config.openai", "research.config.typed-decisions", "research.mcp.fx",
                                          "research.model.vision", "research.openai", "research.secrets-ignored",
                                          "research.typed-decisions"])
        self.assertTrue(all(c.passes() for c in checks.values()), [c for c in checks.values() if not c.passes()])
        self.assertIn("4 tools", checks["research.mcp.fx"].evidence)
        self.assertEqual(checks["research.typed-decisions"].evidence, "jev-latest: yes (0.95)")
        self.assertEqual(checks["research.model.vision"].evidence, "image: left=red, right=blue; pdf: 4711; not probed: video")
        self.assertIn("research.openai", self.ids(doctor_repo(self.repo)))

        self.write(".gitignore", "")
        caps["websearch"] = [{"server": "fx", "tool": "no_such_tool"}]
        write_pairs(self.write, mcp={"servers": {"fx": stdio_server()}, "capabilities": caps}, secret=SECRET)
        self.write(".vae/.env.openai.json", json.loads(json.dumps((self.repo / ".vae/.env.openai.json").read_text())).replace("m-vision", "m-gone"))
        blind.append(True)
        checks = self.ids(endpoint_checks(self.repo))
        self.assertFalse(checks["research.secrets-ignored"].passes())
        self.assertIn("not served: ['m-gone']", checks["research.openai"].evidence)
        self.assertIn("image: the answer names no red and blue half", checks["research.model.vision"].evidence)
        self.assertIn("fx.no_such_tool: no such tool", checks["research.capability.websearch"].evidence)
        decide_json = self.repo / ".vae/.env.typed-decisions.json"
        self.write(".vae/.env.typed-decisions.json", decide_json.read_text().replace("jev-latest", "jev-gone"))
        self.assertIn("model 'jev-gone' not served", self.ids(endpoint_checks(self.repo))["research.typed-decisions"].evidence)
        dec.close()
        self.assertFalse(self.ids(endpoint_checks(self.repo))["research.typed-decisions"].passes())
        openai_json = self.repo / ".vae/.env.openai.json"
        self.write(".vae/.env.openai.json", openai_json.read_text().replace('"video"', '"teleport"'))
        self.assertIn("'teleport' is not a capability a model serves", self.ids(endpoint_checks(self.repo))["research.models"].evidence)
        self.write(".vae/.env.openai.json", "{broken")
        self.assertIn("invalid JSON", self.ids(endpoint_checks(self.repo))["research.config.openai"].evidence)


class InitTests(RepoCase):
    def test_scaffolds_six_files_never_overwrites_and_ignores_the_secrets(self):
        self.write(".vae/.env.openai", "OPENAI_API_KEY=mine\n")
        done = init(self.repo, ROOT)
        self.assertEqual(done, [".vae/.env.openai.json", ".vae/.env.mcp", ".vae/.env.mcp.json", ".vae/.env.typed-decisions",
                                ".vae/.env.typed-decisions.json", ".gitignore: + .vae/.env.*"])
        self.assertEqual((self.repo / ".vae/.env.openai").read_text(), "OPENAI_API_KEY=mine\n", "never overwritten")
        self.assertEqual(stat.S_IMODE(os.stat(self.repo / ".vae/.env.mcp").st_mode), 0o600)
        self.assertEqual(init(self.repo, ROOT), [], "a second run changes nothing")
        checks = self.ids(endpoint_checks(self.repo))
        self.assertTrue(checks["research.secrets-ignored"].passes())
        self.assertIn("models.reasoner needs a non-empty `name`", checks["research.config.openai"].evidence, "fill-in is enforced")
        self.assertTrue(checks["research.config.mcp"].passes(), "the template's servers ship disabled")
        self.assertIn("`TYPED_DECISIONS_API_KEY` is empty", checks["research.config.typed-decisions"].evidence)
        self.assertNotIn("research.typed-decisions", checks, "an unfilled key never reaches the network")
        self.assertEqual(sorted(k for k in checks if k.startswith("research.capability.")),
                         ["research.capability.ocr", "research.capability.repo_file", "research.capability.vision",
                          "research.capability.webscrape"], "only the builtins remain while every server is disabled")

    def ids(self, checks):
        return {c.id: c for c in checks}
