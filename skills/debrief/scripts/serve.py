"""Serve one week's debrief page on localhost and save the quiz results.

Usage: python3 serve.py <week folder> [--port 0]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 1024 * 1024


def make_server(week_dir, port: int = 0) -> ThreadingHTTPServer:
    week_dir = Path(week_dir).resolve()
    week = week_dir.name

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            # a DNS-rebinding page would send its own hostname here
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden")
            if self.path.split("?")[0] == "/results":
                try:
                    return self._send(200, (week_dir / "results.json").read_bytes(), "application/json")
                except OSError:
                    return self._send(404, b"not found")
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return self._send(404, b"not found")
            try:
                body = (week_dir / "index.html").read_bytes()
            except OSError:
                return self._send(404, b"not found")
            self._send(200, body, "text/html; charset=utf-8")

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"forbidden")
            if self.path.split("?")[0] != "/results":
                return self._send(404, b"not found")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, b"json only")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(400, b"bad length")
            if length < 0 or length > MAX_BODY:
                self.rfile.read(min(max(length, 0), 8 * MAX_BODY))  # drain so the client sees the 413
                return self._send(413, b"too large")
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                return self._send(400, b"bad json")
            if not isinstance(data, dict) or data.get("week") != week or not isinstance(data.get("answers"), dict):
                return self._send(400, b"bad payload")
            fd, tmp = tempfile.mkstemp(dir=week_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, week_dir / "results.json")
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                return self._send(500, b"write failed")
            self._send(200, b"ok")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("week_dir")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    server = make_server(args.week_dir, args.port)
    print("http://127.0.0.1:%d/" % server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
