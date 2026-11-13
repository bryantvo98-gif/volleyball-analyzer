"""Local review screen: `vb review <match_id>` opens it in your browser.

Watch each point in the full match video and fix what the tool got wrong:
not a point, merge with the next one, split in two, move the start or end,
add a missed point. Then record who won, the action that ended it and the
near-side player involved. Everything is saved to points.json as you go.

Runs only on your computer (http://127.0.0.1); nothing is uploaded.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .media import DATA_DIR, match_dir

HTML = Path(__file__).with_name("review.html")


def load_points(mdir: Path) -> list[dict]:
    return json.loads((mdir / "points.json").read_text())


def save_points(mdir: Path, points: list[dict]) -> None:
    backup = mdir / "points.auto.json"
    if not backup.exists():
        shutil.copyfile(mdir / "points.json", backup)   # keep the tool's original guesses
    points = sorted(points, key=lambda p: p["start_s"])
    for i, p in enumerate(points, 1):
        p["index"] = i
    tmp = mdir / "points.json.tmp"
    tmp.write_text(json.dumps(points, indent=2))
    tmp.replace(mdir / "points.json")


def make_handler(mdir: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # keep Terminal quiet
            pass

        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(HTML.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/points":
                return self._send(json.dumps(load_points(mdir)).encode(), "application/json")
            if self.path == "/api/meta":
                meta = json.loads((mdir / "meta.json").read_text())
                players = mdir / "players.json"
                meta["players"] = json.loads(players.read_text()) if players.exists() else []
                return self._send(json.dumps(meta).encode(), "application/json")
            if self.path.startswith("/video"):
                return self._video()
            self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

        def _video(self):
            # Browsers (Safari especially) need byte-range support to seek in a video.
            path = mdir / "video.mp4"
            size = path.stat().st_size
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:
                    start = max(0, size - int(m.group(2)))
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            length = end - start + 1
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = length
                try:
                    while remaining > 0:
                        chunk = f.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass   # the browser stopped reading (normal when seeking)

        def do_POST(self):
            if self.path != "/api/points":
                return self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            save_points(mdir, json.loads(body))
            self._send(json.dumps(load_points(mdir)).encode(), "application/json")

    return Handler


def serve(match_id: str, root: Path = DATA_DIR, port: int = 8765, open_browser: bool = True) -> None:
    mdir = match_dir(match_id, root)
    if not (mdir / "points.json").exists():
        raise SystemExit(f"No points found for {match_id}. Run `vb rallies {match_id}` first.")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(mdir))
    url = f"http://127.0.0.1:{port}/"
    print(f"Review screen running at {url}")
    print("Your changes save automatically. Press Control+C here when you're done.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
