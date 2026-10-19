"""Read web pages through CloakBrowser, the patched Chromium the site e2e installs, over the DevTools protocol.

WHY a browser at all: npm, GitHub and many publishers answer plain fetches and stock headless Chrome with 403/503 bot
challenges (VERIFIED: tests/ARCH.md); CloakBrowser passes them. WHY a hand-written WebSocket client: the DevTools
protocol runs JavaScript only over WebSocket, the stdlib has no WebSocket client, and the client side of RFC 6455 that
CDP needs (handshake, masked text frames, fragments, ping) is about 80 lines.

Binary: `$CLOAKBROWSER_BINARY_PATH`, else the newest `chromium-*` build under `${CLOAKBROWSER_CACHE_DIR:-~/.cloakbrowser}`
(installed by `tests/site/install-cloakbrowser.sh` in this repository, or `bunx cloakbrowser install` anywhere).
Launch flags follow the `cloakbrowser` npm wrapper's stealth defaults (`--fingerprint=<seed>`, `--fingerprint-platform`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from vae_net import request_json

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT_JS = "JSON.stringify({title: document.title, url: location.href, text: document.body ? document.body.innerText : ''})"


class CloakError(RuntimeError):
    pass


def binary() -> Path | None:
    """The CloakBrowser executable, or None when it is not installed."""
    explicit = os.environ.get("CLOAKBROWSER_BINARY_PATH")
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    cache = Path(os.environ.get("CLOAKBROWSER_CACHE_DIR") or Path.home() / ".cloakbrowser")
    exe = "Chromium.app/Contents/MacOS/Chromium" if sys.platform == "darwin" else "chrome"
    builds = sorted((p for p in cache.glob("chromium-*") if (p / exe).is_file()),
                    key=lambda p: [int(n) for n in p.name.split("-", 1)[1].split(".") if n.isdigit()])
    return builds[-1] / exe if builds else None


class WebSocket:
    """The client half of RFC 6455 over a blocking socket: text messages out (masked), messages in (reassembled)."""

    def __init__(self, url: str, timeout: float):
        if not url.startswith("ws://"):
            raise CloakError(f"only ws:// is supported: {url}")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CloakError("the browser closed the connection during the WebSocket handshake")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        fields = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if " 101 " not in lines[0] + " " or fields.get("sec-websocket-accept") != accept:
            raise CloakError(f"WebSocket handshake refused: {lines[0]}")

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise CloakError("the browser closed the WebSocket")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                         bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                         bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode("utf-8"))

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            n = struct.unpack(">H", self._read(2))[0] if n == 126 else struct.unpack(">Q", self._read(8))[0] if n == 127 else n
            mask = self._read(4) if b1 & 0x80 else b""
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise CloakError("the browser closed the WebSocket")
            if opcode == 0x9:
                self._frame(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                parts.append(payload)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        self.sock.close()


class Browser:
    """One headless CloakBrowser with a throwaway profile; `close()` kills it and deletes the profile."""

    def __init__(self, exe: Path, timeout: float = 30.0):
        self.timeout = timeout
        self.profile = tempfile.mkdtemp(prefix="vae-cloak-")
        platform = "macos" if sys.platform == "darwin" else "windows"
        self.proc = subprocess.Popen(
            [str(exe), "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--no-sandbox",
             f"--fingerprint={random.randint(10000, 99999)}", f"--fingerprint-platform={platform}", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        # WHY the DevToolsActivePort file: with port 0 the browser picks a free port and writes it there.
        port_file, deadline = Path(self.profile) / "DevToolsActivePort", time.time() + timeout
        while not (port_file.is_file() and port_file.read_text().strip()):
            if self.proc.poll() is not None or time.time() > deadline:
                self.close()
                raise CloakError(f"CloakBrowser did not open its DevTools port (exit {self.proc.poll()})")
            time.sleep(0.05)
        self.port = int(port_file.read_text().split()[0])

    def page(self) -> Page:
        targets = request_json(f"http://127.0.0.1:{self.port}/json/list", timeout=self.timeout)
        target = next((t for t in targets if t.get("type") == "page"), None)
        if not target:
            raise CloakError("the browser has no page target")
        return Page(WebSocket(target["webSocketDebuggerUrl"], self.timeout), self.timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    """One tab: commands with ids, events queued until someone waits for them."""

    def __init__(self, ws: WebSocket, timeout: float):
        self.ws, self.timeout, self.next_id = ws, timeout, 0
        self.events: list[dict[str, Any]] = []

    def _message(self, deadline: float) -> dict[str, Any]:
        self.ws.sock.settimeout(max(0.05, deadline - time.time()))
        try:
            return json.loads(self.ws.recv())
        except socket.timeout:
            raise CloakError("the page did not answer in time") from None

    def send(self, method: str, **params: Any) -> dict[str, Any]:
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        deadline = time.time() + self.timeout
        while True:
            msg = self._message(deadline)
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise CloakError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait(self, event: str, deadline: float) -> dict[str, Any] | None:
        """The first queued or arriving `event`, or None at the deadline."""
        while True:
            for i, msg in enumerate(self.events):
                if msg["method"] == event:
                    return self.events.pop(i)
            if time.time() >= deadline:
                return None
            try:
                msg = self._message(deadline)
            except CloakError:
                return None
            if "method" in msg:
                self.events.append(msg)


def fetch(url: str, want_screenshot: bool = False, timeout: float = 30.0, exe: Path | None = None) -> dict[str, Any]:
    """{status, url, title, text[, screenshot (base64 PNG)]} of one page, read after its load event or the timeout."""
    exe = exe or binary()
    if exe is None:
        raise CloakError("CloakBrowser is not installed (set CLOAKBROWSER_BINARY_PATH or run `bunx cloakbrowser install`)")
    browser = Browser(exe, timeout)
    try:
        page = browser.page()
        try:
            page.send("Page.enable")
            page.send("Network.enable")
            nav = page.send("Page.navigate", url=url)
            if nav.get("errorText"):
                raise CloakError(f"navigation failed: {nav['errorText']}")
            deadline, status = time.time() + timeout, None
            while status is None:
                ev = page.wait("Network.responseReceived", deadline)
                if ev is None:
                    break
                p = ev["params"]
                if p.get("type") == "Document" and p.get("loaderId") == nav.get("loaderId"):
                    status = p["response"].get("status")
            page.wait("Page.loadEventFired", deadline)
            value = page.send("Runtime.evaluate", expression=TEXT_JS, returnByValue=True)["result"].get("value")
            out = {"status": status, **json.loads(value or "{}")}
            if want_screenshot:
                out["screenshot"] = page.send("Page.captureScreenshot", format="png")["data"]
            return out
        finally:
            page.ws.close()
    finally:
        browser.close()
