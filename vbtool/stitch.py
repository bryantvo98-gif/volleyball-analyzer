"""Labelled point videos.

- Single clips (clips/point_NNN.mp4) are labelled "Point 12  |  6:24": the
  point number and where it starts in the full match video.
- The stitched video (all_points.mp4) plays every point back to back,
  labelled "Point 12  |  Bumps 9 - 8 Opponents": the score before that point,
  from the winners recorded in the review screen.

Labels are drawn with Pillow and overlaid by ffmpeg, so this works with any
ffmpeg build (no font support needed).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .media import probe, require_tool

OUTPUT_NAME = "all_points.mp4"


def fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def make_label(text: str, video_height: int, out: Path) -> None:
    size = max(18, video_height // 18)
    font = ImageFont.load_default(size=size)
    pad = size // 3
    probe_img = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe_img.textbbox((0, 0), text, font=font)
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 170))
    ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)


TEAM = "Bumps"
OPPONENT = "Opponents"

# 8-bit 4:2:0 H.264 High profile plays natively in QuickTime and browsers.
VIDEO_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-profile:v", "high", "-tag:v", "avc1"]
AUDIO_OUT = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"]


def clip_label(point: dict) -> str:
    return f"Point {point['index']}  |  {fmt(point['start_s'])}"


def scores_before(points: list[dict]) -> list[tuple[int, int]]:
    """(our score, their score) going into each point, from recorded winners."""
    us = them = 0
    out = []
    for p in points:
        out.append((us, them))
        winner = (p.get("extra") or {}).get("winner")
        if winner == "near":
            us += 1
        elif winner == "far":
            them += 1
    return out


def stitched_label(point: dict, score: tuple[int, int]) -> str:
    return f"Point {point['index']}  |  {TEAM} {score[0]} - {score[1]} {OPPONENT}"


def cut_labelled(video: Path, start: float, end: float, text: str, height: int,
                 out: Path, workdir: Path, extra: list[str] | None = None) -> None:
    """Cut [start, end] from video with `text` in the top right corner."""
    label = workdir / (out.stem + "_label.png")
    make_label(text, height, label)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(video), "-i", str(label),
         "-filter_complex", "[0:v][1:v]overlay=W-w-H/36:H/36[v]",
         "-map", "[v]", "-map", "0:a?", *VIDEO_OUT, *AUDIO_OUT, *(extra or []),
         "-movflags", "+faststart", str(out)],
        check=True,
    )


def stitch(mdir: Path) -> Path | None:
    """Build mdir/all_points.mp4 from the points in points.json."""
    require_tool("ffmpeg")
    points = json.loads((mdir / "points.json").read_text())
    video = mdir / "video.mp4"
    if not points or not video.exists():
        return None
    height = probe(video)["height"]
    out = mdir / OUTPUT_NAME
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts = []
        for p, score in zip(points, scores_before(points)):
            part = tmpdir / f"part_{p['index']:03d}.mp4"
            # Same frame rate and audio format for every part so they join cleanly.
            cut_labelled(video, p["clip_start_s"], p["clip_end_s"], stitched_label(p, score),
                         height, part, tmpdir, extra=["-r", "30"])
            parts.append(part)
        listing = tmpdir / "list.txt"
        listing.write_text("".join(f"file '{x.as_posix()}'\n" for x in parts))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)],
            check=True,
        )
    return out
