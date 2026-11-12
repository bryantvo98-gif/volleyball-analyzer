"""End-to-end test of rally detection on a synthetic match.

The fake match is 60 s at 5 frames per second. Three rallies have moving
blocks on court; the serve happens on the near side (bottom), then far side,
then near side again. A 3 kHz whistle sounds a few seconds before each serve and just after each
rally ends. Short squeak-like blips happen during rallies.
"""
import json
import math
import shutil
import struct
import subprocess
import wave

import numpy as np
import pytest
from PIL import Image

from vbtool import audio, rallies

RATE = 5
DURATION = 60
RALLIES = [(5, 15, "near"), (25, 37, "far"), (45, 55, "near")]
END_WHISTLES = [15.6, 37.6, 55.6]
SERVE_WHISTLES = [2.0, 22.0, 42.0]
WHISTLES = sorted(END_WHISTLES + SERVE_WHISTLES)
SQUEAKS = [8.0, 30.0, 48.0]   # short 3 kHz blips, like shoe squeaks
W, H = 320, 180


def frame_at(t, rng, near_scale=1):
    img = np.full((H, W), 90, dtype=np.uint8)
    img += rng.integers(0, 3, size=img.shape, dtype=np.uint8)  # sensor noise
    for a, b, server in RALLIES:
        if a <= t < b:
            serving = t < a + 1.5
            phase = (t - a) * 7.0
            # Blocks that move fast: on the server's side only during the serve
            halves = [server] if serving else ["near", "far"]
            for side in halves:
                size = 20 * near_scale if side == "near" else 20
                y0 = H // 2 + 10 if side == "near" else 20
                x = int((math.sin(phase) * 0.5 + 0.5) * (W - size))
                img[y0:y0 + size, x:x + size] = 240
    return img


def write_match(mdir, near_scale=1):
    rng = np.random.default_rng(0)
    frames = mdir / "frames"
    frames.mkdir(parents=True)
    n = DURATION * RATE
    for i in range(n):
        Image.fromarray(frame_at(i / RATE, rng, near_scale)).save(frames / f"{i + 1:06d}.jpg", quality=90)
    sr = 16000
    t = np.arange(DURATION * sr) / sr
    x = rng.normal(0, 0.01, size=t.shape)  # crowd hiss
    for w in WHISTLES:
        m = (t >= w) & (t < w + 0.5)
        x[m] += 0.5 * np.sin(2 * np.pi * 3000 * t[m])
    for q in SQUEAKS:
        m = (t >= q) & (t < q + 0.2)
        x[m] += 0.5 * np.sin(2 * np.pi * 3000 * t[m])
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(mdir / "audio.wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(pcm.tobytes())
    (mdir / "meta.json").write_text(json.dumps({
        "match_id": mdir.name, "source": "synthetic", "title": "synthetic",
        "duration_s": DURATION, "fps": RATE, "width": W, "height": H,
        "frame_rate_sampled": RATE, "frame_width": W, "frame_count": n,
    }))


@pytest.fixture
def match(tmp_path):
    mdir = tmp_path / "matches" / "synthetic"
    write_match(mdir)
    return mdir


def test_whistles_found(match):
    found = rallies.clean_whistles(audio.detect_whistles(match / "audio.wav"), rallies.RallyParams())
    assert len(found) == len(WHISTLES)
    for (a, _), w in zip(found, WHISTLES):
        assert abs(a - w) < 0.1


def test_whistle_mode_points(match):
    points, debug = rallies.detect_points(match, mode="whistle")
    assert len(debug["whistles"]) == 9          # includes the squeaks
    assert len(debug["whistles_kept"]) == 6     # squeaks dropped
    assert len(points) == 3
    for pt, s, e in zip(points, SERVE_WHISTLES, END_WHISTLES):
        assert abs(pt.start_s - s) < 0.1
        assert abs(pt.end_s - e) < 0.1
        assert abs(pt.clip_start_s - (s - 2)) < 0.1
        assert abs(pt.clip_end_s - (e + 2)) < 0.1


def test_pairing_recovers_after_missed_whistle():
    p = rallies.RallyParams()
    # 10 serve, 18 end, 30 serve, [end whistle missed], 95 serve, 104 end
    ws = [(10, 11), (18, 19), (30, 31), (95, 96), (104, 105)]
    assert rallies.pair_whistles(ws, p) == [(10, 18), (95, 104)]


def test_points_detected(match):
    points, _ = rallies.detect_points(match)
    assert len(points) == 3
    for pt, (a, b, server) in zip(points, RALLIES):
        assert abs(pt.start_s - a) <= 1.0
        assert pt.ended_by_whistle
        assert abs(pt.end_s - (b + 0.6)) <= 0.5
        assert pt.server_guess == server
    # Rally scoring: winner of a point is the next point's server
    assert [p.winner_guess for p in points] == ["far", "near", "unknown"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_run_writes_points_and_clips(match):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(RATE),
         "-i", str(match / "frames" / "%06d.jpg"), "-i", str(match / "audio.wav"),
         "-pix_fmt", "yuv420p", "-shortest", str(match / "video.mp4")],
        check=True,
    )
    rallies.run("synthetic", root=match.parent, mode="whistle")
    saved = json.loads((match / "points.json").read_text())
    assert len(saved) == 3
    for pt in saved:
        assert (match / pt["clip"]).stat().st_size > 0


def test_server_guess_with_bigger_near_players(tmp_path):
    """Near players look bigger on camera; far serves must still be found."""
    mdir = tmp_path / "matches" / "perspective"
    write_match(mdir, near_scale=3)
    points, _ = rallies.detect_points(mdir)
    assert [p.server_guess for p in points] == [s for _, _, s in RALLIES]
