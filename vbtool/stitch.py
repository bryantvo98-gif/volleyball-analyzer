"""Stitch every point clip into one review video, labelled in the top right.

The label reads e.g. "Point 12  |  6:24" (point number and where the point
starts in the original video). Labels are drawn with Pillow and overlaid by
ffmpeg, so this works with any ffmpeg build (no font support needed).
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


def stitch(mdir: Path) -> Path | None:
    """Build mdir/all_points.mp4 from the clips listed in points.json."""
    require_tool("ffmpeg")
    points = json.loads((mdir / "points.json").read_text())
    points = [p for p in points if p.get("clip") and (mdir / p["clip"]).exists()]
    if not points:
        return None
    height = probe(mdir / points[0]["clip"])["height"]
    out = mdir / OUTPUT_NAME
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts = []
        for p in points:
            label = tmpdir / f"label_{p['index']:03d}.png"
            make_label(f"Point {p['index']}  |  {fmt(p['start_s'])}", height, label)
            part = tmpdir / f"part_{p['index']:03d}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-i", str(mdir / p["clip"]), "-i", str(label),
                 "-filter_complex", "[0:v][1:v]overlay=W-w-20:20[v]",
                 "-map", "[v]", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-profile:v", "high", "-tag:v", "avc1",
                 "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                 str(part)],
                check=True,
            )
            parts.append(part)
        listing = tmpdir / "list.txt"
        listing.write_text("".join(f"file '{x.as_posix()}'\n" for x in parts))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)],
            check=True,
        )
    return out
