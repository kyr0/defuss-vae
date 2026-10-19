"""vae_annotate: the review page carries any Markdown intact, and a downloaded comments file reads back as the human's
replies and new comments with their page lines. tests/site/e2e.mjs drives the page itself in Chrome."""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from vae_testkit import PY, ROOT, RepoCase

# isort: split
from vae_annotate import (
    MAX_RESPONSE,
    REVIEWS_DIR,
    ensure_server,
    link,
    page_data,
    page_html,
    read_back,
    stop_server,
    write_page,
)

PAGE = "# Guide\n\nThe service is very fast. It answers quickly.\n\n## Use\n\nRun the check daily.\n"
RUN = "tmp/vae/edit/guide-2026-10-12T10-00-00-000Z-a1b2c3"


def export(comments: list[dict]) -> dict:
    return {"vae": {"kind": "edit", "run": RUN, "source": "guide.md", "snapshot": f"{RUN}/source.md", "documentId": "x",
                    "findings": {"p1-1": {"part": 1, "index": 1, "line": 3, "quote": "The service is very fast."}}},
            "version": 1, "exported": "2026-10-12T10:00:00Z", "comments": comments}


class AnnotateTests(RepoCase):
    def test_the_page_carries_any_markdown_and_title_intact(self):
        markdown = "# T\n\n```html\n</script><script>alert(1)</script>\n<!-- c -->\n```\nline\u2028sep & <b>\n\nA --> B __VAE_DATA__\n"
        html = page_html("A <b> & title", markdown, [{"id": "c1", "quote": "</script>", "body": "x"}], {"kind": "edit"})
        self.assertNotIn("</script><script>alert", html)
        self.assertEqual(html.count("A --> B"), 1, "the data lands in its script only, never in a comment it could close")
        self.assertIn("<title>A &lt;b&gt; &amp; title</title>", html)
        self.assertNotIn("<title>__VAE_TITLE__", html)
        self.assertNotIn('id="vae-data">__VAE_DATA__', html, "page text naming a slot stays content")
        path = self.repo / "tmp" / "page.html"
        write_page(path, "A <b> & title", markdown, [{"id": "c1", "quote": "</script>", "body": "x"}], {"kind": "edit"})
        data = page_data(path)
        self.assertEqual((data["markdown"], data["title"], data["comments"][0]["quote"]), (markdown, "A <b> & title", "</script>"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600, "the page holds the document's text")
        template = (ROOT / "templates" / "annotate.html").read_text()
        for pinned in ("defuss-shadcn@v0.9.9/dist/apps/document-editor/document-editor.min.js", "lucide@1.8.0"):
            self.assertIn(pinned, template, "builds are pinned, never latest")

    def test_replies_and_new_comments_read_back_with_their_page_lines(self):
        self.write(f"{RUN}/source.md", PAGE)
        path = self.write("downloads/review.comments.json", json.dumps(export([
            {"id": "p1-1", "author": "Agent", "anchor": {"block": "b2", "text": "The service is very fast."}, "body": "S03"},
            {"id": "r1", "author": "You", "parent": "p1-1", "body": "Keep it?\nIt is a measured claim."},
            {"id": "r2", "author": "You", "parent": "r1", "body": "Yes, cite the benchmark."},
            {"id": "n1", "author": "You", "anchor": {"block": "b4", "text": "Run the check daily."}, "body": "Hourly, not daily."},
            {"id": "n2", "author": "You", "anchor": {"block": "b9", "text": "gone"}, "body": "?"}])))
        code, lines = read_back(self.repo, path)
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], f"COMMENTS {RUN} (edit of guide.md): agent=1, replied=1, new=2, replies=2, edits=0")
        self.assertIn('REPLY guide.md:3 p1-1 "The service is very fast." by You: Keep it? / It is a measured claim.', lines)
        self.assertIn('REPLY guide.md:3 p1-1 "The service is very fast." by You: Yes, cite the benchmark.', lines, "a reply to a reply")
        self.assertIn('NEW guide.md:7 n1 "Run the check daily." by You: Hourly, not daily.', lines)
        self.assertIn('NEW guide.md:? n2 "gone" by You: ?', lines)
        self.assertTrue(lines[-1].startswith("DO: act on EVERY ACCEPT, REJECT, REPLY and NEW line"))
        p = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "comments", str(path), "--repo", str(self.repo)],
                           capture_output=True, text=True, check=False)
        self.assertEqual((p.returncode, p.stdout.splitlines()), (0, lines))

    def test_quick_verdicts_read_back_with_their_action_and_unanswered_applied_findings_count_as_accepted(self):
        self.write(f"{RUN}/source.md", PAGE)
        quote = "The service is very fast."
        finding = lambda status: {"line": 3, "quote": quote, "status": status}
        findings = {"p1-1": finding("applied"), "p1-2": finding("not applied: typed decision: the meaning changes (no, 0.94)"),
                    "p1-3": finding(None), "p1-4": finding("applied"), "p1-5": finding("applied"), "p1-6": finding("applied")}
        verdict = lambda i, parent, v: {"id": f"v{i}", "author": "You", "parent": parent, "body": "✅ Accepted", "verdict": v}
        agent = [{"id": fid, "author": "Agent", "anchor": {"block": "b2", "text": quote}, "body": "S03"} for fid in findings]
        upload = lambda comments: self.write("r.json", json.dumps(export(agent + comments) | {"vae": export([])["vae"] | {"findings": findings}}))
        code, lines = read_back(self.repo, upload([
            verdict(1, "p1-1", "accept"), verdict(2, "p1-2", "accept"), verdict(3, "p1-3", "reject"),
            verdict(4, "p1-4", "accept"), verdict(5, "p1-4", "reject"),
            {"id": "r6", "author": "You", "parent": "p1-6", "body": "Keep the old wording."}]))
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], f"COMMENTS {RUN} (edit of guide.md): agent=6, replied=5, new=0, replies=6, edits=0")
        at = f'guide.md:3 {{}} "{quote}"'
        self.assertEqual(lines[1:-1], [f"REPLY {at.format('p1-6')} by You: Keep the old wording.",
                                       f"ACCEPT {at.format('p1-1')} applied → keep it",
                                       f"ACCEPT {at.format('p1-2')} not applied → apply its replacement",
                                       f"REJECT {at.format('p1-3')} → leave the text as it is",
                                       f"REJECT {at.format('p1-4')} applied → restore its original text",
                                       "ACCEPTED 1 applied finding(s) without a reply → keep them: p1-5"],
                         "a verdict is no REPLY line, the last verdict stands, and a written reply is no silence")
        self.assertTrue(lines[-1].startswith("DO: act on EVERY ACCEPT, REJECT, REPLY and NEW line"))
        self.assertEqual(read_back(self.repo, upload([]))[1][1:], ["ACCEPTED 4 applied finding(s) without a reply → keep them: p1-1, p1-4, p1-5, p1-6",
                                                                    "∅: the human added nothing"], "silence changes nothing")
        stray = {"id": "n1", "author": "You", "anchor": {"block": "b4", "text": "Run the check daily."}, "body": "Hourly."}
        lines = read_back(self.repo, upload([stray, verdict(7, "n1", "accept")]))[1]
        self.assertIn('REPLY guide.md:7 n1 "Run the check daily." by You: ✅ Accepted', lines, "a verdict on no finding is a plain reply")
        odd = read_back(self.repo, upload([verdict(8, "p1-3", ["accept"])]))[1]
        self.assertIn(f"REPLY {at.format('p1-3')} by You: ✅ Accepted", odd, "an unknown verdict is a plain reply, never a crash")
        mangled = export(agent) | {"vae": export([])["vae"] | {"findings": ["p1-1"]}}
        self.assertEqual(read_back(self.repo, self.write("m.json", json.dumps(mangled)))[0], 0, "findings that are no map read as none")

    def test_a_response_records_the_page_as_base_only_in_the_review_folder_it_names(self):
        self.write("guide.md", PAGE)
        self.write(f"{RUN}/source.md", PAGE)
        folder = f"{REVIEWS_DIR}/guide.md-1-abcdef12"
        self.write(f"{folder}/doc.json", json.dumps({"page": "guide.md", "revision": 1}))
        self.write("tmp/elsewhere/doc.json", "{}")
        agent = [{"id": "p1-1", "author": "Agent", "body": "S03"}]
        reply = [*agent, {"id": "r1", "author": "You", "parent": "p1-1", "body": "Yes."}]
        response = lambda comments, review: self.write("r.json", json.dumps(export(comments) | {"vae": export([])["vae"] | {"review": review}}))
        for review in ("tmp/elsewhere", f"{REVIEWS_DIR}/../tmp/elsewhere", "guide.md"):
            self.assertTrue(read_back(self.repo, response(reply, review))[1][-1].startswith("DO: act on EVERY"), review)
        self.assertEqual(read_back(self.repo, response(agent, folder))[1][-1], "∅: the human added nothing")
        self.assertEqual(list(self.repo.rglob("base.md")), [], "nothing to revise, and no folder outside .vae/doc-reviews/")
        outside = self.repo.parent / "outside.txt"
        outside.write_text("not the page")
        self.write(f"{folder}/doc.json", json.dumps({"page": "../outside.txt", "revision": 1}))
        read_back(self.repo, response(reply, folder))
        self.assertFalse((self.repo / folder / "base.md").exists(), "doc.json names a file outside the repository")
        self.write(f"{folder}/doc.json", json.dumps({"page": "guide.md", "revision": 1}))
        upload = export(reply) | {"vae": export([])["vae"] | {"review": folder, "source": str(outside)}}
        self.assertIn(f"every other byte stays as in {folder}/base.md", read_back(self.repo, self.write("r.json", json.dumps(upload)))[1][-1])
        self.assertEqual((self.repo / folder / "base.md").read_text(), PAGE, "the page doc.json names, never the upload's source")

    def test_untouched_pages_and_foreign_files_say_so(self):
        self.write(f"{RUN}/source.md", PAGE)
        quiet = self.write("quiet.json", json.dumps(export([{"id": "p1-1", "author": "Agent", "body": "S03"}])))
        self.assertEqual(read_back(self.repo, quiet), (0, [f"COMMENTS {RUN} (edit of guide.md): agent=1, replied=0, new=0, replies=0, edits=0",
                                                           "∅: the human added nothing"]))
        for name, text in (("broken.json", "{"), ("other.json", json.dumps({"version": 1, "comments": []}))):
            code, lines = read_back(self.repo, self.write(name, text))
            self.assertEqual(code, 2, name)
            self.assertTrue(lines[0].startswith("UNKNOWN[comments]"), lines)

    def test_the_humans_text_edits_read_back_from_the_review_folder_as_hunks_with_page_lines(self):
        self.write(f"{RUN}/source.md", PAGE)
        baseline = "# Guide\n\nThe service is very fast. It answers quickly.\n\n## Use\n\nRun the check daily."
        edited = baseline.replace("It answers quickly.", "It answers in 40 ms.").replace("daily.", "hourly.") + "\n\nA new line."
        folder = f"{REVIEWS_DIR}/guide.md-1-abcdef12"
        self.write(f"{folder}/response.json", json.dumps(export([]) | {"document": {"baseline": baseline, "edited": edited}}))
        code, lines = read_back(self.repo, self.repo / folder)
        self.assertEqual((code, lines[0]), (0, f"COMMENTS {RUN} (edit of guide.md): agent=1, replied=0, new=0, replies=0, edits=2"))
        self.assertIn("EDITS guide.md: 2 hunk(s) of the human's text changes, a diff of the review page's Markdown (`-` before, `+` after)", lines)
        at = lines.index("@@ guide.md:3 @@")
        self.assertEqual(lines[at + 1:at + 4], [" ", "-The service is very fast. It answers quickly.",
                                                "+The service is very fast. It answers in 40 ms."])
        self.assertIn("@@ guide.md:7 @@", lines)
        self.assertIn("+A new line.", lines)
        self.assertTrue(lines[-1].startswith("DO: act on EVERY ACCEPT, REJECT, REPLY and NEW line, and carry EVERY EDITS hunk into the page"))
        self.write(f"{folder}/response.json", json.dumps(export([]) | {"document": {"baseline": baseline, "edited": baseline}}))
        self.assertEqual(read_back(self.repo, self.repo / folder)[1][-1], "∅: the human added nothing", "an unchanged text is no edit")


class ServerTests(RepoCase):
    """The review server as a real detached process on 127.0.0.1."""

    def setUp(self):
        super().setUp()
        self.folder = "guide.md-1-abcdef12"
        self.write(f"{REVIEWS_DIR}/{self.folder}/index.html", "<!doctype html><title>review</title>")
        self.write(f"{REVIEWS_DIR}/{self.folder}/doc.json", json.dumps({"page": "guide.md", "revision": 1}))

    def tearDown(self):
        stop_server(self.repo)  # before RepoCase deletes the repo with the server's state
        super().tearDown()

    def request(self, base: str, method: str, path: str, body: bytes | None = None, **headers: str) -> tuple[int, bytes]:
        """`path` below the base URL (which ends in the token), or from the server root when it starts with `//`."""
        port = int(base.split(":")[2].split("/")[0])
        path = path[1:] if path.startswith("//") else urlsplit(base).path.rstrip("/") + path
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in {"Host": f"127.0.0.1:{port}", **headers}.items():
                conn.putheader(name.replace("_", "-"), value)
            conn.endheaders(body)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def send(self, base: str, value: object, path: str | None = None, **headers: str) -> int:
        body = json.dumps(value).encode()
        port = base.split(":")[2].split("/")[0]
        hdrs = {"Origin": f"http://127.0.0.1:{port}", "Content_Type": "application/json", "Content_Length": str(len(body)), **headers}
        return self.request(base, "POST", path or f"/{self.folder}/response.json", body, **hdrs)[0]

    def test_it_serves_the_review_folders_and_saves_only_the_pages_own_response(self):
        base = ensure_server(self.repo)
        self.assertRegex(base, r"^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{22}/$")
        status, body = self.request(base, "GET", f"/{self.folder}/")
        self.assertEqual((status, body), (200, b"<!doctype html><title>review</title>"))
        self.assertEqual(self.request(base, "GET", f"//{self.folder}/")[0], 404, "without the token the folders do not exist")
        token = base.rstrip("/").rsplit("/", 1)[1]
        self.assertNotIn(token, subprocess.run(["ps", "-ax", "-o", "command"], capture_output=True, text=True, check=False).stdout,
                         "the token stays off the command line, which other users can read")
        self.assertEqual(self.request(base, "GET", f"/{self.folder}/", Host="evil.example:80")[0], 403, "DNS rebinding: a foreign Host")
        self.assertEqual(self.request(base, "GET", "/.gitignore")[0], 404, "no dotfiles")
        response = {"vae": {"kind": "edit"}, "version": 1, "comments": [], "document": {"baseline": "a", "edited": "b"}}
        self.assertEqual(self.send(base, response), 200)
        saved = self.repo / REVIEWS_DIR / self.folder / "response.json"
        self.assertEqual((json.loads(saved.read_text()), saved.stat().st_mode & 0o777), (response, 0o600))
        refused = {"a foreign Origin": (self.send(base, response, Origin="https://evil.example"), 403),
                   "no Origin": (self.send(base, response, Origin=""), 403),
                   "a form's content type": (self.send(base, response, Content_Type="text/plain"), 415),
                   "a folder without doc.json": (self.send(base, response, "/other/response.json"), 404),
                   "no token": (self.send(base, response, f"//{self.folder}/response.json"), 404),
                   "another file name": (self.send(base, response, f"/{self.folder}/index.html"), 404),
                   "not a review response": (self.send(base, {"comments": []}), 422),
                   "an oversized body": (self.send(base, response, Content_Length=str(MAX_RESPONSE + 1)), 413)}
        for why, (status, want) in refused.items():
            self.assertEqual(status, want, why)
        self.assertEqual(json.loads(saved.read_text()), response, "a refused request leaves the saved response alone")

    def test_one_server_per_repo_is_reused_stopped_and_restarted_on_its_port(self):
        base = ensure_server(self.repo)
        self.assertEqual(ensure_server(self.repo), base, "a running server is reused")
        out = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "edit", "--repo", str(self.repo), "serve"], capture_output=True, text=True, check=False)
        self.assertIn(f"SERVING {base} ({REVIEWS_DIR}/; stop: ", out.stdout)
        self.assertTrue(stop_server(self.repo))
        with self.assertRaises(OSError):
            self.request(base, "GET", "/")
        self.assertFalse(stop_server(self.repo), "nothing left to stop")
        self.assertEqual(ensure_server(self.repo), base, "a restart takes the previous port while it is free")
        out = subprocess.run([PY, str(ROOT / "scripts/vae.py"), "edit", "--repo", str(self.repo), "serve", "--stop"], capture_output=True, text=True, check=False)
        self.assertEqual(out.stdout.strip(), "STOPPED the review server")
        log = (self.repo / "tmp/vae/review-server.log").read_text()
        self.assertRegex(log.splitlines()[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ INFO serving ")

    def test_a_server_whose_folder_is_gone_ends_itself(self):
        base = ensure_server(self.repo)
        state = json.loads((self.repo / "tmp/vae/review-server.json").read_text())
        shutil.rmtree(self.repo / REVIEWS_DIR)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and subprocess.run(["kill", "-0", str(state["pid"])], capture_output=True, check=False).returncode == 0:
            time.sleep(0.1)
        with self.assertRaises(OSError):
            self.request(base, "GET", "/")

    def test_without_a_server_the_link_names_the_file(self):
        (self.repo / "tmp/vae/review-server.json").mkdir(parents=True)  # the server cannot record its state
        page = self.repo / REVIEWS_DIR / self.folder / "index.html"
        lines = link(self.repo, page)
        self.assertEqual(lines[0], f'PAGE: {REVIEWS_DIR}/{self.folder}/index.html (open the file; "Send to agent" needs the server)')
        self.assertTrue(lines[1].startswith("UNKNOWN[review-server] BC "), lines)
