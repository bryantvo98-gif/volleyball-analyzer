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
RALLIES = [(5, 15, "near"), (27, 37, "far"), (48, 56, "near")]
END_WHISTLES = [15.6, 37.6, 56.6]
SERVE_WHISTLES = [2.0, 24.0, 45.0]
WHISTLES = sorted(END_WHISTLES + SERVE_WHISTLES)
SQUEAKS = [8.0, 30.0, 52.0]   # very short 3 kHz blips, like shoe squeaks
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
        m = (t >= q) & (t < q + 0.1)
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
    for (a, *_), w in zip(found, WHISTLES):
        assert abs(a - w) < 0.1


def test_whistle_mode_points(match):
    points, debug = rallies.detect_points(match, mode="whistle")
    assert len(debug["whistles"]) == 6          # blips too short to register
    assert len(points) == 3
    for pt, s, e in zip(points, SERVE_WHISTLES, END_WHISTLES):
        assert abs(pt.start_s - s) < 0.1
        assert abs(pt.end_s - e) < 0.1
        assert abs(pt.clip_start_s - (s - 2)) < 0.1
        assert abs(pt.clip_end_s - (e + 2)) < 0.1


def test_missed_end_whistle_does_not_shift_later_points():
    """One end whistle is never heard. The point is kept with an estimated end,
    and the points after it still pair serve to end correctly."""
    p = rallies.RallyParams()
    ws = [
        (10.0, 11.0, 1.0), (20.0, 20.8, 0.8),    # point 1
        (30.0, 31.0, 1.0),                       # point 2 serve; end whistle missed
        (50.0, 51.0, 1.0), (58.0, 58.8, 0.8),    # point 3
        (68.0, 69.0, 1.0), (79.0, 79.6, 0.6),    # point 4
    ]
    got = rallies.decode_whistles(ws, p)
    assert [(a, b) for a, b, _ in got] == [(10.0, 20.0), (30.0, 44.0), (50.0, 58.0), (68.0, 79.0)]
    assert [est for _, _, est in got] == [False, True, False, False]


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
    from vbtool.media import probe
    review = match / "all_points.mp4"
    assert review.exists()
    clip_total = sum(probe(match / pt["clip"])["duration_s"] for pt in saved)
    assert abs(probe(review)["duration_s"] - clip_total) < 1.0


def test_server_guess_with_bigger_near_players(tmp_path):
    """Near players look bigger on camera; far serves must still be found."""
    mdir = tmp_path / "matches" / "perspective"
    write_match(mdir, near_scale=3)
    points, _ = rallies.detect_points(mdir)
    assert [p.server_guess for p in points] == [s for _, _, s in RALLIES]


def test_muffled_end_whistle_and_stray_sound():
    """A short end whistle (drowned out by cheering) still ends the point, and a
    stray short sound mid-rally doesn't."""
    p = rallies.RallyParams()
    ws = [
        (10.0, 11.0, 1.0),     # serve whistle
        (14.0, 14.2, 0.0),     # stray sound 4 s in
        (22.0, 22.35, 0.0),    # muffled end whistle (a bit longer)
        (31.0, 32.0, 1.0),     # next serve whistle
        (42.0, 43.0, 1.0),     # end whistle
    ]
    assert rallies.pair_whistles(ws, p) == [(10.0, 22.0), (31.0, 42.0)]


def test_timeout_whistle_is_ignored():
    p = rallies.RallyParams()
    ws = [
        (10.0, 11.0, 1.0), (20.0, 20.8, 0.8),    # point
        (25.0, 26.5, 1.5),                       # timeout whistle, then 60 s break
        (86.0, 87.0, 1.0), (95.0, 95.8, 0.8),    # point
    ]
    assert rallies.pair_whistles(ws, p) == [(10.0, 20.0), (86.0, 95.0)]


def test_rerun_does_not_overwrite_review(match):
    rallies.run("synthetic", root=match.parent, clips=False)
    pts = json.loads((match / "points.json").read_text())
    pts[0]["extra"] = {"winner": "near"}
    (match / "points.json").write_text(json.dumps(pts))
    with pytest.raises(SystemExit):
        rallies.run("synthetic", root=match.parent, clips=False)
    assert json.loads((match / "points.json").read_text())[0]["extra"] == {"winner": "near"}
    rallies.run("synthetic", root=match.parent, clips=False, force=True)
    assert (match / "points.reviewed-backup.json").exists()
