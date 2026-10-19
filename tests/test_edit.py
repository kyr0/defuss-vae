"""vae_edit: pro-edit's review contract in Python. Oracles on pro-edit's recorded example run, ports of its core tests,
the engine against a real OpenAI-compatible server on 127.0.0.1, and the agent's loop through the CLI (no mocks)."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
import urllib.request
from typing import Any

from research_kit import HttpFixture, llm, typesafe, write_pairs
from vae_testkit import PY, REPO, ROOT, RepoCase

# isort: split
import vae_edit as E
from vae_annotate import REVIEWS_DIR, page_data, stop_server

FIX = REPO / "tests" / "fixtures" / "pro-edit-example"
SECRET = "fixture-secret-123456"
THINKING = {"editor": {"name": "m-editor"}, "editor-thinking": {"name": "m-think", "thinking": {"type": "enabled"}}}
# pro-edit's schemas, byte for byte (feat-pro-edit/schemas, 2026-09-16): compatibility is the point of the copy.
SCHEMA_SHA = {"review": "8fa1127e226f3f3c8eb6983b70945aeb9c0f3574882e85e0ab31b0fabd8a84b6",
              "summary": "e5e893302112fe4c18d24ebc92aa5db1360d659a7deb6096326ae21124d28348",
              "audit": "dccd73bff45032fedb0319495e28e929752cd9660a050e1d295d7292475ebe00"}


def fixture_step(n: int) -> dict[str, Any]:
    return json.loads((FIX / "steps" / f"{n:04d}.json").read_text())


def part(i: int, text: str) -> dict[str, Any]:
    return {"id": i, "start": 0, "end": len(text), "line": i, "headings": [], "text": text}


def delta(inp: dict[str, Any]) -> dict[str, Any]:
    """pro-edit's test delta: one new entry holding the newest page, anchored on all of it."""
    page = inp["seiten"][-1]
    nid = f"M{inp['vorherige_zusammenfassung']['naechste_id']:06d}"
    return {"basis_revision": inp["vorherige_zusammenfassung"]["revision"],
            "neu": [{"id": nid, "typ": "aussage", "inhalt": page["text"], "woertlich": [],
                     "belege": [{"seite": page["seite"], "zitat": page["text"], "vorkommen": 1}], "bezug_ids": []}],
            "ergaenze_belege": [], "berichtige": [], "blocker": [],
            "seitenstatus": [{"seite": p["seite"], "status": "bearbeitet" if p["text"].strip() else "leer"} for p in inp["seiten"]]}


def setup() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    original = copy.deepcopy(E.EMPTY_MEMORY)
    inp = E.summary_input("test", original, [part(1, "Nur unter Aufsicht könnte A helfen.")], False)
    return original, inp, delta(inp)


def review_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    overlap, current = "😀 Vorher.\n\n", "Die Prüfung erfolgen täglich."
    inp = {"text": overlap + current, "zusammenfassung": "", "abschnitt_id": "2",
           "pruefbereich": {"von": len(overlap), "bis": len(overlap + current)}}
    finding = {"zitat": current, "vorkommen": 1, "typ": "satz", "umformulierung": "Die Prüfung erfolgt täglich.",
               "befunde": [{"regel": "T03", "schwere": "gering", "sicherheit": "hoch", "aktion": "umformulieren",
                            "kommentar": "Subjekt und Verb müssen im Numerus übereinstimmen.", "belege": []}]}
    return inp, finding


class OracleTests(unittest.TestCase):
    """The port against pro-edit's own recorded run of its constructed example article (tests/fixtures)."""

    def test_the_port_reproduces_the_recorded_example_run(self):
        raw = (FIX / "source.md").read_bytes()
        parts = E.split(raw.decode(), 8000, 2)
        self.assertEqual(parts, json.loads((FIX / "parts.json").read_text()), "UTF-16 offsets, lines, headings, texts")
        final = json.loads((FIX / "final.json").read_text())
        self.assertEqual(E.sha(raw), final["documentId"])
        schema = E.review_schema(E.load_schema("review"), E.catalog_ids())
        memory = E.EMPTY_MEMORY
        for n in range(1, 5):
            step = fixture_step(n)
            self.assertEqual(E.review_input(parts, n - 1, memory, {}), step["customInput"], f"review input {n}")
            read = {t["args"]["teil"] for c in step["calls"] if c["role"] == "custom" for t in c["tools"] if t["name"] == "document_read"}
            E.validate_schema(schema, step["custom"])
            E.validate_review(step["custom"], step["customInput"], parts, read)
            inp = E.summary_input(final["documentId"], memory, E.window_for(parts, n - 1), n == 4)
            E.validate_schema(E.load_schema("summary"), step["delta"])
            self.assertEqual(E.js_json(E.apply_delta(inp, step["delta"])), E.js_json(step["summary"]), f"memory {n}, byte for byte")
            if step["audit"] is not None:
                E.validate_schema(E.load_schema("audit"), step["audit"])
                E.validate_audit(inp, step["delta"], step["audit"])
            memory = step["summary"]
        self.assertEqual(E.render_memory(final["summary"]) + "\n", (FIX / "summary.md").read_text())

    def test_shipped_schemas_are_pro_edits_byte_for_byte(self):
        for name, digest in SCHEMA_SHA.items():
            self.assertEqual(hashlib.sha256((ROOT / "references" / "edit" / f"{name}.json").read_bytes()).hexdigest(), digest, name)

    def test_prompts_answer_in_the_documents_language_and_know_the_whole_catalog(self):
        for name in ("review", "summary", "audit"):
            text = (ROOT / "references" / "edit" / f"{name}.md").read_text()
            self.assertIn("in der Sprache des Dokumenttexts schreiben, unabhängig von der Sprache dieser Anweisung", text, name)
            self.assertNotIn("deutsch", text.lower(), f"{name}: no instruction may demand German output")
        review = (ROOT / "references" / "edit" / "review.md").read_text()
        self.assertEqual(len(E.catalog_ids()), 61)
        for rule in E.catalog_ids():
            self.assertIn(f"**{rule} — ", review, "every catalog rule has its heading in the review prompt")
        self.assertIn("Führe keine externe Recherche aus.", review)

    def test_the_review_schema_extension_leaves_the_shipped_schema_alone(self):
        base = E.load_schema("review")
        before = json.dumps(base)
        schema = E.review_schema(base, E.catalog_ids())
        self.assertEqual(json.dumps(base), before)
        self.assertEqual(len(schema["$defs"]["befund"]["properties"]["regel"]["enum"]), 61)
        self.assertTrue(schema["$id"].endswith(":document-evidence:vae"))
        _, finding = review_fixture()
        for evidence in ({"quelle": "dokument", "teil": 1, "zitat": "x", "vorkommen": 1},
                         {"quelle": "datei", "pfad": "a.py", "zitat": "x", "vorkommen": 1}):
            finding["befunde"][0]["belege"] = [evidence]
            E.validate_schema(schema, [finding])
        finding["befunde"][0]["belege"] = [{"quelle": "web", "zitat": "x", "vorkommen": 1}]
        with self.assertRaisesRegex(E.EditError, "JSON Schema"):
            E.validate_schema(schema, [finding])
        finding["befunde"][0]["regel"] = "Z99"
        with self.assertRaisesRegex(E.EditError, "regel"):
            E.validate_schema(schema, [finding])

    def test_the_validator_fails_closed_on_a_keyword_it_does_not_implement(self):
        with self.assertRaisesRegex(E.EditError, "unsupported keyword"):
            E.check_keywords({"type": "object", "properties": {"x": {"type": "string", "format": "email"}}})


class CoreTests(unittest.TestCase):
    """pro-edit's test/core.test.ts, ported case by case."""

    def test_strict_json(self):
        for source in ('{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"x":[{"z":1,"z":2}]}'):
            with self.assertRaisesRegex(E.EditError, "Duplicate", msg=source):
                E.parse_json(source)
        for source in ("```json\n[]\n```", "prefix []", "[] trailing", "[NaN]", "[1e999]", '{"x":-1e999}'):
            with self.assertRaises(E.EditError, msg=source):
                E.parse_json(source)
        self.assertEqual(E.parse_json('{"x":"{\\"x\\":2}","a":[{"x":1},{"x":2}]}'), {"x": '{"x":2}', "a": [{"x": 1}, {"x": 2}]})

    def test_overlapping_occurrences_in_codepoints(self):
        self.assertEqual(E.occurrence("aaaa", "aa", 3), 2)
        self.assertEqual(E.occurrence("😀 ä\r\n😀 ä", "😀 ä", 2), 5, "codepoints here; pro-edit's 6 counts UTF-16 units")
        self.assertEqual((len("😀x"), E.u16("😀x")), (2, 3))
        for quote, nth in (("\ud83d", 1), ("x", 0), ("", 1), ("x", True)):
            with self.assertRaises(E.EditError):
                E.occurrence("😀x", quote, nth)

    def test_two_part_windows_cover_each_new_part_once(self):
        for count in range(1, 41):
            parts = [part(i + 1, str(i)) for i in range(count)]
            for i in range(count):
                self.assertEqual([p["id"] for p in E.window_for(parts, i)], [i, i + 1] if i else [1])

    def test_grep_is_literal(self):
        parts = [part(1, "A.*B"), part(2, "AXB")]
        self.assertEqual([m["teil"] for m in E.grep_parts(parts, ".*")["matches"]], [1])
        with self.assertRaises(E.EditError):
            E.grep_parts(parts, "")

    def test_delta_application_never_mutates_old_memory_and_keeps_qualifiers(self):
        original, inp, candidate = setup()
        candidate["neu"][0]["woertlich"] = ["Nur", "unter Aufsicht", "könnte"]
        before = json.dumps(inp)
        result = E.apply_delta(inp, candidate)
        self.assertEqual(json.dumps(inp), before)
        self.assertEqual((original["revision"], result["revision"], result["naechste_id"]), (0, 1, 2))
        self.assertRegex(E.render_memory(result), "unter Aufsicht könnte")

    def test_the_reducer_rejects_every_broken_delta(self):
        mutations = {
            "stale revision": lambda d: d.update(basis_revision=2),
            "wrong ID": lambda d: d["neu"][0].update(id="M000004"),
            "invented quote": lambda d: d["neu"][0]["belege"][0].update(zitat="Not in the source"),
            "wrong occurrence": lambda d: d["neu"][0]["belege"][0].update(vorkommen=2),
            "unknown source": lambda d: d["neu"][0]["belege"][0].update(seite=30),
            "missing protected term": lambda d: d["neu"][0].update(woertlich=["unbedingt"]),
            "duplicate anchor": lambda d: d["neu"][0]["belege"].append(dict(d["neu"][0]["belege"][0])),
            "self relation": lambda d: d["neu"][0].update(bezug_ids=["M000001"]),
            "unknown relation": lambda d: d["neu"][0].update(bezug_ids=["M000099"]),
            "blocker": lambda d: d.update(blocker=["Quelle unlesbar"]),
            "unresolved page": lambda d: d["seitenstatus"][0].update(status="klaerung_noetig"),
            "false empty page": lambda d: d["seitenstatus"][0].update(status="leer"),
        }
        for name, mutate in mutations.items():
            _, inp, candidate = setup()
            mutate(candidate)
            with self.assertRaises(E.EditError, msg=name):
                E.apply_delta(inp, candidate)

    def test_a_memory_over_budget_fails_without_truncating(self):
        _, inp, candidate = setup()
        with self.assertRaisesRegex(E.EditError, "budget"):
            E.apply_delta(inp, candidate, 10)
        self.assertEqual(inp["vorherige_zusammenfassung"]["eintraege"], [])

    def test_unchanged_entries_survive_byte_for_byte_and_evidence_is_never_repeated(self):
        _, inp, candidate = setup()
        first = E.apply_delta(inp, candidate)
        nxt = E.summary_input("test", first, [part(1, inp["seiten"][0]["text"]), part(2, "Keine Wirkung nachgewiesen.")], True)
        self.assertEqual(E.js_json(E.apply_delta(nxt, delta(nxt))["eintraege"][0]), E.js_json(first["eintraege"][0]))
        nxt = E.summary_input("test", first, [part(1, inp["seiten"][0]["text"]), part(2, inp["seiten"][0]["text"])], True)
        more = delta(nxt)
        more["neu"] = []
        more["ergaenze_belege"] = [{"id": "M000001", "belege": [{"seite": 2, "zitat": inp["seiten"][0]["text"], "vorkommen": 1}]}]
        self.assertEqual(len(E.apply_delta(nxt, more)["eintraege"][0]["belege"]), 2)
        more["ergaenze_belege"][0]["belege"][0]["seite"] = 1
        with self.assertRaisesRegex(E.EditError, "Repeated"):
            E.apply_delta(nxt, more)

    def test_the_audit_must_cite_visible_originals(self):
        _, inp, candidate = setup()
        with self.assertRaisesRegex(E.EditError, "visible"):
            E.validate_audit(inp, candidate, [{"regel": "geltungsbereich", "eintrag_ids": ["M000001"], "seite": 1,
                                               "zitat": "A hilft.", "kommentar": "Nicht gedeckt."}])

    def test_review_quotes_belong_to_the_current_part_in_order_without_open_replacements(self):
        inp, finding = review_fixture()
        E.validate_review([finding], inp, [], [])
        with self.assertRaisesRegex(E.EditError, "overlap"):
            E.validate_review([finding, finding], inp, [], [])
        for mutate, why in ((lambda f: f.update(zitat="😀 Vorher."), "current"),
                            (lambda f: f["befunde"][0].update(aktion="pruefen"), "Unresolved"),
                            (lambda f: f["befunde"].insert(0, dict(f["befunde"][0], schwere="gering")) or f["befunde"][1].update(schwere="kritisch"), "severity"),
                            (lambda f: f["befunde"][0].update(belege=[{"quelle": "zusammenfassung", "zitat": "x", "vorkommen": 1}]), "Summary-only")):
            inp, finding = review_fixture()
            mutate(finding)
            with self.assertRaisesRegex(E.EditError, why):
                E.validate_review([finding], inp, [], [])
        inp, finding = review_fixture()
        inp["vorgaben"] = {"geschuetzte_begriffe": ["erfolgen"]}
        with self.assertRaisesRegex(E.EditError, "protected wording: erfolgen"):
            E.validate_review([finding], inp, [], [])

    def test_evidence_an_earlier_answer_cited_cannot_vanish_while_confidence_stays_high(self):
        inp, plain = review_fixture()
        cite = [{"quelle": "zusammenfassung", "zitat": "x", "vorkommen": 1}]

        def answer(*issues: dict[str, Any]) -> list[dict[str, Any]]:
            f = copy.deepcopy(plain)
            f["befunde"] = [plain["befunde"][0] | issue for issue in issues]
            return [f]
        earlier = [E.issue_spans(answer({"belege": cite}), inp)]
        relabeled = answer({"regel": "S03"})
        rejected = [("T03 cited evidence in an earlier answer", answer({}), earlier),
                    ("S03 takes the place of T03", relabeled, earlier),
                    ("S03 takes the place of T03", answer({"sicherheit": "mittel"}, {"regel": "S03"}), earlier),
                    ("S03 takes the place of T03", relabeled, [*earlier, E.issue_spans(relabeled, inp)])]
        for why, value, before in rejected:
            with self.assertRaisesRegex(E.EditError, why):
                E.validate_review(value, inp, [], [], None, before)
        local = [{"quelle": "text", "zitat": "Vorher.", "vorkommen": 1}]
        allowed = [answer({"sicherheit": "mittel"}), answer({"belege": local}), answer({"belege": local}, {"regel": "S03"})]
        for value in allowed:
            E.validate_review(value, inp, [], [], None, earlier)
        E.validate_review(answer({"regel": "S03"}), inp, [], [], None, [E.issue_spans(answer({"belege": cite}, {"regel": "S03"}), inp)])
        start = len("😀 Vorher.\n\n")
        self.assertEqual(E.issue_spans(answer({}), inp), {("T03", start, start + len(plain["zitat"]), False)})
        self.assertEqual((E.issue_spans("not a list", inp), E.issue_spans([{"zitat": "nowhere", "vorkommen": 1}], inp)),
                         (set(), set()), "unparsable answers and unlocatable quotes add nothing")

    def test_document_and_file_evidence_must_have_been_read(self):
        inp, finding = review_fixture()
        original = part(1, "Definition A.")
        finding["befunde"][0]["belege"] = [{"quelle": "dokument", "teil": 1, "zitat": original["text"], "vorkommen": 1}]
        with self.assertRaisesRegex(E.EditError, "not read"):
            E.validate_review([finding], inp, [original], [])
        E.validate_review([finding], inp, [original], [1])
        finding["befunde"][0]["belege"] = [{"quelle": "datei", "pfad": "src/a.py", "zitat": "TIMEOUT = 30", "vorkommen": 1}]
        with self.assertRaisesRegex(E.EditError, "File evidence was not read"):
            E.validate_review([finding], inp, [], [], {})
        with self.assertRaisesRegex(E.EditError, "not present"):
            E.validate_review([finding], inp, [], [], {"src/a.py": "TIMEOUT = 60\n"})
        E.validate_review([finding], inp, [], [], {"src/a.py": "TIMEOUT = 30\n"})

    def test_documented_limit_an_exact_anchor_does_not_prove_a_paraphrase(self):
        _, inp, candidate = setup()
        candidate["neu"][0]["inhalt"] = "A wirkt immer."  # unsupported on purpose: only a semantic audit can see it
        self.assertEqual(E.apply_delta(inp, candidate)["eintraege"][0]["inhalt"], "A wirkt immer.")


def kind(body: dict[str, Any]) -> str:
    system = body["messages"][0]["content"]
    return "custom" if system.startswith("# Redaktionelle") else "summary" if system.startswith("# Quellengebundene") else "audit"


def envelope(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(body["messages"][1]["content"])


def call(name: str, **args: Any) -> dict[str, Any]:
    return {"tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def decisions(case: RepoCase, score, language: tuple[str, float] | None = ("English", 0.95), support=None) -> HttpFixture:
    """A System One server scoring each text's complexity with `score(text)`: a number, or None for a 500; the
    language question gets `language` (label, confidence), the evidence question `support(state)`, or a 500 for None."""
    def answers(body):
        question = body["questions"].get("answer", {}).get("instructions")
        value = (score(body["state"]) if "complexity" in body["questions"]
                 else support(body["state"]) if question == E.SUPPORT_QUESTION else language)
        if value is None:
            raise RuntimeError("down")
        if "complexity" in body["questions"]:
            return {"complexity": {"type": "score", "score": value, "confidence": 0.9, "legend": {}, "probabilities": {}}}
        return {"answer": {"type": "choice", "choice": value[0], "confidence": value[1], "probabilities": {value[0]: value[1]}}}

    def handler(method, path, headers, body):
        try:
            return typesafe(answers, key=SECRET)(method, path, headers, body)
        except RuntimeError:
            return 500, {"Content-Type": "application/json"}, b'{"detail": "down"}'
    srv = HttpFixture(handler)
    case.addCleanup(srv.close)
    write_pairs(case.write, decide_url=srv.url, secret=SECRET)
    return srv


class EngineCase(RepoCase):
    def tearDown(self):
        stop_server(self.repo)  # a finished review starts the review server; stop it before the repo goes
        super().tearDown()

    def engine(self, script, models: dict[str, Any] | None = None, **cfg: Any) -> tuple[HttpFixture, E.Engine]:
        srv = HttpFixture(llm(script, key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, llm_url=srv.url, secret=SECRET)
        if models:
            pair = json.loads((self.repo / ".vae/.env.openai.json").read_text())
            self.write(".vae/.env.openai.json", json.dumps(pair | {"models": models}))
        engine = E.engine_for(self.repo, {**E.settings({})[0], **cfg})
        self.addCleanup(engine.close)
        return srv, engine

    def roles(self, srv: HttpFixture) -> list[str]:
        return [kind(b) for m, p, h, b in srv.requests if p.endswith("/chat/completions")]


def standard(body: dict[str, Any]) -> Any:
    """Review: no finding; summary: the test delta; audit: no divergence."""
    k = kind(body)
    return "[]" if k != "summary" else E.js_json(delta(envelope(body)["eingabe"]))


class EngineTests(EngineCase):
    """pro-edit's pipeline tests, against a real OpenAI-compatible server instead of an injected function."""

    def test_review_then_summary_then_audit_on_the_pre_step_memory(self):
        srv, engine = self.engine(standard)
        parts = [part(1, "Erste Aussage."), part(2, "Zweite Aussage."), part(3, "Dritte Aussage.")]
        steps: list[dict[str, Any]] = []
        final = E.run_steps(engine, "test", parts, {}, steps.append)
        self.assertEqual(self.roles(srv), ["custom", "summary", "audit"] * 3)
        bodies = [b for m, p, h, b in srv.requests if p.endswith("/chat/completions")]
        self.assertEqual(envelope(bodies[0])["eingabe"]["zusammenfassung"], "")
        second = envelope(bodies[3])["eingabe"]["zusammenfassung"]
        self.assertIn("Erste Aussage", second)
        self.assertNotIn("Zweite Aussage", second)
        self.assertEqual((final["summary"]["bis_seite"], len(steps)), (3, 3))
        self.assertIn("Allowed source part IDs: 1, 2, 3.", bodies[6]["messages"][0]["content"], "review: the prefix")
        self.assertIn("Allowed source part IDs: 2, 3.", bodies[7]["messages"][0]["content"], "summary: the window only")
        self.assertEqual(engine.role, "reasoner")
        self.assertEqual({t["function"]["name"] for t in bodies[0]["tools"]}, {"document_read", "document_grep", "file_grep", "file_read"})
        self.assertEqual({t["function"]["name"] for t in bodies[1]["tools"]}, {"document_read", "document_grep"}, "summary: no file tools")

    def test_a_failed_audit_retries_from_the_same_base_before_committing(self):
        audits = []

        def script(body):
            if kind(body) == "audit":
                audits.append(1)
                if len(audits) == 1:
                    return E.js_json([{"regel": "geltungsbereich", "eintrag_ids": ["M000001"], "seite": 1,
                                       "zitat": "A könnte helfen.", "kommentar": "Modalität prüfen."}])
            return standard(body)
        srv, engine = self.engine(script)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "A könnte helfen.")], {}, steps.append)
        self.assertEqual((self.roles(srv).count("summary"), len(steps)), (2, 1))
        bases = [envelope(b)["eingabe"]["vorherige_zusammenfassung"]["revision"] for m, p, h, b in srv.requests
                 if p.endswith("/chat/completions") and kind(b) == "summary"]
        self.assertEqual(bases, [0, 0])

    def test_a_persistent_audit_divergence_is_committed_under_protest(self):
        def script(body):
            if kind(body) == "audit" and envelope(body)["eingabe"]["eingabe"]["fenster"]["bis"] == 1:
                return E.js_json([{"regel": "geltungsbereich", "eintrag_ids": ["M000001"], "seite": 1,
                                   "zitat": "A könnte helfen.", "kommentar": "Dauerdivergenz."}])
            return standard(body)
        _, engine = self.engine(script, attempts=2)
        steps: list[dict[str, Any]] = []
        result = E.run_steps(engine, "test", [part(1, "A könnte helfen."), part(2, "B.")], {}, steps.append)
        self.assertEqual((steps[0].get("auditOverride"), len(steps[0]["audit"]), steps[1].get("auditOverride")), (True, 1, None))
        self.assertEqual((steps[1]["previousRevision"], result["auditOverrides"]), (1, [1]))

    def decisions(self, score, language: tuple[str, float] | None = ("English", 0.95), support=None) -> HttpFixture:
        return decisions(self, score, language, support)

    def test_a_part_the_decision_model_scores_complex_is_reviewed_with_thinking(self):
        dec = self.decisions({"Einfach.": 0.2, "Verschachtelt.": 1.4}.get)
        srv, engine = self.engine(standard, THINKING)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "Einfach."), part(2, "Verschachtelt.")], {}, steps.append)
        models = [(kind(b), b["model"]) for m, p, h, b in srv.requests if p.endswith("/chat/completions")]
        self.assertEqual(models, [("custom", "m-editor"), ("summary", "m-editor"), ("audit", "m-editor"),
                                  ("custom", "m-think"), ("summary", "m-editor"), ("audit", "m-editor")])
        self.assertEqual([s["route"] for s in steps], [{"role": "editor", "score": 0.2}, {"role": "editor-thinking", "score": 1.4}])
        scored = [b for m, p, h, b in dec.requests if "complexity" in b["questions"]]
        self.assertEqual([b["state"] for b in scored], ["Einfach.", "Verschachtelt."], "the part, not the window")
        self.assertEqual(scored[0]["questions"]["complexity"]["type"], "score")

    def test_a_thinking_review_that_fails_falls_back_to_the_editor(self):
        self.decisions(lambda text: 1.8)
        srv, engine = self.engine(lambda b: "not json" if b["model"] == "m-think" else standard(b), THINKING, attempts=2)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "Verschachtelt.")], {}, steps.append)
        models = [(kind(b), b["model"]) for m, p, h, b in srv.requests if p.endswith("/chat/completions")]
        self.assertEqual(models[:3], [("custom", "m-think"), ("custom", "m-think"), ("custom", "m-editor")])
        self.assertEqual((steps[0]["route"]["role"], steps[0]["route"]["score"]), ("editor", 1.8))
        self.assertIn("custom task rejected after 2 attempts", steps[0]["route"]["fallback"])

    def test_without_a_complexity_score_the_review_stays_on_the_editor(self):
        self.decisions(lambda text: None)
        srv, engine = self.engine(standard, THINKING)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "Verschachtelt.")], {}, steps.append)
        self.assertEqual({b["model"] for m, p, h, b in srv.requests if p.endswith("/chat/completions")}, {"m-editor"})
        self.assertEqual(steps[0]["route"]["role"], "editor")
        self.assertIn("500", steps[0]["route"]["error"])
        dec = self.decisions(lambda text: 2.0)
        srv, engine = self.engine(standard, {"editor": {"name": "m-editor"}})
        E.run_steps(engine, "test", [part(1, "Verschachtelt.")], {}, [].append)
        scored = [b for m, p, h, b in dec.requests if "complexity" in b["questions"]]
        self.assertEqual((scored, engine.thinking), ([], None), "no thinking role: no complexity to score")

    def test_the_documents_language_ends_every_prompt_when_the_decision_model_is_sure(self):
        dec = self.decisions(lambda text: 0.2, ("English", 0.95))
        srv, engine = self.engine(standard)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "Plain words."), part(2, "More words.")], {}, steps.append)
        asked = [b for m, p, h, b in dec.requests if "answer" in b["questions"]]
        self.assertEqual([b["state"] for b in asked], ["Plain words.\n\nMore words."], "once per document")
        self.assertEqual(list(asked[0]["questions"]["answer"]["criteria"]), E.LANGUAGES)
        systems = [b["messages"][0]["content"] for m, p, h, b in srv.requests if p.endswith("/chat/completions")]
        self.assertEqual(len(systems), 6)
        self.assertTrue(all(s.endswith(E.OUTPUT_LANGUAGE.format(language="English")) for s in systems), "review, summary, audit")
        self.assertEqual([s["language"] for s in steps], [{"language": "English", "confidence": 0.95}] * 2)

    def test_an_unsure_or_failed_language_answer_leaves_the_prompts_alone(self):
        for language in (("German", 0.5), None):
            self.decisions(lambda text: 0.2, language)
            srv, engine = self.engine(standard)
            steps: list[dict[str, Any]] = []
            E.run_steps(engine, "test", [part(1, "Ein Satz.")], {}, steps.append)
            systems = [b["messages"][0]["content"] for m, p, h, b in srv.requests if p.endswith("/chat/completions")]
            self.assertFalse(any("Output language:" in s for s in systems))
            if language:
                self.assertEqual(steps[0]["language"], {"language": "German", "confidence": 0.5})
            else:
                self.assertIn("500", steps[0]["language"]["error"])

    def test_unparseable_audit_output_is_fatal_and_commits_nothing(self):
        _, engine = self.engine(lambda b: "not json" if kind(b) == "audit" else standard(b), attempts=2)
        steps: list[dict[str, Any]] = []
        with self.assertRaisesRegex(E.EditError, "audit output rejected"):
            E.run_steps(engine, "test", [part(1, "A.")], {}, steps.append)
        self.assertEqual(steps, [])

    def test_corrective_retries_are_bounded_and_an_unaccepted_review_never_advances_memory(self):
        reviews = []

        def fenced(body):
            if kind(body) == "custom":
                reviews.append(envelope(body)["rueckmeldung"])
                if len(reviews) == 1:
                    return "```[]```"
            return standard(body)
        _, engine = self.engine(fenced)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "A.")], {}, steps.append)
        self.assertEqual((len(reviews), len(steps)), (2, 1))
        self.assertEqual((reviews[0], reviews[1]["previous_output"]), (None, "```[]```"), "the retry carries the error and the answer")
        srv, engine = self.engine(lambda b: "42", attempts=2)
        with self.assertRaisesRegex(E.EditError, "custom task rejected after 2 attempts"):
            E.run_steps(engine, "test", [part(1, "A.")], {}, steps.append)
        self.assertEqual(self.roles(srv), ["custom", "custom"])

    def test_a_retry_cannot_drop_evidence_to_keep_high_confidence(self):
        quote = "Die tägliche Prüfung beweist die Zuverlässigkeit."
        answers = [("hoch", [{"quelle": "zusammenfassung", "zitat": "x", "vorkommen": 1}]), ("hoch", []), ("mittel", [])]
        feedback = []

        def script(body):
            if kind(body) != "custom":
                return standard(body)
            feedback.append((envelope(body)["rueckmeldung"] or {}).get("error"))
            sure, evidence = answers[len(feedback) - 1]
            return json.dumps([{"zitat": quote, "vorkommen": 1, "typ": "satz", "umformulierung": None, "befunde": [
                {"regel": "L01", "schwere": "wesentlich", "sicherheit": sure, "aktion": "pruefen", "kommentar": "Nicht gedeckt.",
                 "belege": evidence}]}])
        _, engine = self.engine(script)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, quote)], {}, steps.append)
        self.assertEqual(len(feedback), 3)
        self.assertIn("Summary-only", feedback[1])
        self.assertIn("L01 cited evidence in an earlier answer", feedback[2], "dropping the evidence does not pass")
        self.assertEqual(steps[0]["custom"][0]["befunde"][0]["sicherheit"], "mittel")

    def test_evidence_the_decision_model_is_sure_does_not_support_the_comment_goes_back(self):
        quote, aside = "Die tägliche Prüfung beweist die Zuverlässigkeit.", "Der Bericht erscheint montags."
        answers = [aside, None, quote]
        feedback = []

        def script(body):
            if kind(body) != "custom":
                return standard(body)
            feedback.append((envelope(body)["rueckmeldung"] or {}).get("error"))
            cite = answers[len(feedback) - 1]
            return json.dumps([{"zitat": quote, "vorkommen": 1, "typ": "satz", "umformulierung": None, "befunde": [
                {"regel": "L01", "schwere": "wesentlich", "sicherheit": "hoch", "aktion": "pruefen", "kommentar": "Nicht gedeckt.",
                 "belege": [{"quelle": "text", "zitat": cite, "vorkommen": 1}] if cite else []}]}])
        dec = self.decisions(lambda text: 0.2, support=lambda state: ("no", 0.95) if "montags" in state["evidence"][0]["quote"] else ("yes", 0.9))
        _, engine = self.engine(script)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, f"{quote} {aside}")], {}, steps.append)
        self.assertIn("does not support the comment: L01", feedback[1])
        self.assertIn("L01 cited evidence in an earlier answer", feedback[2], "a refuted citation cannot be deleted to keep `hoch`")
        self.assertEqual(steps[0]["support"], [{"finding": 0, "issue": 0, "rule": "L01", "decision": "yes (0.90)"}])
        asked = [b["state"] for m, p, h, b in dec.requests if b["questions"]["answer"]["instructions"] == E.SUPPORT_QUESTION]
        self.assertEqual(asked[0], {"quoted_text": quote, "comment": "Nicht gedeckt.", "evidence": [{"source": "text", "quote": aside}]})
        for verdict, record in ((("no", 0.6), "no (0.60)"), (None, "HTTP 500")):  # only a sure `no` sends the answer back
            self.decisions(lambda text: 0.2, support=lambda state, v=verdict: v)
            _, engine = self.engine(script)
            feedback.clear()
            steps.clear()
            E.run_steps(engine, "test", [part(1, f"{quote} {aside}")], {}, steps.append)
            self.assertEqual(len(feedback), 1)
            self.assertIn(record, str(steps[0]["support"][0]))

    def test_resume_continues_from_the_last_committed_step(self):
        down = {"on": True}

        def script(body):
            if kind(body) == "custom" and envelope(body)["eingabe"]["abschnitt_id"] == "2" and down["on"]:
                return {"status": 500, "body": {"error": "model down"}}
            return standard(body)
        _, engine = self.engine(script)
        parts = [part(1, "A."), part(2, "B."), part(3, "C.")]
        steps: list[dict[str, Any]] = []
        with self.assertRaisesRegex(Exception, "HTTP 500"):
            E.run_steps(engine, "test", parts, {}, steps.append)
        self.assertEqual(len(steps), 1)
        down["on"] = False
        result = E.run_steps(engine, "test", parts, {}, steps.append, resume={"memory": steps[0]["summary"], "start": 1})
        self.assertEqual([s["part"] for s in steps], [1, 2, 3])
        self.assertEqual((steps[1]["previousRevision"], result["summary"]["revision"]), (steps[0]["summary"]["revision"], 3))

    def test_document_evidence_needs_a_read_and_the_prefix_scope_holds(self):
        def script(body):
            if kind(body) != "custom" or envelope(body)["eingabe"]["abschnitt_id"] != "2":
                return standard(body)
            tool_results = [m["content"] for m in body["messages"] if m["role"] == "tool"]
            if not tool_results:
                return call("document_read", teil=3)
            if len(tool_results) == 1:
                return call("document_read", teil=1)
            quote = "Definition: A ist B."
            return E.js_json([{"zitat": "A ist C.", "vorkommen": 1, "typ": "satz", "umformulierung": None, "befunde": [
                {"regel": "L03", "schwere": "wesentlich", "sicherheit": "hoch", "aktion": "pruefen", "kommentar": "Widerspricht Teil 1.",
                 "belege": [{"quelle": "dokument", "teil": 1, "zitat": quote, "vorkommen": 1}]}]}])
        srv, engine = self.engine(script)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, "Definition: A ist B.\n\n"), part(2, "A ist C.\n\n"), part(3, "Ende.")], {}, steps.append)
        self.assertEqual(steps[1]["custom"][0]["befunde"][0]["belege"][0]["teil"], 1)
        bodies = [b for m, p, h, b in srv.requests if p.endswith("/chat/completions") and kind(b) == "custom"]
        results = [m["content"] for b in bodies for m in b["messages"] if m["role"] == "tool"]
        self.assertTrue(any("outside this task's allowed source scope" in r for r in results), results)
        self.assertEqual([t["name"] for t in steps[1]["calls"][0]["tools"]], ["document_read", "document_read"])

    def test_internal_evidence_reads_repository_files_but_never_secrets(self):
        self.write("src/app.py", "TIMEOUT = 30\n")
        self.write(".github/workflows/ci.yml", "on: push\n")
        page = "The timeout is 60 seconds.\n"

        def script(body):
            if kind(body) != "custom":
                return standard(body)
            results = [m["content"] for m in body["messages"] if m["role"] == "tool"]
            if len(results) == 0:
                return call("file_read", pfad=".vae/.env.openai")
            if len(results) == 1:
                return call("file_grep", query="TIMEOUT")
            if len(results) == 2:
                return call("file_read", pfad="./.github/workflows/ci.yml")
            if len(results) == 3:
                return call("file_read", pfad="src/app.py")
            return E.js_json([{"zitat": "The timeout is 60 seconds.", "vorkommen": 1, "typ": "satz",
                               "umformulierung": "The timeout is 30 seconds.", "befunde": [
                                   {"regel": "B01", "schwere": "kritisch", "sicherheit": "hoch", "aktion": "revidieren",
                                    "kommentar": "The code sets 30 s.",
                                    "belege": [{"quelle": "datei", "pfad": "src/app.py", "zitat": "TIMEOUT = 30", "vorkommen": 1}]}]}])
        srv, engine = self.engine(script)
        steps: list[dict[str, Any]] = []
        E.run_steps(engine, "test", [part(1, page)], {}, steps.append)
        self.assertEqual(steps[0]["custom"][0]["umformulierung"], "The timeout is 30 seconds.")
        last = [b for m, p, h, b in srv.requests if p.endswith("/chat/completions") and kind(b) == "custom"][-1]
        results = [m["content"] for m in last["messages"] if m["role"] == "tool"]
        self.assertIn("is not a readable repository file", results[0], "a secret file is never read")
        self.assertIn('"pfad":"src/app.py","zeile":1', results[1])
        self.assertIn('"pfad":".github/workflows/ci.yml","text":"on: push', results[2], "a dot folder is a repository folder")
        self.assertNotIn(SECRET, json.dumps([b for m, p, h, b in srv.requests]))

    def test_budgets_stop_the_run_with_failure_json_and_traces_never_hold_the_key(self):
        def script(body):
            if kind(body) == "custom" and not any(m["role"] == "tool" for m in body["messages"]):
                return {"tool_calls": [call("document_grep", query="A")["tool_calls"][0],
                                       {"id": "c-2", "type": "function", "function": {"name": "document_read", "arguments": '{"teil": 1}'}}]}
            return standard(body)
        self.write("page.md", "# A\n\nA.\n")
        srv = HttpFixture(llm(script, key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, llm_url=srv.url, secret=SECRET)
        cfg = {**E.settings({})[0], "max_tool_calls": 1}
        run = E.create_run(self.repo, "page.md", cfg, "engine")
        engine = E.engine_for(self.repo, cfg)
        with self.assertRaisesRegex(E.EditError, "Tool-call budget exceeded"):
            E.run_engine(self.repo, E.harness_state(self.repo, run), engine, {})
        self.assertIn("Tool-call budget exceeded", json.loads((run / "failure.json").read_text())["error"])
        engine = E.engine_for(self.repo, {**cfg, "max_tool_calls": 32})
        page = E.run_engine(self.repo, E.harness_state(self.repo, run), engine, {})
        final = json.loads((run / "final.json").read_text())
        self.assertEqual(list(final), ["source", "documentId", "model", "auditEnabled", "customToolScope", "traceEnabled",
                                       "promptHashes", "summary", "custom", "steps", "auditOverrides"])
        self.assertEqual((final["model"]["provider"], final["traceEnabled"], (run / "failure.json").exists()), ("openai-compatible", True, False))
        traces = list((run / "steps").glob("*.trace.jsonl"))
        self.assertTrue(traces)
        for t in traces:
            self.assertEqual(t.stat().st_mode & 0o777, 0o600)
            text = t.read_text()
            self.assertNotIn(SECRET, text)
            self.assertNotIn("Authorization", text)
            self.assertIn('"request"', text)
        self.assertEqual(page_data(page)["meta"]["kind"], "edit")

    def test_the_editor_role_wins_over_the_reasoner(self):
        self.engine(standard)
        config = json.loads((self.repo / ".vae/.env.openai.json").read_text())
        config["models"]["editor"] = {"name": "m-reasoner", "max_tokens": 50}
        self.write(".vae/.env.openai.json", json.dumps(config))
        self.assertEqual(E.engine_for(self.repo, E.settings({})[0]).role, "editor")


PAGE = "# Guide\n\nThe service is very fast. It answers quickly.\n\n## Use\n\nRun the check daily.\n"


def finding(quote: str, rule: str = "S03", action: str = "umformulieren", replacement: str | None = "The service is fast.",
            certainty: str = "hoch", evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"zitat": quote, "vorkommen": 1, "typ": "satz", "umformulierung": replacement, "befunde": [
        {"regel": rule, "schwere": "gering", "sicherheit": certainty, "aktion": action, "kommentar": "Drop the intensifier.",
         "belege": evidence or []}]}


class HarnessTests(RepoCase):
    """The agent's loop through the CLI: start, submit per part, the second opinion, apply, show."""

    def tearDown(self):
        stop_server(self.repo)  # a finished review starts the review server; stop it before the repo goes
        super().tearDown()

    def cli(self, *args: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
        p = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "edit", "--repo", str(self.repo), *args],
                           capture_output=True, text=True, check=False)
        self.assertIn(p.returncode, ok, p.stdout + p.stderr)
        return p

    def begin(self) -> str:
        self.write("guide.md", PAGE)
        out = self.cli("start", "guide.md", "--model", "claude-test").stdout
        return next(ln.split("run=")[1] for ln in out.splitlines() if ln.startswith("defuss-vae EDIT"))

    def draft(self, run: str, n: int, value: Any) -> str:
        path = f"{run}/draft/{n:04d}.json"
        self.write(path, value if isinstance(value, str) else json.dumps(value))
        return path

    def submit(self, run: str, n: int, value: Any, confidence: str = "high", ok: tuple[int, ...] = (0,), *extra: str) -> str:
        return self.cli("submit", "--run", run, "--part", str(n), "--findings", self.draft(run, n, value), "--confidence", confidence,
                        *extra, ok=ok).stdout

    def test_start_prints_the_contract_and_the_first_window(self):
        self.write("guide.md", PAGE)
        out = self.cli("start", "guide.md").stdout
        for token in ("# Edit contract: findings as anchored JSON", "EDIT guide.md part 1/2 (lines 1-4)", "REVIEW part 1",
                      "    3| The service is very fast. It answers quickly.", "CHECK EVERY catalog rule against part 1: B01",
                      "P09, R01", "T07", "--part 1 --findings tmp/vae/edit/guide-"):
            self.assertIn(token, out)
        self.assertNotIn("CONTEXT", out)
        self.assertIn("UNKNOWN[edit] BC missing.md is not a file", self.cli("start", "missing.md", ok=(2,)).stdout)

    def test_submissions_are_checked_and_a_rejection_prints_the_window_again(self):
        run = self.begin()
        bad = {"an invented quote": [finding("The service is blazing fast.")],
               "a replacement on an open action": [finding("The service is very fast.", action="pruefen")],
               "an unknown rule": [finding("The service is very fast.", rule="Z99")]}
        for why, value in bad.items():
            out = self.submit(run, 1, value, "high", (2,))
            self.assertIn("REJECTED part 1:", out, why)
            self.assertIn("REVIEW part 1", out, why)
        out = self.submit(run, 1, '[{"zitat": "x", "zitat": "y"}]', "high", (2,))
        self.assertIn("Duplicate JSON key: zitat", out)
        self.submit(run, 1, [finding("The service is very fast.")])
        out = self.submit(run, 2, [finding("The service is very fast.")], "high", (2,))
        self.assertIn("Finding is outside the current part", out, "the context part supplies no quote")
        self.assertIn("CONTEXT part 1", out)
        self.assertIn("part 3 is not next", self.submit(run, 3, [], "high", (2,)))

    def test_the_loop_finishes_with_pro_edits_files_and_apply_writes_only_resolved_replacements(self):
        run = self.begin()
        out = self.submit(run, 1, [finding("The service is very fast."), finding("It answers quickly.", rule="B01", action="belegen",
                                                                                   replacement=None)])
        self.assertIn("ACCEPTED part 1: findings=2", out)
        self.assertFalse((self.repo / run / "final.json").exists(), "final.json marks completion only")
        out = self.submit(run, 2, [])
        folder = f"{REVIEWS_DIR}/guide.md-1-{hashlib.sha256(PAGE.encode()).hexdigest()[:8]}"
        for token in ("REVIEW", "parts=2, findings=2, replacements=1, open=1", f"RESPONSE: {folder}/response.json",
                      f"VERIFIED[edit.review] BC {run}/final.json", "apply --run"):
            self.assertIn(token, out)
        url = next(ln.split()[1] for ln in out.splitlines() if ln.startswith("PAGE: "))
        self.assertRegex(url, rf"^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{{22}}/{folder.split('/')[-1]}/$", "port, token, folder")
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertIn(b'id="vae-data"', r.read(), "the link serves the page")
        final = json.loads((self.repo / run / "final.json").read_text())
        self.assertEqual((final["model"], final["auditEnabled"], final["source"]), ({"provider": "harness", "model": "claude-test"}, False, "guide.md"))
        step = json.loads((self.repo / run / "steps/0001.json").read_text())
        self.assertEqual(list(step), ["part", "window", "previousRevision", "summary", "delta", "audit", "customInput", "custom", "calls",
                                     "support"])
        data = page_data(self.repo / folder / "index.html")
        self.assertEqual([c["quote"] for c in data["comments"]], ["The service is very fast.", "It answers quickly."])
        self.assertEqual((data["markdown"], data["meta"]["findings"]["p1-2"]["line"]), (PAGE, 3))
        self.assertEqual([f["status"] for f in data["meta"]["findings"].values()], [None, None], "nothing applied yet")
        self.assertEqual((data["meta"]["review"], data["meta"]["snapshot"]), (folder, f"{folder}/source.md"))
        self.assertEqual((self.repo / folder / "source.md").read_text(), PAGE, "the reviewed bytes outlive the run's tidy")
        doc = json.loads((self.repo / folder / "doc.json").read_text())
        self.assertEqual((doc["page"], doc["revision"], doc["run"]), ("guide.md", 1, run))
        self.assertFalse((self.repo / run / "review.html").exists(), "the human's page lives in the review folder only")
        show = self.cli("show", "--run", run).stdout
        self.assertIn("guide.md:3 p1-1 S03/gering/umformulieren replacement", show)
        out = self.cli("apply", "--run", run).stdout
        self.assertIn("APPLIED 1/1 replacements to guide.md", out)
        self.assertIn("OPEN guide.md:3 B01 belegen", out)
        self.assertEqual((self.repo / "guide.md").read_text(), PAGE.replace("The service is very fast.", "The service is fast."))
        applied = json.loads((self.repo / run / "applied.json").read_text())
        self.assertEqual(applied["applied"], [{"part": 1, "index": 1}])
        data = page_data(self.repo / folder / "index.html")
        self.assertIn("**Status:** applied", data["comments"][0]["body"])
        self.assertEqual([f["status"] for f in data["meta"]["findings"].values()], ["applied", None], "the page dashes p1-1; p1-2 stays open")
        self.assertIn(f"PAGE: {url} ", out, "apply rewrites the same review folder")
        self.assertIn("already applied", self.cli("apply", "--run", run, ok=(2,)).stdout)
        again = self.begin()
        self.submit(again, 1, [])
        self.assertIn(f"{REVIEWS_DIR}/guide.md-2-", self.submit(again, 2, []), "the next review of the page is revision 2")
        (self.repo / f"{REVIEWS_DIR}/guide.md-3-{folder[-8:]}").mkdir()  # taken, without doc.json
        third = self.begin()
        self.submit(third, 1, [])
        self.assertIn(f"{REVIEWS_DIR}/guide.md-4-", self.submit(third, 2, []), "a taken folder moves the review up")

    def test_the_next_review_revision_holds_only_changes_a_human_comment_asked_for(self):
        run = self.begin()
        self.submit(run, 1, [finding("The service is very fast.")])
        self.submit(run, 2, [])
        self.cli("apply", "--run", run)
        folder = f"{REVIEWS_DIR}/guide.md-1-{hashlib.sha256(PAGE.encode()).hexdigest()[:8]}"
        applied = PAGE.replace("The service is very fast.", "The service is fast.")
        meta = page_data(self.repo / folder / "index.html")["meta"]
        self.write(f"{folder}/response.json", json.dumps({"vae": meta, "version": 1, "comments": [
            {"id": "p1-1", "author": "Agent", "anchor": {"block": "b2", "text": "The service is very fast."}, "body": "S03"},
            {"id": "c1", "author": "You", "parent": "p1-1", "body": "Good. Say it answers in 40 ms."},
            {"id": "c2", "author": "You", "anchor": {"block": "b4", "text": "Run the check daily."}, "body": "Hourly."}]}))
        notes = self.write("tmp/notes.json", "[]")
        self.assertIn("read the response first", self.cli("review", folder, "--notes", str(notes), "--check", ok=(2,)).stdout)
        comments = lambda f: subprocess.run([PY, str(ROOT / "scripts/vae.py"), "comments", str(self.repo / f), "--repo", str(self.repo)],
                                            capture_output=True, text=True, check=True).stdout
        self.assertIn(f"every other byte stays as in {folder}/base.md", comments(folder))
        self.assertEqual((self.repo / folder / "base.md").read_text(), applied, "the page when the human's response was read")
        revised = applied.replace("It answers quickly.", "It answers in 40 ms.").replace("daily.", "hourly.")
        self.write("guide.md", revised.replace("# Guide", "# User guide").replace("is fast.", "is quick."))
        comments(folder)
        self.assertEqual((self.repo / folder / "base.md").read_text(), applied, "a second read keeps the first base")
        note = lambda on, quote, body: {"on": on, "quote": quote, "body": body}
        self.write("tmp/notes.json", json.dumps([note("p1-1", "It answers in 40 ms.", "Done."), note("c2", "Run the check hourly.", "Changed."),
                                                 note(None, "# User guide", "A clearer title?")]))
        out = self.cli("review", folder, "--notes", str(notes), "--check", ok=(1,)).stdout
        self.assertIn('LOOSE guide.md:1 "Guide" → "User guide"', out, "a note on null only comments")
        self.assertIn('LOOSE guide.md:3 "fast." → "quick."', out, "a quote covers its words, not its whole paragraph")
        self.assertIn("UNKNOWN[revision] BC 2 change(s) lie inside no note's quote", out)
        for bad, why in (([note("zz", "Run the check hourly.", "x")], "none of the human's comments (c2, chat, p1-1) or null"),
                         ([note("chat", "# User guide", "x")], "and said (the human's words) when on is chat"),
                         ([note("c2", "Run the check weekly.", "x")], "its quote is not in guide.md as it is now"),
                         ([{"on": "c2", "quote": "Run"}], "exactly on, quote and body")):
            self.write("tmp/notes.json", json.dumps(bad))
            self.assertIn(why, self.cli("review", folder, "--notes", str(notes), "--check", ok=(2,)).stdout)
        self.write("guide.md", revised.replace("# Guide", "# User guide") + "\n## Limits\n\nNeeds network.\n")
        self.write("tmp/notes.json", json.dumps([note("p1-1", "It answers in 40 ms.", "Done."), note("c2", "Run the check hourly.", "Changed."),
                                                 note("chat", "# User guide", "Renamed.") | {"said": "Call it a user guide."},
                                                 note(None, "## Use", "Is this heading still right?"),
                                                 note("chat", "## Limits\n\nNeeds network.\n", "Added.") | {"said": "Say it needs network."}]))
        out = self.cli("review", folder, "--notes", str(notes), "--check").stdout
        self.assertIn("REVISION guide.md since", out)
        self.assertIn("changes=3, notes=5", out, "hourly and the new section merge into one change")
        self.assertIn("VERIFIED[revision] BC every change since base.md", out)
        self.assertEqual(len(list((self.repo / REVIEWS_DIR).glob("guide.md-*"))), 1, "--check writes nothing")
        out = self.cli("review", folder, "--notes", str(notes)).stdout
        revised = revised.replace("# Guide", "# User guide") + "\n## Limits\n\nNeeds network.\n"
        second = f"{REVIEWS_DIR}/guide.md-2-{hashlib.sha256(revised.encode()).hexdigest()[:8]}"
        self.assertIn(f"REVIEW {second}: revision 2, comments=5", out)
        self.assertIn(f"RESPONSE: {second}/response.json", out)
        data = page_data(self.repo / second / "index.html")
        self.assertEqual((data["markdown"], data["title"], data["meta"]["previous"]), (revised, "Review: guide.md (revision 2)", folder))
        first, _, third, fourth, fifth = data["comments"]
        self.assertEqual((first["id"], first["quote"]), ("r2-1", "It answers in 40 ms."))
        self.assertEqual(first["body"], "> **You** on p1-1: Good. Say it answers in 40 ms.\n\nDone.\n\n**Before:** It answers quickly.")
        self.assertEqual(third["body"], "> **You** in chat: Call it a user guide.\n\nRenamed.\n\n**Before:** # Guide")
        self.assertEqual(fourth["body"], "Is this heading still right?", "a note on null quotes no one, and unchanged text has no before")
        self.assertTrue(fifth["body"].endswith("Added.\n\n**Before:** (new text)"), "a new section's blank line goes with it")
        self.assertEqual([f["status"] for f in data["meta"]["findings"].values()], ["applied", "applied", "applied", None, "applied"],
                         "a note on a change already made is applied; a question is not")
        self.assertEqual(json.loads((self.repo / second / "doc.json").read_text())["previous"], folder)
        self.write(f"{second}/response.json", json.dumps({"vae": data["meta"], "version": 1, "comments": [
            {"id": "r2-1", "author": "Agent", "body": "Done."}, {"id": "c3", "author": "You", "parent": "r2-1", "body": "Thanks."}]}))
        self.assertIn('REPLY guide.md:3 r2-1 "It answers in 40 ms." by You: Thanks.', comments(second))
        self.assertEqual((self.repo / second / "base.md").read_text(), revised, "each revision records its own base")

    def test_apply_refuses_after_an_edit_made_since_the_snapshot(self):
        run = self.begin()
        self.submit(run, 1, [finding("The service is very fast.")])
        self.submit(run, 2, [])
        self.write("guide.md", PAGE + "\nA line the human added.\n")
        self.assertIn("changed after the snapshot", self.cli("apply", "--run", run, ok=(2,)).stdout)
        self.assertTrue((self.repo / "guide.md").read_text().endswith("A line the human added.\n"))

    def test_file_evidence_quotes_the_repository_as_it_is(self):
        self.write("src/app.py", "SPEED = 'fast'\n")
        run = self.begin()
        cite = lambda q: [finding("The service is very fast.", evidence=[{"quelle": "datei", "pfad": "src/app.py", "zitat": q, "vorkommen": 1}])]
        self.assertIn("not present", self.submit(run, 1, cite("SPEED = 'slow'"), "high", (2,)))
        self.assertIn("S03 cited evidence in an earlier answer", self.submit(run, 1, [finding("The service is very fast.")], "high", (2,)),
                      "a rejected submission's evidence binds the next one")
        self.submit(run, 1, cite("SPEED = 'fast'"))

    def test_a_submission_whose_evidence_the_decision_model_refutes_goes_back(self):
        verdict = {"value": ("no", 0.95)}
        decisions(self, None, support=lambda state: verdict["value"] if "quickly" in state["evidence"][0]["quote"] else ("yes", 0.9))
        run = self.begin()
        cite = lambda q: [finding("The service is very fast.", evidence=[{"quelle": "text", "zitat": q, "vorkommen": 1}])]
        self.assertIn("does not support the comment: S03", self.submit(run, 1, cite("It answers quickly."), "high", (2,)))
        self.assertIn("S03 cited evidence in an earlier answer", self.submit(run, 1, [finding("The service is very fast.")], "high", (2,)),
                      "a refuted citation cannot be deleted to keep `hoch`")
        self.assertIn("ACCEPTED part 1", self.submit(run, 1, cite("The service is very fast.")))
        step = json.loads((self.repo / run / "steps/0001.json").read_text())
        self.assertEqual(step["support"], [{"finding": 0, "issue": 0, "rule": "S03", "decision": "yes (0.90)"}])
        verdict["value"] = None
        run = self.begin()
        out = self.submit(run, 1, cite("It answers quickly."))
        self.assertIn("UNKNOWN[evidence-support] BC the decision model gave no answer for 1 issue(s)", out)
        self.assertIn("ACCEPTED part 1", out)

    def test_without_an_engine_an_unsure_part_is_said_to_stand_alone(self):
        run = self.begin()
        out = self.submit(run, 1, [finding("The service is very fast.")], "medium")
        self.assertIn("UNKNOWN[second-opinion] BC no .vae/.env.openai.json", out)
        self.assertIn("ACCEPTED part 1", out)

    def engine_server(self, findings_by_part: dict[str, Any]) -> HttpFixture:
        srv = HttpFixture(llm(lambda b: E.js_json(findings_by_part.get(envelope(b)["eingabe"]["abschnitt_id"], [])), key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, llm_url=srv.url, secret=SECRET)
        return srv

    def test_an_unsure_part_gets_the_engines_review_and_a_checked_consensus(self):
        mine = [finding("The service is very fast.")]
        theirs = [finding("The service is very fast."), finding("It answers quickly.", rule="P05", action="praezisieren", replacement=None)]
        srv = self.engine_server({"1": theirs})
        run = self.begin()
        out = self.submit(run, 1, mine, "medium", (2,))
        for token in ("SECOND OPINION part 1 (m-reasoner): agent=1 engine=2 pairs=1 agent-only=0 engine-only=1", "ENGINE:",
                      "[1] \"It answers quickly.\" P05/gering/praezisieren (open)", "--decisions <decisions.json>"):
            self.assertIn(token, out)
        opinion = json.loads((self.repo / run / "steps/0001.opinion.json").read_text())
        self.assertEqual((opinion["pairs"], opinion["engineOnly"], opinion["confidence"]), ([[0, 0]], [1], "medium"))
        self.assertEqual(opinion["route"], {"role": "reasoner"}, "no thinking role: the base role, unscored")
        self.assertEqual(opinion["language"], {}, "no decision model: the prompts' own language rule")
        self.assertFalse((self.repo / run / "steps/0001.json").exists(), "nothing commits before the consensus")
        merged = self.draft(run, 1, theirs)
        decide = lambda d: self.cli("submit", "--run", run, "--part", "1", "--findings", merged, "--decisions",
                                    self.draft(run, 99, d), ok=(0, 2)).stdout
        self.assertIn("every unpaired finding needs a decision", decide([]))
        self.assertIn("names no unpaired finding", decide([{"source": "agent", "index": 0, "keep": True, "reason": "x"}]))
        self.assertIn("no consensus finding covers its quote", self.cli(
            "submit", "--run", run, "--part", "1", "--findings", self.draft(run, 1, mine), "--decisions",
            self.draft(run, 99, [{"source": "engine", "index": 1, "keep": True, "reason": "vague"}]), ok=(2,)).stdout)
        self.draft(run, 1, theirs)
        out = decide([{"source": "engine", "index": 1, "keep": True, "reason": "'quickly' names no measure"}])
        self.assertIn("ACCEPTED part 1: findings=2", out)
        opinion = json.loads((self.repo / run / "steps/0001.opinion.json").read_text())
        self.assertEqual(opinion["decisions"][0]["reason"], "'quickly' names no measure")
        step = json.loads((self.repo / run / "steps/0001.json").read_text())
        self.assertEqual(([c["role"] for c in step["calls"]], len(step["custom"])), (["opinion"], 2))
        self.assertTrue(list((self.repo / run / "steps").glob("0001.trace.jsonl")), "the second opinion is traced")
        self.assertNotIn(SECRET, (self.repo / run / "steps/0001.trace.jsonl").read_text())
        self.assertEqual(sum(1 for m, p, h, b in srv.requests if p.endswith("/chat/completions")), 1)

    def test_an_engine_that_agrees_commits_without_a_consensus_round(self):
        mine = [finding("The service is very fast.")]
        self.engine_server({"1": mine})
        run = self.begin()
        out = self.submit(run, 1, mine, "low")
        self.assertIn("VERIFIED[second-opinion] BC the engine's findings for part 1 equal the agent's", out)
        self.assertIn("ACCEPTED part 1", out)

    def test_the_typed_decision_gate_keeps_a_replacement_it_doubts_open(self):
        verdict = {"label": "no", "confidence": 0.93}
        srv = HttpFixture(typesafe(lambda b: {"answer": {"type": "choice", "choice": verdict["label"], "confidence": verdict["confidence"],
                                                         "probabilities": {"yes": 0.07, "no": 0.93}}}, key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, decide_url=srv.url, secret=SECRET)
        run = self.begin()
        self.submit(run, 1, [finding("The service is very fast.")])
        self.submit(run, 2, [])
        out = self.cli("apply", "--run", run).stdout
        self.assertIn("APPLIED 0/1", out)
        self.assertIn("SKIPPED p1-1: typed decision: the meaning changes (no, 0.93)", out)
        self.assertEqual((self.repo / "guide.md").read_text(), PAGE)
        self.assertIn("APPLIED 0/1", self.cli("apply", "--run", run).stdout, "an apply that wrote nothing may run again")
        state = srv.requests[-1][3]["state"]
        self.assertEqual(state, {"original": "The service is very fast.", "replacement": "The service is fast."})
        verdict.update(label="no", confidence=0.99)
        run = self.begin()
        self.submit(run, 1, [finding("The service is very fast.", rule="B01", action="revidieren", replacement="The service answers in 40 ms.")])
        self.submit(run, 2, [])
        self.assertIn("APPLIED 1/1", self.cli("apply", "--run", run).stdout, "a correction changes the content by design: not asked")
        self.assertEqual(json.loads((self.repo / run / "applied.json").read_text())["applied"], [{"part": 1, "index": 1}])
        for label, confidence in (("no", 0.4), ("yes", 0.5)):  # only a confident `no` holds a replacement back
            verdict.update(label=label, confidence=confidence)
            run = self.begin()
            self.submit(run, 1, [finding("The service is very fast.")])
            self.submit(run, 2, [])
            self.assertIn("APPLIED 1/1", self.cli("apply", "--run", run).stdout, label)
            applied = json.loads((self.repo / run / "applied.json").read_text())["applied"]
            self.assertEqual(applied[0]["decision"], f"{label} ({confidence:.2f})")

    def test_the_engine_reviews_a_whole_page_from_the_cli(self):
        srv = HttpFixture(llm(standard, key=SECRET))
        self.addCleanup(srv.close)
        write_pairs(self.write, llm_url=srv.url, secret=SECRET)
        self.write("guide.md", PAGE)
        out = self.cli("start", "guide.md", "--engine").stdout
        self.assertIn("engine m-reasoner, parts=2", out)
        self.assertIn("VERIFIED[edit.review]", out)
        run = next(ln.split()[1].rstrip(":") for ln in out.splitlines() if ln.startswith("REVIEW"))
        self.assertEqual(json.loads((self.repo / run / "final.json").read_text())["summary"]["bis_seite"], 2)
        self.assertIn("all parts are already committed", self.cli("resume", "--run", run, ok=(2,)).stdout)

    def test_settings_are_validated(self):
        self.assertEqual(E.settings({})[0], E.DEFAULTS)
        for bad in ({"edit": {"attempts": 0}}, {"edit": {"tool_scope": "web"}}, {"edit": {"nope": 1}}, {"guidelines": []},
                    {"edit": {"thinking_score": 2.5}}, {"edit": {"thinking_score": True}}):
            with self.assertRaises(E.EditError, msg=bad):
                E.settings({"prose": bad})


if __name__ == "__main__":
    unittest.main()
