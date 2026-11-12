"""Find points (rallies) in a match.

Default ("whistle" mode), based on how refereed matches flow:
    serve whistle -> serve -> rally -> end whistle -> walk back -> next serve whistle
1. Find referee whistles in the audio: a loud, narrow-band tone between about
   2 and 4.5 kHz. Clear whistles last at least `min_whistle_s`. Shorter
   whistle-like sounds (often a whistle drowned out by cheering) are trusted
   less: see pair_whistles.
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

from . import audio, motion, stitch
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
    min_whistle_s: float = 0.4     # a clear whistle; shoe squeaks are shorter
    weak_whistle_s: float = 0.15   # shorter whistle-like sounds (whistles drowned by cheering)
    min_between_s: float = 5.0     # end whistle to next serve whistle, at least
    missed_end_before_s: float = 6.0  # if an end whistle is missed, end the point this long before the next serve
    missed_whistle_cost: float = 4.0
    break_gap_s: float = 25.0      # silence after a timeout / set-break whistle
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
    for w0, *_ in whistles:
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


Whistle = tuple  # (start_s, end_s, clear_s)


def clean_whistles(whistles: list, p: RallyParams) -> list[Whistle]:
    """Drop tiny blips and merge whistle sounds that start close together."""
    out: list[list[float]] = []
    for w in whistles:
        a, b = w[0], w[1]
        clear = w[2] if len(w) > 2 else (b - a)
        if b - a < p.weak_whistle_s:
            continue
        if out and a - out[-1][0] < p.merge_whistle_s:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2] += clear
        else:
            out.append([a, b, clear])
    return [tuple(x) for x in out]


def _skip_cost(w: Whistle, p: RallyParams) -> float:
    """How costly it is to call this sound "not a referee whistle"."""
    a, b, clear = w
    return 0.3 + 3.0 * min(1.0, clear / p.min_whistle_s) + 1.0 * min(1.0, (b - a) / 0.6)


def _serve_cost(w: Whistle) -> float:
    """Serve whistles are usually long, steady blasts (about 1 s)."""
    d = w[1] - w[0]
    if d < 0.4:
        return 2.0
    return 0.7 * max(0.0, (1.0 - d) / 0.6)


def _point_cost(d: float, p: RallyParams) -> float | None:
    """Cost of a serve whistle followed by an end whistle d seconds later."""
    if d < p.min_point_s:
        return None
    if d > p.max_point_s:
        return None
    if d < 4.0:
        return 1.0     # possible (service error) but unusual
    if d > 30.0:
        return 1.5
    return 0.0


def _between_cost(g: float, p: RallyParams) -> float | None:
    """Cost of an end whistle followed by the next serve whistle g seconds later."""
    if g < p.min_between_s:
        return None
    if g < 7.0:
        return 1.5
    if g > 25.0:
        return 0.5     # timeouts and set breaks
    return 0.0


def decode_whistles(ws: list[Whistle], p: RallyParams) -> list[tuple[float, float, bool]]:
    """Label each whistle as serve, end or noise, picking the most plausible match.

    Rather than pairing whistles one by one (where one missed whistle shifts
    every point after it), this scores whole sequences and keeps the best:
    - skipping a clear, long whistle is expensive; skipping a short blip is cheap
    - a point (serve to end whistle) usually lasts 4 to 30 s
    - the gap from an end whistle to the next serve whistle is usually 7 to 25 s
    - if an end whistle is missed entirely, the point is still kept, with its
      end estimated as `missed_end_before_s` before the next serve whistle

    Returns (serve_s, end_s, end_estimated) per point.
    """
    n = len(ws)
    if n == 0:
        return []
    t = [w[0] for w in ws]
    skip = [_skip_cost(w, p) for w in ws]
    # A whistle followed by a long silence is usually a timeout or set-break
    # whistle rather than a serve or end whistle, so ignoring it is cheap.
    # The same goes for the whistle that ends a break and calls teams back.
    for k in range(n - 1):
        if t[k + 1] - t[k] >= p.break_gap_s:
            skip[k] = min(skip[k], 0.5)
            skip[k + 1] = min(skip[k + 1], 0.5)
    # Serve whistles are usually long, steady blasts; end whistles are often
    # short or drowned out by cheering. A short sound is a less likely serve.
    serve_cost = [_serve_cost(w) for w in ws]
    pre = [0.0]
    for c in skip:
        pre.append(pre[-1] + c)

    def skipped(i: int, j: int) -> float:   # cost of skipping everything strictly between i and j
        return pre[j] - pre[i + 1]

    INF = float("inf")
    S = [INF] * n   # best cost with whistle j as a serve whistle
    E = [INF] * n   # best cost with whistle j as an end whistle
    S_from: list = [None] * n
    E_from: list = [None] * n
    for j in range(n):
        # j is the first whistle we use: everything before it is noise
        S[j], S_from[j] = pre[j] + serve_cost[j], ("start", None)
        for i in range(j):
            between = skipped(i, j)
            if E[i] < INF:
                c = _between_cost(t[j] - t[i], p)
                if c is not None:
                    cost = E[i] + between + c + serve_cost[j]
                    if cost < S[j]:
                        S[j], S_from[j] = cost, ("E", i)
            if S[i] < INF:
                c = _point_cost(t[j] - t[i], p)
                if c is not None:
                    cost = S[i] + between + c
                    if cost < E[j]:
                        E[j], E_from[j] = cost, ("S", i)
                # serve whistle i whose end whistle was never heard
                if t[j] - t[i] >= p.missed_end_before_s + p.min_point_s:
                    cost = S[i] + between + p.missed_whistle_cost + serve_cost[j]
                    if cost < S[j]:
                        S[j], S_from[j] = cost, ("S", i)
    # finish: last used whistle is an end whistle, or a serve whose end is cut off
    best, last, kind = INF, None, None
    for j in range(n):
        tail = pre[n] - pre[j + 1]
        if E[j] + tail < best:
            best, last, kind = E[j] + tail, j, "E"
        if S[j] + tail + p.missed_whistle_cost < best:
            best, last, kind = S[j] + tail + p.missed_whistle_cost, j, "S"
    labels: list[tuple[str, int]] = []
    while last is not None:
        labels.append((kind, last))
        prev = (S_from if kind == "S" else E_from)[last]
        if prev[0] == "start":
            break
        kind, last = prev
    labels.reverse()
    points = []
    for k, (kind, j) in enumerate(labels):
        if kind != "S":
            continue
        nxt = labels[k + 1] if k + 1 < len(labels) else None
        if nxt and nxt[0] == "E":
            points.append((t[j], t[nxt[1]], False))
        elif nxt and nxt[0] == "S":
            points.append((t[j], t[nxt[1]] - p.missed_end_before_s, True))
    return points


def pair_whistles(whistles: list, p: RallyParams) -> list[tuple[float, float]]:
    """(serve_s, end_s) for each point; see decode_whistles."""
    return [(a, b) for a, b, _ in decode_whistles(clean_whistles(whistles, p), p)]


def detect_from_raw(raw: list, p: RallyParams) -> list[tuple[float, float]]:
    return pair_whistles(raw, p)


def detect_points_from_whistles(mdir: Path, p: RallyParams,
                                whistle_params: audio.WhistleParams | None = None) -> tuple[list[Point], dict]:
    meta = json.loads((mdir / "meta.json").read_text())
    duration = float(meta["duration_s"])
    raw = audio.detect_whistles(mdir / "audio.wav", whistle_params)
    kept = clean_whistles(raw, p)
    points = []
    for i, (a, b, estimated) in enumerate(decode_whistles(kept, p)):
        points.append(Point(
            index=i + 1, start_s=round(a, 2), end_s=round(b, 2),
            clip_start_s=round(max(0.0, a - p.clip_pad_before_s), 2),
            clip_end_s=round(min(duration, b + p.clip_pad_after_s), 2),
            ended_by_whistle=not estimated,
            server_guess="unknown", server_confidence=0.0,
            winner_guess="unknown", winner_confidence=0.0,
        ))
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
    if clips and points and (mdir / "video.mp4").exists():
        print("Stitching all clips into one review video ...")
        out = stitch.stitch(mdir)
        if out:
            print(f"Review video: {out}")
    (mdir / "rally_debug.json").write_text(json.dumps(debug))
    if mode == "whistle":
        print(f"Found {len(points)} points from {len(debug['whistles_kept'])} whistles "
              f"({len(debug['whistles']) - len(debug['whistles_kept'])} short sounds ignored).")
        for pt in points:
            end = f"end whistle {fmt(pt.end_s)}" if pt.ended_by_whistle else f"end ~{fmt(pt.end_s)} (no whistle heard)"
            print(f"  #{pt.index:>3}  serve whistle {fmt(pt.start_s)}  {end}  "
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
