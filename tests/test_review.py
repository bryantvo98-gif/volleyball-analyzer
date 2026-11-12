import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from vbtool import review


@pytest.fixture
def server(tmp_path):
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "meta.json").write_text(json.dumps({"duration_s": 60}))
    (mdir / "video.mp4").write_bytes(bytes(range(256)) * 40)
    (mdir / "points.json").write_text(json.dumps([
        {"index": 1, "start_s": 30.0, "end_s": 40.0},
        {"index": 2, "start_s": 5.0, "end_s": 12.0},
    ]))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), review.make_handler(mdir))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mdir, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_page_and_points(server):
    mdir, url = server
    assert b"Point review" in urllib.request.urlopen(url + "/").read()
    pts = json.loads(urllib.request.urlopen(url + "/api/points").read())
    assert len(pts) == 2


def test_save_renumbers_and_keeps_original(server):
    mdir, url = server
    original = (mdir / "points.json").read_text()
    body = json.dumps([
        {"index": 1, "start_s": 30.0, "end_s": 40.0, "extra": {"winner": "near"}},
        {"index": 2, "start_s": 5.0, "end_s": 12.0},
    ]).encode()
    req = urllib.request.Request(url + "/api/points", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    saved = json.loads(urllib.request.urlopen(req).read())
    assert [p["start_s"] for p in saved] == [5.0, 30.0]
    assert [p["index"] for p in saved] == [1, 2]
    assert (mdir / "points.auto.json").read_text() == original


def test_video_range_requests(server):
    mdir, url = server
    req = urllib.request.Request(url + "/video", headers={"Range": "bytes=100-199"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 206
    assert resp.headers["Content-Range"] == f"bytes 100-199/{256 * 40}"
    assert resp.read() == (bytes(range(256)) * 40)[100:200]
