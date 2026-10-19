"""vae_oai: chat over JSON and SSE, images, tools and errors against a real OpenAI-compatible server on 127.0.0.1."""
from __future__ import annotations

import json

from research_kit import HttpFixture, llm, write_pairs
from vae_net import ConfigError
from vae_oai import Llm, LlmError, from_config
from vae_testkit import RepoCase

SECRET = "fixture-secret-123456"


class ChatTests(RepoCase):
    def serve(self, script, vision_caps=None, **kw) -> tuple[HttpFixture, Llm]:
        srv = HttpFixture(llm(script, key=SECRET, **kw))
        self.addCleanup(srv.close)
        write_pairs(self.write, llm_url=srv.url, secret=SECRET, vision_caps=vision_caps)
        return srv, from_config(self.repo)

    def test_streamed_and_plain_replies_carry_text_reasoning_usage(self):
        srv, client = self.serve(lambda b: {"text": "Paris", "reasoning": "capital of France"})
        out = client.chat([{"role": "user", "content": "Capital?"}])
        self.assertEqual((out["text"], out["reasoning"], out["finish_reason"], out["model"]), ("Paris", "capital of France", "stop", "m-reasoner"))
        self.assertEqual(out["usage"]["completion_tokens"], 5)
        body = srv.requests[-1][3]
        self.assertEqual((body["stream"], body["stream_options"], body["max_tokens"]), (True, {"include_usage": True}, 100))
        plain = client.chat([{"role": "user", "content": "Capital?"}], stream=False, temperature=0)
        self.assertEqual((plain["text"], plain["reasoning"]), ("Paris", "capital of France"))
        self.assertEqual(srv.requests[-1][3]["temperature"], 0, "call parameters override the config")

    def test_a_trace_holds_each_request_as_sent_and_its_reply_but_no_header(self):
        replies = iter(["one", {"status": 500, "body": {"error": "down"}}])
        _, client = self.serve(lambda b: next(replies))
        trace: list = []
        messages = [{"role": "user", "content": "first"}]
        client.chat(messages, trace=trace.append)
        messages.append({"role": "assistant", "content": "one"})
        with self.assertRaises(LlmError):
            client.chat(messages, trace=trace.append)
        self.assertEqual([len(t["request"]["messages"]) for t in trace], [1, 2], "each entry keeps its own request")
        self.assertEqual((trace[0]["response"]["text"], trace[0]["method"]), ("one", "POST"))
        self.assertIn("HTTP 500", trace[1]["error"])
        self.assertNotIn(SECRET, json.dumps(trace))
        self.assertNotIn("Authorization", json.dumps(trace))

    def test_tool_calls_join_across_deltas(self):
        call = {"id": "c1", "type": "function", "function": {"name": "calc", "arguments": '{"expr": "17*23"}'}}
        _, client = self.serve(lambda b: {"text": "", "tool_calls": [call]})
        tools = [{"type": "function", "function": {"name": "calc", "parameters": {"type": "object"}}}]
        for stream in (True, False):
            out = client.chat([{"role": "user", "content": "17*23?"}], tools=tools, stream=stream)
            self.assertEqual(out["tool_calls"], [call])
            self.assertEqual(out["finish_reason"], "tool_calls")

    def test_inline_thinking_moves_to_reasoning(self):
        _, client = self.serve(lambda b: "<think>let me see</think>\nanswer")
        out = client.chat([{"role": "user", "content": "q"}])
        self.assertEqual((out["text"], out["reasoning"]), ("answer", "let me see"))

    def test_attachments_join_the_last_user_message_as_their_parts(self):
        srv, client = self.serve(lambda b: "a cat", vision_caps=["vision", "pdf"])
        png = self.write("cat.png", "")
        png.write_bytes(b"\x89PNG fake")
        pdf = self.write("doc.pdf", "%PDF-1.4 fake")
        client.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "What?"}], role="vision",
                    images=[str(png), "https://example.invalid/cat.jpg"],
                    files=[str(pdf), "https://example.invalid/a.pdf", "data:application/pdf;base64,AAAA"],
                    videos=["https://example.invalid/v.mp4"])
        body = srv.requests[-1][3]
        self.assertEqual(body["model"], "m-vision")
        self.assertNotIn("capabilities", body, "a role's capabilities never reach the API")
        parts = body["messages"][-1]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "What?"})
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(parts[2]["image_url"]["url"], "https://example.invalid/cat.jpg")
        self.assertEqual(parts[3], {"type": "file", "file": {"file_data": "data:application/pdf;base64,JVBERi0xLjQgZmFrZQ==",
                                                             "filename": "doc.pdf"}})
        self.assertEqual(parts[4], {"type": "file", "file": {"file_url": "https://example.invalid/a.pdf"}})
        self.assertEqual(parts[5], {"type": "file", "file": {"file_data": "data:application/pdf;base64,AAAA"}})
        self.assertEqual(parts[6], {"type": "video_url", "video_url": {"url": "https://example.invalid/v.mp4"}})
        with self.assertRaisesRegex(LlmError, "file not found"):
            client.chat([{"role": "user", "content": "x"}], images=["missing.png"])
        with self.assertRaisesRegex(LlmError, "a video goes to a model as a URL"):
            client.chat([{"role": "user", "content": "x"}], videos=[str(pdf)])

    def test_errors_are_redacted_and_roles_checked(self):
        _, client = self.serve(lambda b: {"status": 400, "body": {"error": f"bad request for key {SECRET}"}})
        with self.assertRaises(LlmError) as ctx:
            client.chat([{"role": "user", "content": "x"}])
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))
        with self.assertRaisesRegex(ConfigError, "no model for role 'editor'"):
            client.chat([{"role": "user", "content": "x"}], role="editor")
        self.assertEqual(client.models_available(), ["m-reasoner", "m-vision"])

    def test_no_key_sends_no_header_and_a_wrong_key_fails(self):
        srv = HttpFixture(llm(lambda b: "ok"))
        self.addCleanup(srv.close)
        client = Llm({"base_url": srv.url, "models": {"reasoner": {"name": "m"}}}, {})
        self.assertEqual(client.chat([{"role": "user", "content": "x"}])["text"], "ok")
        self.assertNotIn("Authorization", srv.requests[-1][2])
        locked = HttpFixture(llm(lambda b: "ok", key="right-key-123456"))
        self.addCleanup(locked.close)
        with self.assertRaisesRegex(LlmError, "HTTP 401"):
            Llm({"base_url": locked.url, "models": {"reasoner": {"name": "m"}}}, {}).models_available()

    def test_config_is_validated(self):
        self.assertIsNone(from_config(self.repo))
        for settings, why in (({"models": {"r": {"name": "m"}}}, "base_url"), ({"base_url": "http://x"}, "`models`"),
                              ({"base_url": "http://x", "models": {"r": {"name": ""}}}, "models.r needs"),
                              ({"base_url": "http://x", "models": {"r": {"name": "m", "capabilities": "pdf"}}}, "capabilities must be a list")):
            with self.subTest(why=why), self.assertRaisesRegex(ConfigError, why):
                Llm(settings, {})
        self.write(".vae/.env.openai.json", json.dumps({"base_url": "ftp://x", "models": {}}))
        with self.assertRaises(ConfigError):
            from_config(self.repo)
