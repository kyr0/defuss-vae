"""vae_net: config pairs, variable expansion, request templates and HTTP with retries against a real local server."""
from __future__ import annotations

import io
import os
import unittest
import urllib.error

from research_kit import HttpFixture, as_json
from vae_net import (
    ConfigError,
    expand,
    fill,
    http_error,
    iter_sse,
    load_env,
    load_pair,
    open_request,
    redact,
    request_json,
)
from vae_testkit import RepoCase


class PairTests(RepoCase):
    def test_env_file_rules(self):
        p = self.write(".vae/.env.x", "# comment\n\nexport A=1\nB='two words'\nC=\"q\"\nnot a pair\nD = spaced \n")
        self.assertEqual(load_env(p), {"A": "1", "B": "two words", "C": "q", "D": "spaced"})
        self.assertEqual(load_env(self.repo / "missing"), {})

    def test_pair_is_none_without_json_and_the_file_wins_over_the_environment(self):
        self.assertEqual(load_pair(self.repo, "x"), (None, {}))
        self.write(".vae/.env.x.json", '{"a": 1}')
        self.write(".vae/.env.x", "VAE_TEST_KEY=from-file\n")
        os.environ["VAE_TEST_KEY"], os.environ["VAE_TEST_ONLY_ENV"] = "from-env", "env-only"
        try:
            settings, env = load_pair(self.repo, "x")
        finally:
            del os.environ["VAE_TEST_KEY"], os.environ["VAE_TEST_ONLY_ENV"]
        self.assertEqual(settings, {"a": 1})
        self.assertEqual((env["VAE_TEST_KEY"], env["VAE_TEST_ONLY_ENV"]), ("from-file", "env-only"))

    def test_invalid_json_names_the_file(self):
        self.write(".vae/.env.x.json", "{nope")
        with self.assertRaisesRegex(ConfigError, r"\.vae/\.env\.x\.json: invalid JSON"):
            load_pair(self.repo, "x")
        self.write(".vae/.env.x.json", "[1]")
        with self.assertRaisesRegex(ConfigError, "must be an object"):
            load_pair(self.repo, "x")


class TemplateTests(unittest.TestCase):
    def test_expand_fills_secrets_everywhere_and_names_a_missing_one(self):
        env = {"K": "s3cret", "U": "host"}
        self.assertEqual(expand({"h": ["Bearer ${K}", "$U:1"], "n": 2}, env, "w"), {"h": ["Bearer s3cret", "host:1"], "n": 2})
        with self.assertRaisesRegex(ConfigError, r"w: \$\{GONE\} is not set"):
            expand("x ${GONE}", env, "w")

    def test_fill_keeps_types_drops_absent_and_inlines_text(self):
        t = {"q": "$question", "labels": "$labels", "opt": "$missing", "url": "https://x/$repo/HEAD/$path", "n": 3}
        self.assertEqual(fill(t, {"question": "Q?", "labels": ["a", "b"], "repo": "o/r", "path": "README.md"}),
                         {"q": "Q?", "labels": ["a", "b"], "url": "https://x/o/r/HEAD/README.md", "n": 3})

    def test_redact_hides_only_long_values(self):
        self.assertEqual(redact("key abcdefgh12 and ab", ["abcdefgh12", "ab"]), "key *** and ab")

    def test_sse_joins_multi_line_data_and_skips_other_fields(self):
        stream = io.BytesIO(b": comment\nevent: x\ndata: a\ndata: b\n\ndata:c\r\n\r\ndata: tail")
        self.assertEqual(list(iter_sse(stream)), ["a\nb", "c", "tail"])


class HttpTests(unittest.TestCase):
    def test_retries_transient_statuses_but_not_client_errors(self):
        state = {"n": 0}

        def handler(method, path, headers, body):
            state["n"] += 1
            if path == "/flaky" and state["n"] < 3:
                return as_json({"error": "busy"}, 503)
            return as_json({"ok": path}, 404 if path == "/missing" else 200)

        srv = HttpFixture(handler)
        self.addCleanup(srv.close)
        self.assertEqual(request_json(srv.url + "/flaky", {"x": 1}, attempts=3), {"ok": "/flaky"})
        self.assertEqual(state["n"], 3)
        state["n"] = 0
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            open_request(srv.url + "/missing", attempts=3)
        self.assertEqual(state["n"], 1, "a 404 is not retried")
        self.assertRegex(http_error(ctx.exception), r"HTTP 404 .*\{\"ok\": \"/missing\"\}")
        self.assertEqual(srv.requests[0][3], {"x": 1})
        self.assertEqual(srv.requests[0][2]["Content-Type"], "application/json")

    def test_connection_failures_become_url_errors(self):
        with self.assertRaises(urllib.error.URLError) as ctx:
            open_request("http://127.0.0.1:9/", timeout=2, attempts=2)
        self.assertTrue(http_error(ctx.exception).startswith("URLError"))
