"""vae_cloak: the WebSocket client against a real RFC 6455 server on 127.0.0.1, binary discovery on real folders, and a
real CloakBrowser page read when the browser is installed (skipped otherwise, as in CI)."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from research_kit import HttpFixture
from vae_cloak import WS_GUID, CloakError, WebSocket, binary, fetch


def frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    n = len(payload)
    size = bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    return bytes([(0x80 if fin else 0) | opcode]) + size + payload


class WsServer:
    """Accepts one client, checks its handshake, then follows `script(conn)`."""

    def __init__(self, script):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port, self.seen = self.sock.getsockname()[1], {}
        threading.Thread(target=self.serve, args=(script,), daemon=True).start()

    def serve(self, script):
        conn, _ = self.sock.accept()
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        lines = head.decode().split("\r\n")
        fields = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
        accept = base64.b64encode(hashlib.sha1((fields["sec-websocket-key"] + WS_GUID).encode()).digest()).decode()
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        self.seen["path"] = lines[0].split()[1]
        script(conn, self.seen)
        conn.close()

    def read_frame(self, conn) -> tuple[int, bytes, bool]:
        def take(n):
            buf = b""
            while len(buf) < n:
                buf += conn.recv(n - len(buf))
            return buf
        b0, b1 = take(2)
        n = b1 & 0x7F
        n = struct.unpack(">H", take(2))[0] if n == 126 else struct.unpack(">Q", take(8))[0] if n == 127 else n
        mask = take(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(take(n)))
        return b0 & 0x0F, data, bool(b1 & 0x80)


class WebSocketTests(unittest.TestCase):
    def test_handshake_masking_fragments_ping_and_close(self):
        big = ("x" * 70000).encode()

        def script(conn, seen):
            op, data, masked = srv.read_frame(conn)
            seen["first"] = (op, data, masked)
            conn.sendall(frame(0x9, b"hb"))  # a ping the client must answer
            seen["pong"] = srv.read_frame(conn)[:2]
            conn.sendall(frame(0x1, big[:30000], fin=False) + frame(0x0, big[30000:]))
            op, data, _ = srv.read_frame(conn)
            conn.sendall(frame(0x1, data))  # echo the 200-byte message (16-bit length form)
            conn.sendall(frame(0x8, b""))

        srv = WsServer(script)
        ws = WebSocket(f"ws://127.0.0.1:{srv.port}/devtools/page/1", timeout=5)
        ws.send("hello")
        self.assertEqual(ws.recv(), big.decode(), "fragments reassembled after the ping")
        self.assertEqual(srv.seen["first"], (0x1, b"hello", True), "client frames are masked")
        self.assertEqual(srv.seen["pong"], (0xA, b"hb"))
        ws.send("y" * 200)
        self.assertEqual(ws.recv(), "y" * 200)
        with self.assertRaisesRegex(CloakError, "closed"):
            ws.recv()
        self.assertEqual(srv.seen["path"], "/devtools/page/1")
        ws.close()

    def test_a_refused_handshake_and_other_schemes_fail(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)

        def refuse():
            conn, _ = s.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            conn.close()

        threading.Thread(target=refuse, daemon=True).start()
        with self.assertRaisesRegex(CloakError, "handshake refused: HTTP/1.1 403"):
            WebSocket(f"ws://127.0.0.1:{s.getsockname()[1]}/x", timeout=5)
        s.close()
        with self.assertRaisesRegex(CloakError, "only ws://"):
            WebSocket("wss://example.invalid/x", timeout=1)


class BinaryTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("CLOAKBROWSER_BINARY_PATH", "CLOAKBROWSER_CACHE_DIR")}
        self.td = tempfile.TemporaryDirectory()

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self.td.cleanup()

    def test_explicit_path_then_newest_cached_build(self):
        exe = "Chromium.app/Contents/MacOS/Chromium" if sys.platform == "darwin" else "chrome"
        for version in ("chromium-99.0.1", "chromium-145.0.7632.109.2", "chromium-145.0.10"):
            p = Path(self.td.name) / version / exe
            p.parent.mkdir(parents=True)
            p.write_text("")
        os.environ["CLOAKBROWSER_CACHE_DIR"] = self.td.name
        self.assertEqual(binary().relative_to(self.td.name).parts[0], "chromium-145.0.7632.109.2")
        os.environ["CLOAKBROWSER_BINARY_PATH"] = str(Path(self.td.name) / "missing")
        self.assertIsNone(binary(), "an explicit path that does not exist means not installed")
        with self.assertRaisesRegex(CloakError, "not installed"):
            fetch("http://127.0.0.1:9/")


@unittest.skipUnless(binary(), "CloakBrowser is not installed (make setup installs it; CI skips)")
class LiveTests(unittest.TestCase):
    def test_reads_a_local_page(self):
        page = b"<html><head><title>Fixture page</title></head><body><p>Hello from the fixture</p></body></html>"
        srv = HttpFixture(lambda m, p, h, b: (200, {"Content-Type": "text/html"}, page))
        self.addCleanup(srv.close)
        out = fetch(srv.url + "/page", want_screenshot=True, timeout=30)
        self.assertEqual((out["status"], out["title"]), (200, "Fixture page"))
        self.assertIn("Hello from the fixture", out["text"])
        self.assertTrue(base64.b64decode(out["screenshot"]).startswith(b"\x89PNG"))
