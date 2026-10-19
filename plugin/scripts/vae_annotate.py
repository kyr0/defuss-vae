"""The review page: one standalone HTML file per edit run or research report, where the human reads the Markdown with
the agent's comments, replies, adds comments and downloads them as JSON for the agent to read back.

WHY a page, not chat: comments sit on the exact text they are about, a reply stays under its comment, and the human
answers many findings at once instead of one question per turn. WHY defuss-shadcn's Document Editor scaffold
(application/scaffold-document-editor, 0.9.9): it is the human's own component framework, it renders Markdown in
Editor.js with a comment column (anchors are block id + text) and it loads pinned builds from jsDelivr, so the plugin
ships one template and no JavaScript bundle. HYPOTHESIS: a page opened from disk (`file://`) loads those module builds,
since jsDelivr serves them with CORS; falsifier: tests/site/e2e.mjs opening a generated page from disk.

The page holds the agent's comments as `{id, author, time, color, quote, body}`; its script turns each Markdown quote
into an anchor on the rendered text. The response is `{vae: meta, version: 1, exported, comments, document}` (the
Document Comments model, version 1, plus `document: {baseline, edited}` for an edit review); a reply from the page's
quick ✅ / ❌ buttons also carries `verdict` (`accept` or `reject`). `meta.findings` maps each agent comment id to its
part, line, quote and apply `status` (`applied`, `not applied: <reason>`, or null), which dashes an applied card.

An edit review's page lives in `.vae/doc-reviews/<page>-<revision>-<sha8>/`, served by one local server per repo
(`ensure_server`): the human opens a link instead of a file, edits and comments, and "Send to agent" saves
`response.json` into the folder. WHY a server: a link opens in one click from the agent's answer, and only a server
can take the response back without a download the human must hand over.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import secrets
import signal
import socketserver
import subprocess
import sys
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from vae_repo import STATE_DIR, now_iso, read_json, runtime_dir, write_private

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "annotate.html"
REVIEWS_DIR = f"{STATE_DIR}/doc-reviews"  # one folder per review of a page, gitignored by its own `.gitignore`
SERVER_DIR = "tmp/vae"  # the server's state (pid, port) and log: runtime files, never served
RESPONSE = "response.json"
BASE = "base.md"  # the page as it stood when the agent first read the human's response: what the next revision starts from
MAX_RESPONSE = 16 * 1024 * 1024  # bytes: the page's text twice plus comments; a larger body is refused unread
VERDICTS = {"accept": "ACCEPT", "reject": "REJECT"}  # a reply's `verdict`, set by the page's quick ✅ / ❌ buttons


def page_html(title: str, markdown: str, comments: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    """The template filled in. WHY `<` as \\u003c: no page text can close the data script or open a comment in it,
    and JSON.parse restores it exactly."""
    data = json.dumps({"title": title, "markdown": markdown, "comments": comments, "meta": meta}, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = TEMPLATE.read_text("utf-8")
    # WHY the exact slots: a placeholder name anywhere else (a comment) would receive the data too, where page text
    # such as a Mermaid arrow `-->` closes the comment and spills the JSON onto the page.
    for slot, value in (("<title>__VAE_TITLE__</title>", f"<title>{html.escape(title)}</title>"),
                        ('id="vae-data">__VAE_DATA__</script>', f'id="vae-data">{data}</script>')):
        if page.count(slot) != 1:
            raise ValueError(f"{TEMPLATE}: expected one {slot!r}")
        page = page.replace(slot, value)
    return page


def write_page(path: Path, title: str, markdown: str, comments: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    write_private(path, page_html(title, markdown, comments, meta))


def page_data(path: Path) -> dict[str, Any]:
    """The data a written page carries (tests and the agent read it back without a browser)."""
    m = re.search(r'<script type="application/json" id="vae-data">(.*?)</script>', path.read_text("utf-8"), re.DOTALL)
    if not m:
        raise ValueError(f"{path}: no vae-data script")
    return json.loads(m.group(1))


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`~>#|\\]", "", text)).strip()


def locate(source: str, needle: str) -> int | None:
    """The 1-based line where rendered text `needle` starts in Markdown `source`, or None."""
    if not needle.strip():
        return None
    at = source.find(needle)
    if at >= 0:
        return source.count("\n", 0, at) + 1
    want = plain(needle)[:40]
    for n, line in enumerate(source.splitlines(), 1):
        if want and want in plain(line):
            return n
    return None


def one_line(body: Any) -> str:
    return re.sub(r"\s*\n\s*", " / ", str(body or "").strip())


def edit_hunks(source: str, label: str, baseline: str, edited: str) -> list[str]:
    """The human's edits as unified-diff hunks of the editor's Markdown, each headed by the page line where its first
    changed line (else its first context line) starts. WHY a diff of the editor's Markdown, not the edited file: the
    editor writes every block in its own Markdown style and drops what it cannot represent, so copying its output
    would rewrite text nobody edited."""
    a, b = baseline.splitlines(), edited.splitlines()
    out = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(1):
        changed = [a[i] for tag, i1, i2, _, _ in group if tag != "equal" for i in range(i1, i2)]
        context = [a[i] for _, i1, i2, _, _ in group for i in range(i1, i2)]
        line = next((n for n in (locate(source, x) for x in changed + context) if n), None)
        out.append(f"@@ {label}:{line or '?'} @@")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out += [f" {x}" for x in a[i1:i2]]
                continue
            out += [f"-{x}" for x in a[i1:i2]] + [f"+{x}" for x in b[j1:j2]]
    return out


def changes(base: str, text: str) -> list[tuple[int, int, str, str]]:
    """Every change from `base` to `text` at word granularity: its span in `text` (empty for a deletion), the removed
    and the added text. WHY words inside the changed lines: a Markdown paragraph is one line, so a line diff would let
    the quote of one change cover every other change in its paragraph."""
    a, b = base.splitlines(keepends=True), text.splitlines(keepends=True)
    starts = [0]
    for line in b:
        starts.append(starts[-1] + len(line))
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        x, y = re.findall(r"\s+|\S+", "".join(a[i1:i2])), re.findall(r"\s+|\S+", "".join(b[j1:j2]))
        at = [starts[j1]]
        for token in y:
            at.append(at[-1] + len(token))
        out += [(at[l1], at[l2], "".join(x[k1:k2]), "".join(y[l1:l2]))
                for t, k1, k2, l1, l2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes() if t != "equal"]
    return out


def root_of(by_id: dict[str, dict[str, Any]], c: dict[str, Any]) -> dict[str, Any]:
    """The comment a reply (or a reply to a reply) answers."""
    seen = set()
    while c.get("parent") in by_id and c["id"] not in seen:
        seen.add(c["id"])
        c = by_id[c["parent"]]
    return c


def human_threads(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The human's comments in a response by the thread they belong to: a reply under the comment it answers, a new
    comment as its own thread."""
    findings = data["vae"].get("findings") or {}
    by_id = {c["id"]: c for c in data["comments"] if isinstance(c, dict) and isinstance(c.get("id"), str)}
    out: dict[str, list[dict[str, Any]]] = {}
    for c in by_id.values():
        if c.get("parent") or c["id"] not in findings:
            out.setdefault(root_of(by_id, c)["id"], []).append(c)
    return out


def review_folder_of(repo: Path, meta: dict[str, Any]) -> Path | None:
    """The review folder a response names, only when it is one: directly under .vae/doc-reviews/ with doc.json. WHY
    the check: the response is the page's upload, so its `review` field is input, not a path to trust."""
    root = (repo / REVIEWS_DIR).resolve()
    folder = (repo / str(meta.get("review") or "")).resolve()
    return folder if meta.get("review") and folder.parent == root and (folder / "doc.json").is_file() else None


def page_of(repo: Path, folder: Path) -> str | None:
    """The page a review folder reviews, from its doc.json (which only the CLI writes), when it is a file inside the
    repository. WHY not the response's `source`: that comes from the upload, and a path there could name any file."""
    page = (read_json(folder / "doc.json") or {}).get("page")
    path = (repo / str(page or "")).resolve()
    return page if isinstance(page, str) and page and path.is_relative_to(repo.resolve()) and path.is_file() else None


def verdict_action(verdict: str, status: Any) -> str:
    """What a quick verdict asks of the agent, by the finding's apply status: an accepted change stays or goes in, a
    rejected one comes out or stays out."""
    if status == "applied":
        return "applied → keep it" if verdict == "accept" else "applied → restore its original text"
    if verdict == "reject":
        return "→ leave the text as it is"
    return "not applied → apply its replacement" if str(status or "").startswith("not applied") else "→ do what it proposes"


def read_back(repo: Path, path: Path) -> tuple[int, list[str]]:
    """The human's replies, verdicts, new comments and edits in a review response (the file, or the review folder
    holding `response.json`), with their page lines. The first read of an edit review's response that holds any records
    the page as it is then in the review folder (`base.md`): the next revision is checked against it
    (vae_edit.next_revision). WHY an applied finding without a reply counts as accepted: the page dashes it as applied,
    so silence keeps what the human saw done; answering it, or ❌, is how the human objects."""
    if path.is_dir():
        path = path / RESPONSE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return 2, [f"UNKNOWN[comments] BC cannot read {path}: {e}"]
    meta, comments = (data.get("vae"), data.get("comments")) if isinstance(data, dict) else (None, None)
    if not isinstance(meta, dict) or not isinstance(comments, list) or meta.get("kind") not in ("edit", "research"):
        return 2, [f"UNKNOWN[comments] BC {path} is not a defuss-vae comments download (`vae`, `comments`)"]
    findings = meta.get("findings") if isinstance(meta.get("findings"), dict) else {}  # the page's upload: input, not trusted
    snapshot = repo / str(meta.get("snapshot") or meta.get("source") or "")
    source = snapshot.read_text("utf-8", errors="replace") if snapshot.is_file() else ""
    by_id = {c["id"]: c for c in comments if isinstance(c, dict) and isinstance(c.get("id"), str)}

    def where(c: dict[str, Any]) -> str:
        if c["id"] in findings:
            f = findings[c["id"]]
            return f"{meta.get('source')}:{f.get('line')} {c['id']} {json.dumps(str(f.get('quote', ''))[:60], ensure_ascii=False)}"
        text = str((c.get("anchor") or {}).get("text") or "")
        return f"{meta.get('source')}:{locate(source, text) or '?'} {c['id']} {json.dumps(text[:60], ensure_ascii=False)}"
    out: list[str] = []
    replied, new, verdicts = set(), 0, {}
    for c in by_id.values():
        if c.get("parent"):
            r = root_of(by_id, c)
            replied.add(r["id"])
            if isinstance(c.get("verdict"), str) and c["verdict"] in VERDICTS and r["id"] in findings:
                verdicts[r["id"]] = c["verdict"]  # the page keeps one per finding; should there be more, the last stands
                continue
            out.append(f"REPLY {where(r)} by {c.get('author', '?')}: {one_line(c.get('body'))}")
        elif c["id"] not in findings:
            new += 1
            out.append(f"NEW {where(c)} by {c.get('author', '?')}: {one_line(c.get('body'))}")
    status = lambda fid: (findings[fid] if isinstance(findings[fid], dict) else {}).get("status")
    out += [f"{VERDICTS[verdicts[fid]]} {where({'id': fid})} {verdict_action(verdicts[fid], status(fid))}" for fid in findings if fid in verdicts]
    quiet = [fid for fid in findings if status(fid) == "applied" and fid not in replied]
    doc = data.get("document") if isinstance(data.get("document"), dict) else {}
    baseline, edited = doc.get("baseline"), doc.get("edited")
    hunks = edit_hunks(source, str(meta.get("source")), baseline, edited) \
        if isinstance(baseline, str) and isinstance(edited, str) and edited != baseline else []
    edits = sum(1 for h in hunks if h.startswith("@@"))
    if hunks:
        out += [(f"EDITS {meta.get('source')}: {edits} hunk(s) of the human's text changes, "
                 "a diff of the review page's Markdown (`-` before, `+` after)"), *hunks]
    head = (f"COMMENTS {meta.get('run') or meta.get('review')} ({meta['kind']} of {meta.get('source')}): agent={len(findings)}, "
            f"replied={len(replied & set(findings))}, new={new}, replies={sum(1 for c in by_id.values() if c.get('parent'))}, "
            f"edits={edits}")
    folder = review_folder_of(repo, meta) if meta["kind"] == "edit" else None
    page = page_of(repo, folder) if folder else None
    if folder and page and out and not (folder / BASE).exists():
        write_private(folder / BASE, (repo / page).read_bytes())  # once: a second read mid-revision keeps the first base
    info = [f"ACCEPTED {len(quiet)} applied finding(s) without a reply → keep them: {', '.join(quiet)}"] if quiet else []
    if not out:
        tail = "∅: the human added nothing"
    elif folder and page and (folder / BASE).is_file():
        rel = folder.relative_to(repo.resolve())
        tail = (f"DO: revise {page}: change ONLY what an ACCEPT, REJECT, REPLY, NEW or EDITS line or the human's chat "
                f"proposes or accepts (EDITS in the page's own Markdown style); every other byte stays as in {rel}/{BASE}. Write notes "
                "`[{on, quote, body}]` (references/EDIT.md, Next revision): one per change, answer or open question. `vae.py edit review "
                f"{rel} --notes <file> --check` must print VERIFIED[revision]; then offer the human the next review "
                "revision (the same command without --check), which carries the open questions, NOT ask them in chat")
    else:
        tail = ("DO: act on EVERY ACCEPT, REJECT, REPLY and NEW line, and carry EVERY EDITS hunk into the page in its own Markdown style: "
                "change the page where the human decided, answer questions, and ask where a reply leaves a fact open")
    return 0, [head, *out, *info, tail]


# -- the review server: one per repo, on 127.0.0.1, rooted at .vae/doc-reviews/ -------------------------------


def root_id(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


class ReviewHandler(SimpleHTTPRequestHandler):
    """Static files of the review folders under `/<token>/`, plus `POST /<token>/<folder>/response.json`. WHY the
    token: other users of the machine reach 127.0.0.1 too, and only this user can read the token (the mode-600 state
    file), so the link works as a capability for reading and sending. WHY the Host check on every request: a site whose
    name a DNS rebinding points at 127.0.0.1 reaches this port with its own name as Host. WHY Origin plus a JSON
    content type on POST: a cross-site form cannot send JSON, a cross-site fetch of JSON needs a preflight this server
    never grants, and a same-site request carries this server's own origin."""
    server_version = "vae-review"
    sys_version = ""
    server: ReviewServer

    def host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def end_headers(self) -> None:
        self.send_header("X-Vae-Root", root_id(Path(self.directory)))
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def inside(self) -> str | None:
        """The request path below `/<token>`, still URL-encoded, or None without the token."""
        path = urlsplit(self.path).path
        prefix = f"/{self.server.token}"
        return path[len(prefix):] if path == prefix or path.startswith(prefix + "/") else None

    def translate_path(self, path: str) -> str:
        rest = self.inside()
        return super().translate_path(rest or "/") if rest is not None else str(Path(self.directory) / ".none")

    def send_head(self) -> Any:
        if not self.host_ok():
            self.send_error(403, "unknown Host")
            return None
        rest = self.inside()
        if rest is None or any(p.startswith(".") for p in unquote(rest).split("/")):
            self.send_error(404)
            return None
        return super().send_head()

    def reply(self, code: int, message: str) -> None:
        body = json.dumps({"ok": code == 200, "message": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if not self.host_ok() or self.headers.get("Origin") != f"http://{self.headers.get('Host')}":
            return self.reply(403, "only the review page itself may send")
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, "Content-Type must be application/json")
        parts = unquote(self.inside() or "").strip("/").split("/")
        folder = Path(self.directory) / parts[0]
        if len(parts) != 2 or parts[1] != RESPONSE or parts[0] in ("", "..") or parts[0].startswith(".") \
                or not (folder / "doc.json").is_file():
            return self.reply(404, "no review folder at this path")
        try:
            size = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self.reply(411, "Content-Length required")
        if not 0 < size <= MAX_RESPONSE:
            return self.reply(413, f"the response must hold 1 to {MAX_RESPONSE} bytes")
        body = self.rfile.read(size)
        try:
            data = json.loads(body)
        except ValueError:
            return self.reply(400, "not JSON")
        if not (isinstance(data, dict) and isinstance(data.get("vae"), dict) and data["vae"].get("kind") == "edit"
                and isinstance(data.get("comments"), list)):
            return self.reply(422, "not a review response (`vae.kind` edit, `comments`)")
        write_private(folder / RESPONSE, body)
        return self.reply(200, f"saved {parts[0]}/{RESPONSE}")

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"{now_iso()} INFO {self.address_string()} {format % args}\n")

    def log_error(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"{now_iso()} WARN {self.address_string()} {format % args}\n")


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    root: Path
    token: str

    def server_bind(self) -> None:
        # WHY not HTTPServer.server_bind: it resolves the host's name (socket.getfqdn), and that reverse DNS lookup took
        # more than 30 s on a CI runner (tests/ARCH.md), past the 5 s `ensure_server` waits
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    def service_actions(self) -> None:
        # WHY: a repo deleted or moved away leaves no state file to stop this server by; it ends itself instead
        if not self.root.is_dir():
            raise SystemExit(0)


def serve(root: Path, state: Path, port: int, token: str) -> None:
    """The detached server's main: bind 127.0.0.1 on `port` (else a free one), record pid, port and token, serve
    until SIGTERM or until its root is gone."""
    handler = partial(ReviewHandler, directory=str(root))
    try:
        httpd = ReviewServer(("127.0.0.1", port), handler)
    except OSError:
        httpd = ReviewServer(("127.0.0.1", 0), handler)
    httpd.root, httpd.token = root, token

    def stop(*_: Any) -> None:
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    write_private(state, json.dumps({"pid": os.getpid(), "port": httpd.server_address[1], "token": token, "root": str(root)}) + "\n")
    sys.stderr.write(f"{now_iso()} INFO serving {root} on http://127.0.0.1:{httpd.server_address[1]}/\n")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def answers(port: Any, root: Path) -> bool:
    """Whether the review server of `root` answers on `port`."""
    if not isinstance(port, int) or not 0 < port < 65536:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
            headers = r.headers
    except urllib.error.HTTPError as e:  # `/` lies outside the token: a 404 that still names its root
        headers = e.headers
        e.close()
    except OSError:
        return False
    return headers.get("X-Vae-Root") == root_id(root)


def ensure_server(repo: Path) -> str:
    """The base URL of the repo's review server; starts it when it does not answer, on its previous port while that
    is free, so earlier links keep working. OSError when it does not come up within 5 s (its log names why)."""
    root = runtime_dir(repo, REVIEWS_DIR)
    state_path = runtime_dir(repo, SERVER_DIR) / "review-server.json"
    state = read_json(state_path) or {}
    if answers(state.get("port"), root) and isinstance(state.get("token"), str):
        return f"http://127.0.0.1:{state['port']}/{state['token']}/"
    port = state.get("port") if isinstance(state.get("port"), int) else 0
    token = state["token"] if isinstance(state.get("token"), str) else secrets.token_urlsafe(16)
    state_path.unlink(missing_ok=True)
    log_path = state_path.with_name("review-server.log")
    with open(log_path, "ab") as log:
        # the launcher forks the server off and exits at once (see __main__), so no child is left to reap; the token
        # travels in the environment, which other users cannot read, unlike the command line
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "serve", str(root), str(state_path), str(port)],
                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=False, timeout=10,
                       env={**os.environ, "VAE_REVIEW_TOKEN": token})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = read_json(state_path) or {}
        if answers(state.get("port"), root):
            return f"http://127.0.0.1:{state['port']}/{token}/"
        time.sleep(0.05)
    raise OSError(f"the review server did not start; see {log_path.relative_to(repo)}")


def stop_server(repo: Path) -> bool:
    """Stop the repo's review server; True when one was running. The state file stays, so a restart takes the same
    port."""
    state_path = repo / SERVER_DIR / "review-server.json"
    state = read_json(state_path) or {}
    running = answers(state.get("port"), repo / REVIEWS_DIR) and isinstance(state.get("pid"), int)
    if running:
        try:
            os.kill(state["pid"], signal.SIGTERM)
        except OSError:
            running = False
        deadline = time.monotonic() + 3
        while running and time.monotonic() < deadline and answers(state.get("port"), repo / REVIEWS_DIR):
            time.sleep(0.05)
    return running


def link(repo: Path, page: Path) -> list[str]:
    """The lines that hand an edit review's page to the human: its URL on the review server, else the file."""
    folder = page.parent.relative_to(repo)
    try:
        url = f"{ensure_server(repo)}{quote(page.parent.name)}/"
    except OSError as e:
        return [f"PAGE: {folder}/{page.name} (open the file; \"Send to agent\" needs the server)",
                f"UNKNOWN[review-server] BC {e}"]
    return [f"PAGE: {url} (the human's review page: edit, comment, \"Send to agent\")",
            f"RESPONSE: {folder}/{RESPONSE} (read it with the CLI's `comments {folder}`)"]


if __name__ == "__main__":  # vae_annotate.py serve <root> <state file> <port>
    # WHY fork + setsid: the server outlives the CLI call and the agent's shell, with no parent left to reap it
    if os.fork():
        os._exit(0)
    os.setsid()
    serve(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), os.environ.pop("VAE_REVIEW_TOKEN"))
