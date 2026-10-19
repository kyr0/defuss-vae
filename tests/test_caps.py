"""vae_caps: capability routing with fallback over a real stdio MCP server, a real HTTP MCP server and a real
OpenAI-compatible server for model providers."""
from __future__ import annotations

import json
import os

from research_kit import HttpFixture, llm, mcp_http, stdio_server, write_pairs
from vae_caps import READ, CapabilityError, Router, from_repo
from vae_net import ConfigError
from vae_testkit import RepoCase

SECRET = "fixture-secret-123456"


class RouterTests(RepoCase):
    def router(self, capabilities, servers=None, llm_url=None, vision_caps=None) -> Router:
        write_pairs(self.write, llm_url=llm_url, secret=SECRET, vision_caps=vision_caps,
                    mcp={"servers": servers or {"fx": stdio_server()}, "capabilities": capabilities, "timeout_s": 10})
        r = from_repo(self.repo)
        self.addCleanup(r.close)
        return r

    def test_first_provider_that_answers_wins_and_failures_are_listed(self):
        web = HttpFixture(mcp_http({"search": lambda a: {"content": [{"type": "text", "text": f"web: {a['q']}"}]}}))
        self.addCleanup(web.close)
        r = self.router({"websearch": [{"server": "fx", "tool": "fail"},
                                       {"server": "fx", "tool": "echo", "args": {"text": ""}},
                                       {"server": "web", "tool": "search", "args": {"q": "$query"}}]},
                        servers={"fx": stdio_server(), "web": {"type": "http", "url": web.url}})
        out = r.call("websearch", query="mcp spec")
        self.assertEqual((out["provider"], out["text"]), ("web.search", "web: mcp spec"))
        self.assertEqual([a["provider"] for a in out["attempts"]], ["fx.fail", "fx.echo"])
        self.assertIn("tool failed on purpose", out["attempts"][0]["error"])
        self.assertIn("empty result", out["attempts"][1]["error"])

    def test_inputs_pass_unchanged_without_args(self):
        r = self.router({"repo_file": [{"server": "fx", "tool": "echo"}]})
        with self.assertRaisesRegex(CapabilityError, "missing input path"):
            r.call("repo_file", repo="o/r")
        # the fixture's echo needs `text`, which repo_file does not have: the tool's empty answer counts as a failure
        with self.assertRaisesRegex(CapabilityError, r"repo_file: every provider failed \(fx.echo: RuntimeError: empty result\)"):
            r.call("repo_file", repo="o/r", path="a")

    def test_unconfigured_and_unknown_capabilities_fail_cleanly(self):
        r = self.router({"pdf": []})
        self.assertEqual(r.available(), {})
        with self.assertRaisesRegex(CapabilityError, "no provider configured"):
            r.call("pdf", url="https://x")
        with self.assertRaisesRegex(CapabilityError, "unknown capability"):
            r.call("teleport", to="mars")

    def test_cloak_missing_falls_through_and_a_model_provider_reads_the_attachment(self):
        def seen(b):
            parts = b["messages"][-1]["content"]
            return f"{b['model']} got {parts[1]['type']} for: {parts[0]['text']}"
        srv = HttpFixture(llm(seen, key=SECRET))
        self.addCleanup(srv.close)
        saved = os.environ.get("CLOAKBROWSER_BINARY_PATH")
        os.environ["CLOAKBROWSER_BINARY_PATH"] = str(self.repo / "no-browser")
        try:
            r = self.router({"webscrape": [{"builtin": "cloak"}, {"server": "fx", "tool": "echo", "args": {"text": "read $url"}}],
                             "vision": [{"model": "vision"}]}, llm_url=srv.url, vision_caps=["vision"])
            out = r.call("webscrape", url="https://example.invalid/")
        finally:
            os.environ.pop("CLOAKBROWSER_BINARY_PATH")
            if saved is not None:
                os.environ["CLOAKBROWSER_BINARY_PATH"] = saved
        self.assertEqual((out["provider"], out["text"]), ("fx.echo", "read https://example.invalid/"))
        self.assertIn("not installed", out["attempts"][0]["error"])
        self.assertEqual(r.call("vision", image="https://example.invalid/a.png", prompt="what?")["text"], "m-vision got image_url for: what?")
        self.assertEqual(r.available(), {"webscrape": ["cloak", "fx.echo"], "vision": ["model:vision"]})

    def test_declared_capabilities_join_each_chain_after_the_map_unless_placed(self):
        def seen(b):
            parts = b["messages"][-1]["content"]
            return f"{parts[1]['type']}: {parts[0]['text']}"
        srv = HttpFixture(llm(seen, key=SECRET))
        self.addCleanup(srv.close)
        r = self.router({"pdf": [{"server": "fx", "tool": "fail"}], "vision": [{"model": "vision"}, {"server": "fx", "tool": "fail"}]},
                        llm_url=srv.url, vision_caps=["vision", "ocr", "pdf", "document"])
        self.assertEqual(r.available(), {"pdf": ["fx.fail", "model:vision"], "vision": ["model:vision", "fx.fail"],
                                         "ocr": ["model:vision"], "document": ["model:vision"]})
        out = r.call("pdf", url="https://example.invalid/a.pdf")
        self.assertEqual((out["provider"], out["text"]), ("model:vision", f"file: {READ}"))
        self.assertTrue(r.call("ocr", image="https://example.invalid/a.png")["text"].startswith("image_url: Extract all text"))
        self.assertEqual(r.call("document", url="https://example.invalid/a.docx")["text"], f"file: {READ}")

    def test_model_declarations_are_validated(self):
        srv = HttpFixture(llm(lambda b: "ok", key=SECRET))
        self.addCleanup(srv.close)
        for caps, declared, why in (({}, ["teleport"], "'teleport' is not a capability a model serves"),
                                    ({"ocr": [{"model": "vision"}]}, ["vision"], "model 'vision' does not declare 'ocr'"),
                                    ({"websearch": [{"model": "vision"}]}, ["vision"], "a model provider names a role")):
            with self.subTest(why=why), self.assertRaisesRegex(ConfigError, why):
                self.router(caps, llm_url=srv.url, vision_caps=declared)
        self.router({"vision": [{"model": "vision"}]}, llm_url=srv.url, vision_caps=["vision"])
        self.write(".vae/.env.openai.json", "{broken")
        self.assertEqual(from_repo(self.repo).available(), {}, "without a valid model config no model provider is offered")


class ParseTests(RepoCase):
    def caps(self, capabilities, servers=None):
        self.write(".vae/.env.mcp.json", json.dumps({"servers": servers or {"fx": stdio_server(), "off": {"command": "x", "disabled": True}},
                                                     "capabilities": capabilities}))
        return from_repo(self.repo).capabilities

    def test_disabled_servers_drop_out(self):
        self.write(".vae/.env.mcp", f"FIX_TOKEN={SECRET}\n")
        caps = self.caps({"websearch": [{"server": "off", "tool": "s"}, {"server": "fx", "tool": "echo"}]})
        self.assertEqual(caps, {"websearch": [{"server": "fx", "tool": "echo"}]})

    def test_invalid_maps_name_the_problem(self):
        self.write(".vae/.env.mcp", f"FIX_TOKEN={SECRET}\n")
        for capabilities, why in (({"teleport": []}, "unknown capability"), ({"pdf": {}}, "must be a list"),
                                  ({"pdf": [{"server": "nope", "tool": "t"}]}, "server 'nope' is not in `servers`"),
                                  ({"pdf": [{"server": "fx"}]}, "needs `tool`"),
                                  ({"pdf": [{"builtin": "magic"}]}, "unknown builtin 'magic'"),
                                  ({"pdf": [{"builtin": "cloak", "server": "fx"}]}, "each provider is"),
                                  ({"pdf": [{"builtin": "cloak", "args": []}]}, "`args` must be an object")):
            with self.subTest(why=why), self.assertRaisesRegex(ConfigError, why):
                self.caps(capabilities)
        self.write(".vae/.env.mcp.json", json.dumps({"servers": {}, "capabilities": []}))
        with self.assertRaisesRegex(ConfigError, "`capabilities` must map"):
            from_repo(self.repo)
