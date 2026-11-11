import json
import shutil
import subprocess

import pytest

from vbtool import media

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_youtube_id():
    assert media.youtube_id("https://www.youtube.com/watch?v=g5GqP4kEs_0") == "g5GqP4kEs_0"
    assert media.youtube_id("https://youtu.be/g5GqP4kEs_0?t=30") == "g5GqP4kEs_0"
    assert media.youtube_id("not a url") is None


@needs_ffmpeg
def test_ingest_local_file(tmp_path):
    src = tmp_path / "sample match.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=3000:duration=4",
         "-shortest", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    meta = media.ingest(str(src), root=tmp_path / "matches", frame_rate=5, frame_width=640)
    mdir = tmp_path / "matches" / "sample-match"
    assert meta.match_id == "sample-match"
    assert (mdir / "audio.wav").stat().st_size > 0
    assert 19 <= meta.frame_count <= 21
    assert meta.width == 1280 and meta.height == 720
    saved = json.loads((mdir / "meta.json").read_text())
    assert saved["frame_count"] == meta.frame_count
