"""Find points (rallies) in a match.

Default ("whistle" mode), based on how refereed matches flow:
    serve whistle -> serve -> rally -> end whistle -> walk back -> next serve whistle
1. Find referee whistles in the audio: a loud, narrow-band tone between about
   2 and 4.5 kHz lasting at least `min_whistle_s` seconds. Shorter blips are
   ignored because shoe squeaks look similar but are much shorter.
2. Whistles alternate serve / end. A point runs from a serve whistle to the
   next end whistle. If a "point" would be longer than `max_point_s` (usually a
   missed whistle or a timeout), the later whistle starts a fresh pairing.
3. The clip starts `clip_pad_before_s` before the serve whistle (so the serve
   is always in it) and ends `clip_pad_after_s` after the end whistle.

Who served and who won are left as "unknown" in whistle mode for you to set in
the review screen; reliable guesses need ball tracking (a later step).

"motion" mode is the older approach (stretches of high frame-to-frame change).
It is kept for videos without whistles, but on a busy gym video it confuses
players walking between points with rallies.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import audio, motion
from .media import DATA_DIR, match_dir, require_tool


@dataclass
class RallyParams:
    smooth_s: float = 1.0
    threshold_mix: float = 0.4     # 0 = quiet level, 1 = busy level
    min_rally_s: float = 3.0
    merge_gap_s: float = 1.5
    whistle_after_s: float = 3.0   # how long after motion stops a whistle still counts
    serve_window_s: float = 1.5
    clip_pad_before_s: float = 2.0
    clip_pad_after_s: float = 2.0
    net_y: float = 0.5
    # Whistle mode
    min_whistle_s: float = 0.45    # shoe squeaks are shorter than this
    merge_whistle_s: float = 2.0   # whistles closer than this count as one
    min_point_s: float = 2.0
    max_point_s: float = 40.0


@dataclass
class Point:
    index: int
    start_s: float
    end_s: float
    clip_start_s: float
    clip_end_s: float
    ended_by_whistle: bool
    server_guess: str          # "near" or "far"
    server_confidence: float   # 0..1
    winner_guess: str          # "near", "far" or "unknown"
    winner_confidence: float
    clip: str | None = None
    # Filled in during review (step 3)
    reviewed: bool = False
    end_action: str | None = None
    player: str | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)


def find_active_spans(m: np.ndarray, rate: float, p: RallyParams) -> list[tuple[float, float]]:
    s = motion.smooth(m, max(1, round(p.smooth_s * rate)))
    if len(s) == 0:
        return []
    quiet, busy = np.percentile(s, 30), np.percentile(s, 90)
    if busy - quiet < 1e-6:
        return []
    thr = quiet + p.threshold_mix * (busy - quiet)
    return audio.runs(s > thr, 1 / rate, p.merge_gap_s, p.min_rally_s)


def snap_to_whistle(end_s: float, whistles: list[tuple[float, float]],
                    next_start_s: float | None, p: RallyParams) -> tuple[float, bool]:
    for w0, _ in whistles:
        if end_s - 0.5 <= w0 <= end_s + p.whistle_after_s and (next_start_s is None or w0 < next_start_s):
            return max(end_s, w0), True
    return end_s, False


def normalize_sides(near: np.ndarray, far: np.ndarray, spans: list[tuple[float, float]],
                    rate: float) -> tuple[np.ndarray, np.ndarray]:
    """Put near and far motion on the same scale.

    Near-side players are closer to the camera, so they look bigger and
    produce more pixel change than far-side players doing the same thing.
    Without this, the near side "wins" every comparison. We divide each side
    by its own typical motion during rallies.
    """
    mask = np.zeros(len(near), dtype=bool)
    for a, b in spans:
        mask[int(a * rate):int(b * rate)] = True
    if not mask.any():
        mask[:] = True
    n_scale = float(np.median(near[mask])) or 1.0
    f_scale = float(np.median(far[mask])) or 1.0
    return near / n_scale, far / f_scale


def guess_server(near: np.ndarray, far: np.ndarray, start_s: float, rate: float,
                 p: RallyParams) -> tuple[str, float]:
    i0 = int(start_s * rate)
    i1 = max(i0 + 1, int((start_s + p.serve_window_s) * rate))
    n, f = float(near[i0:i1].sum()), float(far[i0:i1].sum())
    if n + f <= 0:
        return "near", 0.0
    side = "near" if n >= f else "far"
    conf = abs(n - f) / (n + f)
    return side, round(conf, 3)


def clean_whistles(whistles: list[tuple[float, float]], p: RallyParams) -> list[tuple[float, float]]:
    """Drop short blips (likely shoe squeaks) and merge whistles close together."""
    out: list[list[float]] = []
    for a, b in whistles:
        if b - a < p.min_whistle_s:
            continue
        if out and a - out[-1][0] < p.merge_whistle_s:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def pair_whistles(whistles: list[tuple[float, float]], p: RallyParams) -> list[tuple[float, float]]:
    """Pair alternating serve and end whistles into (serve_s, end_s) points."""
    points: list[tuple[float, float]] = []
    serve: float | None = None
    for a, _ in whistles:
        if serve is None:
            serve = a
            continue
        gap = a - serve
        if gap < p.min_point_s:
            continue
        if gap > p.max_point_s:
            serve = a          # lost track (missed whistle, timeout): restart here
            continue
        points.append((serve, a))
        serve = None
    return points


def detect_points_from_whistles(mdir: Path, p: RallyParams,
                                whistle_params: audio.WhistleParams | None = None) -> tuple[list[Point], dict]:
    meta = json.loads((mdir / "meta.json").read_text())
    duration = float(meta["duration_s"])
    raw = audio.detect_whistles(mdir / "audio.wav", whistle_params)
    kept = clean_whistles(raw, p)
    points = [
        Point(
            index=i + 1, start_s=round(a, 2), end_s=round(b, 2),
            clip_start_s=round(max(0.0, a - p.clip_pad_before_s), 2),
            clip_end_s=round(min(duration, b + p.clip_pad_after_s), 2),
            ended_by_whistle=True,
            server_guess="unknown", server_confidence=0.0,
            winner_guess="unknown", winner_confidence=0.0,
        )
        for i, (a, b) in enumerate(pair_whistles(kept, p))
    ]
    debug = {"mode": "whistle", "whistles": raw, "whistles_kept": kept}
    return points, debug


def detect_points(mdir: Path, p: RallyParams | None = None,
                  whistle_params: audio.WhistleParams | None = None,
                  mode: str = "motion") -> tuple[list[Point], dict]:
    p = p or RallyParams()
    if mode == "whistle":
        return detect_points_from_whistles(mdir, p, whistle_params)
    meta = json.loads((mdir / "meta.json").read_text())
    rate = float(meta["frame_rate_sampled"])
    duration = float(meta["duration_s"])

    sig = motion.motion_signal(mdir / "frames", net_y=p.net_y)
    wav = mdir / "audio.wav"
    whistles = audio.detect_whistles(wav, whistle_params) if wav.exists() else []
    spans = find_active_spans(sig["total"], rate, p)
    near_n, far_n = normalize_sides(sig["near"], sig["far"], spans, rate)

    points: list[Point] = []
    for i, (a, b) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else None
        end, by_whistle = snap_to_whistle(b, whistles, nxt, p)
        server, sconf = guess_server(near_n, far_n, a, rate, p)
        points.append(Point(
            index=i + 1, start_s=round(a, 2), end_s=round(end, 2),
            clip_start_s=round(max(0.0, a - p.clip_pad_before_s), 2),
            clip_end_s=round(min(duration, end + p.clip_pad_after_s), 2),
            ended_by_whistle=by_whistle,
            server_guess=server, server_confidence=sconf,
            winner_guess="unknown", winner_confidence=0.0,
        ))
    for cur, nxt in zip(points, points[1:]):
        cur.winner_guess = nxt.server_guess
        cur.winner_confidence = nxt.server_confidence

    debug = {
        "mode": "motion",
        "rate": rate,
        "whistles": whistles,
        "motion_total": sig["total"].round(3).tolist(),
        "motion_near": sig["near"].round(3).tolist(),
        "motion_far": sig["far"].round(3).tolist(),
    }
    return points, debug


def cut_clips(mdir: Path, points: list[Point]) -> None:
    require_tool("ffmpeg")
    out = mdir / "clips"
    out.mkdir(exist_ok=True)
    video = mdir / "video.mp4"
    for pt in points:
        name = f"point_{pt.index:03d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-ss", f"{pt.clip_start_s:.2f}", "-to", f"{pt.clip_end_s:.2f}",
             "-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             # 8-bit 4:2:0 H.264 High profile plays natively in QuickTime and
             # browsers; without this a 10-bit source gives clips Macs can't
             # play smoothly.
             "-pix_fmt", "yuv420p", "-profile:v", "high", "-tag:v", "avc1",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out / name)],
            check=True,
        )
        pt.clip = f"clips/{name}"


def run(match_id: str, root: Path = DATA_DIR, clips: bool = True,
        p: RallyParams | None = None, mode: str = "whistle") -> list[Point]:
    mdir = match_dir(match_id, root)
    if not (mdir / "meta.json").exists():
        raise SystemExit(f"No ingested match at {mdir}. Run `vb ingest` first.")
    points, debug = detect_points(mdir, p, mode=mode)
    if clips and points and (mdir / "video.mp4").exists():
        print(f"Cutting {len(points)} clips ...")
        cut_clips(mdir, points)
    (mdir / "points.json").write_text(json.dumps([asdict(x) for x in points], indent=2))
    (mdir / "rally_debug.json").write_text(json.dumps(debug))
    if mode == "whistle":
        print(f"Found {len(points)} points from {len(debug['whistles_kept'])} whistles "
              f"({len(debug['whistles']) - len(debug['whistles_kept'])} short sounds ignored).")
        for pt in points:
            print(f"  #{pt.index:>3}  serve whistle {fmt(pt.start_s)}  end whistle {fmt(pt.end_s)}  "
                  f"clip {fmt(pt.clip_start_s)}-{fmt(pt.clip_end_s)}")
    else:
        print(f"Found {len(points)} points and {len(debug['whistles'])} whistles.")
        for pt in points:
            print(f"  #{pt.index:>3}  {fmt(pt.start_s)}-{fmt(pt.end_s)}  "
                  f"server={pt.server_guess:<4} winner={pt.winner_guess:<7} "
                  f"({pt.winner_confidence:.2f}){'  whistle' if pt.ended_by_whistle else ''}")
    return points


def fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"
