"""vae_decide: the TypeSafe System One wire contract against a real System One server on 127.0.0.1."""
from __future__ import annotations

from research_kit import HttpFixture, answer_of, as_json, typesafe, write_pairs
from vae_decide import DecideError, Decider, from_config
from vae_net import ConfigError
from vae_testkit import RepoCase

SECRET = "fixture-secret-123456"
KEY = {"TYPED_DECISIONS_API_KEY": "k-123"}


class DecideTests(RepoCase):
    def serve(self, answers=None) -> tuple[HttpFixture, Decider]:
        srv = HttpFixture(typesafe(answers, key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, decide_url=srv.url, secret=SECRET)
        return srv, from_config(self.repo)

    def test_a_closed_question_is_a_choice_with_the_labels_as_criteria(self):
        srv, d = self.serve(lambda b: {"answer": {"type": "choice", "choice": "no", "confidence": 0.93,
                                                  "probabilities": {"yes": 0.07, "no": 0.93}}})
        self.assertEqual(d.decide("Is 5 even?", ["yes", "no"]), ("no", 0.93, True))
        method, path, headers, body = srv.requests[-1]
        self.assertEqual((method, path, headers["Authorization"]), ("POST", "/v1/systemone", f"Bearer {SECRET}"))
        self.assertEqual(body, {"state": "Is 5 even?", "model": "jev-latest", "questions": {"answer": {
            "type": "choice", "instructions": "Is 5 even?", "criteria": {"yes": None, "no": None}}}}, "undescribed labels are null")
        d.decide("Is it closed?", ["yes", "no"], {"evidence": "the door is shut"})
        self.assertEqual(srv.requests[-1][3]["state"], {"evidence": "the door is shut"}, "state may be an object")
        self.assertEqual(d.usage, {"calls": 2, "input_tokens": 200, "output_tokens": 14})

    def test_every_question_type_needs_a_usable_answer(self):
        questions = {"team": {"type": "choice", "criteria": {"billing": "Payments", "technical": None}},
                     "urgent": {"type": "noul", "instructions": "The message is urgent."},
                     "anger": {"type": "score", "criteria": ["calm", "civil", "angry"]}}
        override: dict = {}
        _, d = self.serve(lambda b: {**{n: answer_of(x) for n, x in b["questions"].items()}, **override})
        reply = d.system_one("I was charged twice.", questions)
        self.assertEqual((reply["model"], reply["answers"]["team"]["choice"], reply["answers"]["urgent"]["noul"],
                          reply["answers"]["anger"]["score"]), ("jev-1.0.0", "billing", 0.9, 2.0))
        bad = {"choice outside the criteria": ("team", {"type": "choice", "choice": "sales", "confidence": 0.9}),
               "confidence above 1": ("team", {**answer_of(questions["team"]), "confidence": 1.5}),
               "wrong type": ("team", {"type": "noul", "noul": 0.5}),
               "missing": ("team", None),
               "noul as a bool": ("urgent", {"type": "noul", "noul": True}),
               "score above the top level": ("anger", {**answer_of(questions["anger"]), "score": 3})}
        for why, (q, answer) in bad.items():
            override.clear()
            override[q] = answer
            with self.assertRaisesRegex(DecideError, f"question '{q}'.*unusable answer", msg=why):
                d.system_one("s", questions)
        with self.assertRaisesRegex(DecideError, "at least one question"):
            d.system_one("s", {})

    def test_threshold_and_labels(self):
        _, d = self.serve(lambda b: {"answer": {"type": "choice", "choice": "maybe", "confidence": 0.5, "probabilities": {}}})
        self.assertEqual(d.decide("q", ["yes", "maybe", "no"]), ("maybe", 0.5, False), "below the 0.8 threshold")
        with self.assertRaisesRegex(DecideError, "at least two labels"):
            d.decide("x", ["only"])

    def test_models_errors_and_retries(self):
        srv, d = self.serve()
        self.assertEqual(d.models(), ["jev-latest", "jev-preview"])
        self.assertEqual(srv.requests[-1][:2], ("GET", "/v1/models"))
        d.headers = {"Authorization": "Bearer wrong-key"}
        with self.assertRaisesRegex(DecideError, "HTTP 401.*Invalid API key"):
            d.models()
        echo = HttpFixture(lambda m, p, h, b: as_json({"detail": [{"loc": ["body"], "msg": f"key {SECRET} rejected"}]}, 422))
        self.addCleanup(echo.close)
        with self.assertRaises(DecideError) as ctx:
            Decider({"base_url": echo.url, "auth_env": "K"}, {"K": SECRET}).decide("q", ["yes", "no"])
        self.assertIn("HTTP 422", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))
        echo200 = HttpFixture(lambda m, p, h, b: as_json({"echo": h.get("Authorization")}))
        self.addCleanup(echo200.close)
        with self.assertRaisesRegex(DecideError, r"no `answers` object in \{'echo': 'Bearer \*\*\*'\}"):
            Decider({"base_url": echo200.url, "auth_env": "K"}, {"K": SECRET}).decide("q", ["yes", "no"])
        replies = iter([as_json({"error": "slow down"}, 429), as_json({"models": [{"name": "jev-latest"}]})])
        flaky = HttpFixture(lambda m, p, h, b: next(replies))
        self.addCleanup(flaky.close)
        self.assertEqual(Decider({"base_url": flaky.url}, KEY).models(), ["jev-latest"], "a 429 is retried")

    def test_defaults_and_validation(self):
        self.assertIsNone(from_config(self.repo))
        d = Decider({}, KEY)
        self.assertEqual((d.url, d.model, d.threshold, d.headers),
                         ("https://api.typesafe.ai/v1/systemone", "jev-latest", 0.8, {"Authorization": "Bearer k-123"}))
        self.assertEqual(Decider({"base_url": "http://h:1/", "path": "/api/one"}, KEY).url, "http://h:1/api/one")
        with self.assertRaisesRegex(ConfigError, "base_url"):
            Decider({"base_url": ""}, KEY)
        with self.assertRaisesRegex(ConfigError, "`TYPED_DECISIONS_API_KEY` is empty"):
            Decider({}, {"TYPED_DECISIONS_API_KEY": ""})
