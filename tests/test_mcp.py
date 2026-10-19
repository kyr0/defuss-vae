"""vae_mcp: a real stdio server subprocess and a real streamable-HTTP server on 127.0.0.1; no mocks."""
from __future__ import annotations

import json

from research_kit import HttpFixture, as_json, mcp_http, stdio_server
from vae_mcp import McpClient, McpError, from_config, result_text, server_specs
from vae_net import ConfigError
from vae_testkit import RepoCase

SECRET = "fixture-secret-123456"


class StdioTests(RepoCase):
    def client(self, **extra) -> McpClient:
        self.write(".vae/.env.mcp", f"FIX_TOKEN={SECRET}\n")
        self.write(".vae/.env.mcp.json", json.dumps({"servers": {"fx": stdio_server(**extra)}, "timeout_s": 10}))
        c = from_config(self.repo)["fx"]
        self.addCleanup(c.close)
        return c

    def test_handshake_listing_calls_ping_and_banner(self):
        c = self.client()
        self.assertEqual([t["name"] for t in c.list_tools()], ["echo", "fail", "token", "exit"])
        self.assertEqual(c.server_info["name"], "fixture-stdio")
        # the server pings and notifies before this reply and printed a banner at start: the client skips and answers
        self.assertEqual(result_text(c.call("echo", {"text": "hi"})), "hi")
        self.assertEqual(result_text(c.call("token", {})), SECRET, "${FIX_TOKEN} in `env` comes from the secret file")
        failed = c.call("fail", {})
        self.assertTrue(failed["isError"])
        with self.assertRaisesRegex(McpError, r"fx: tools/call: JSON-RPC error -32602: unknown tool nope"):
            c.call("nope", {})

    def test_a_server_that_exits_reports_its_code_and_stderr(self):
        c = self.client()
        with self.assertRaisesRegex(McpError, r"server exited \(code 3\); stderr: fixture log line"):
            c.call("exit", {})

    def test_a_missing_command_is_an_error_not_a_crash(self):
        self.write(".vae/.env.mcp.json", json.dumps({"servers": {"fx": {"command": "/nonexistent/server"}}}))
        with self.assertRaisesRegex(McpError, "fx: cannot start /nonexistent/server"):
            from_config(self.repo)["fx"].list_tools()


class HttpTests(RepoCase):
    def test_session_header_protocol_version_paging_and_sse_replies(self):
        srv = HttpFixture(mcp_http({"echo": lambda a: {"content": [{"type": "text", "text": a["text"]}]},
                                    "struct": lambda a: {"content": [], "structuredContent": {"n": 1}}}))
        self.addCleanup(srv.close)
        self.write(".vae/.env.mcp", f"FIX_TOKEN={SECRET}\n")
        self.write(".vae/.env.mcp.json", json.dumps({"servers": {"web": {
            "type": "streamable-http", "url": srv.url + "/mcp", "headers": {"Authorization": "Bearer ${FIX_TOKEN}"}, "alwaysAllow": ["x"]}}}))
        c = from_config(self.repo)["web"]
        self.addCleanup(c.close)
        self.assertEqual([t["name"] for t in c.list_tools()], ["echo", "struct"], "two pages joined")
        self.assertEqual(result_text(c.call("echo", {"text": "over sse"})), "over sse")
        self.assertEqual(result_text(c.call("struct", {})), '{"n": 1}')
        first, later = srv.requests[0][2], srv.requests[-1][2]
        self.assertEqual(first["Authorization"], f"Bearer {SECRET}")
        self.assertNotIn("Mcp-Session-Id", first)
        self.assertEqual((later["Mcp-Session-Id"], later["Mcp-Protocol-Version"]), ("s-1", "2025-06-18"))
        self.assertIn("text/event-stream", later["Accept"])
        self.assertEqual(srv.requests[1][3]["method"], "notifications/initialized")

    def test_errors_are_redacted(self):
        srv = HttpFixture(lambda m, p, h, b: as_json({"echo": h.get("Authorization")}, 500))
        self.addCleanup(srv.close)
        self.write(".vae/.env.mcp", f"FIX_TOKEN={SECRET}\n")
        self.write(".vae/.env.mcp.json", json.dumps({"servers": {"web": {
            "type": "http", "url": srv.url, "headers": {"Authorization": "Bearer ${FIX_TOKEN}"}}}}))
        with self.assertRaises(McpError) as ctx:
            from_config(self.repo)["web"].list_tools()
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertIn("Bearer ***", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))


class ConfigTests(RepoCase):
    def specs(self, servers):
        self.write(".vae/.env.mcp.json", json.dumps({"servers": servers}))
        return server_specs(self.repo)[0]

    def test_unconfigured_and_disabled(self):
        self.assertEqual(server_specs(self.repo), ({}, {}, []))
        self.assertEqual(self.specs({"a": {"command": "x", "disabled": True}}), {})

    def test_invalid_entries_name_the_server(self):
        for servers, why in (({"a": {"type": "sse", "url": "http://x"}}, "transport 'sse'"),
                             ({"a": {"type": "stdio"}}, "needs `command`"),
                             ({"a": {"type": "http"}}, "needs `url`"),
                             ({"a": {"url": "http://x", "headers": {"A": "${NOPE_UNSET_VAR}"}}}, r"\$\{NOPE_UNSET_VAR\} is not set"),
                             ({"a": "x"}, "must be an object")):
            with self.subTest(why=why), self.assertRaisesRegex(ConfigError, f"servers.a.*{why}"):
                self.specs(servers)
        self.write(".vae/.env.mcp.json", '{"servers": []}')
        with self.assertRaisesRegex(ConfigError, "`servers` must be an object"):
            server_specs(self.repo)

    def test_defaults_and_kinds(self):
        s = self.specs({"a": {"command": "srv"}, "b": {"url": "http://x", "timeout_s": 5}})
        self.assertEqual((s["a"]["type"], s["a"]["timeout_s"], s["a"]["start_timeout_s"]), ("stdio", 30.0, 120.0))
        self.assertEqual((s["b"]["type"], s["b"]["timeout_s"]), ("http", 5.0))

    def test_result_text_names_non_text_blocks(self):
        r = {"content": [{"type": "text", "text": "a"}, {"type": "image", "data": "x"},
                         {"type": "resource", "resource": {"uri": "u://1", "text": "doc"}}]}
        self.assertEqual(result_text(r), "a\n[image block]\ndoc")
