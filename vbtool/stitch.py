"""Labelled point videos.

- Single clips (clips/point_NNN.mp4) are labelled "Point 12 | 6:24": the
  point number and where it starts in the full match video.
- The stitched video (all_points.mp4) plays every point back to back,
  labelled "Point 12 | Bumps 9 – 8 Opponents": the score before that point,
  from the winners recorded in the review screen.

Labels are frosted-glass capsules: drawn with Pillow and overlaid by ffmpeg on
a blurred patch of the video, so this works with any ffmpeg build (no font
support needed).
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


# Label look: a frosted-glass capsule in the top right corner. The video behind
# it is blurred and darkened so white text stays readable over any background.
NEAR_COLOR = (10, 132, 255)     # Bumps (blue)
FAR_COLOR = (255, 159, 10)      # Opponents (orange)
PRIMARY = (255, 255, 255, 255)
SECONDARY = (255, 255, 255, 200)
GLASS_TINT = (22, 22, 24, 150)
GLASS_EDGE = (255, 255, 255, 46)
SUPERSAMPLE = 3                 # draw big, shrink down: smooth edges and corners

# (path, collection index or variable-font style name) from most to least preferred.
FONTS = {
    "regular": [("/System/Library/Fonts/SFNS.ttf", b"Medium"),
                ("/System/Library/Fonts/HelveticaNeue.ttc", 10),
                ("/usr/share/fonts/opentype/inter/Inter-Medium.otf", None)],
    "bold": [("/System/Library/Fonts/SFNS.ttf", b"Semibold"),
             ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
             ("/usr/share/fonts/opentype/inter/Inter-SemiBold.otf", None)],
}


def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    """The system font on a Mac, with fallbacks so labels work anywhere."""
    for path, variant in FONTS[weight]:
        try:
            if isinstance(variant, int):
                return ImageFont.truetype(path, size, index=variant)
            font = ImageFont.truetype(path, size)
            if variant:
                font.set_variation_by_name(variant)
            return font
        except (OSError, ValueError):
            continue
    return ImageFont.load_default(size=size)


def make_label(parts: list[tuple], video_height: int, out: Path, mask_out: Path) -> tuple[int, int]:
    """Draw a label capsule and its blur mask. Returns the label's (width, height).

    parts is a list of ("text", string, "bold" | "regular" | "dim"),
    ("dot", rgb colour) and ("divider",).
    """
    k = SUPERSAMPLE
    size = max(16, video_height // 26) * k
    fonts = {"bold": load_font("bold", size), "regular": load_font("regular", size)}
    fonts["dim"] = fonts["regular"]
    gap, dot = round(size * 0.42), round(size * 0.5)
    height = round(size * 1.9)
    pad = round(size * 0.85)

    widths = []
    for part in parts:
        if part[0] == "text":
            widths.append(round(fonts[part[2]].getlength(part[1])))
        elif part[0] == "dot":
            widths.append(dot)
        else:
            widths.append(k)
    width = 2 * pad + sum(widths) + gap * (len(parts) - 1)

    radius = height // 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius, fill=GLASS_TINT,
                           outline=GLASS_EDGE, width=k)
    x, mid = pad, height // 2
    for part, w in zip(parts, widths):
        if part[0] == "text":
            fill = SECONDARY if part[2] == "dim" else PRIMARY
            draw.text((x, mid), part[1], font=fonts[part[2]], fill=fill, anchor="lm")
        elif part[0] == "dot":
            draw.ellipse((x, mid - dot // 2, x + dot, mid - dot // 2 + dot), fill=(*part[1], 255))
        else:
            draw.line((x, mid - size * 0.5, x, mid + size * 0.5), fill=GLASS_EDGE, width=k)
        x += w + gap
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius, fill=255)

    # Even sizes keep ffmpeg's crop exactly aligned with the label.
    final = (round(width / k / 2) * 2, round(height / k / 2) * 2)
    img.resize(final, Image.LANCZOS).save(out)
    mask.resize(final, Image.LANCZOS).save(mask_out)
    return final


TEAM = "Bumps"
OPPONENT = "Opponents"

# 8-bit 4:2:0 H.264 High profile plays natively in QuickTime and browsers.
VIDEO_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-profile:v", "high", "-tag:v", "avc1"]
AUDIO_OUT = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"]


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


def clip_parts(point: dict) -> list[tuple]:
    return [("text", f"Point {point['index']}", "bold"), ("divider",),
            ("text", fmt(point["start_s"]), "dim")]


def stitched_parts(point: dict, score: tuple[int, int]) -> list[tuple]:
    return [("text", f"Point {point['index']}", "bold"), ("divider",),
            ("dot", NEAR_COLOR), ("text", TEAM, "regular"),
            ("text", f"{score[0]} \u2013 {score[1]}", "bold"),
            ("text", OPPONENT, "regular"), ("dot", FAR_COLOR)]


def cut_labelled(video: Path, start: float, end: float, parts: list[tuple], size: tuple[int, int],
                 out: Path, workdir: Path, extra: list[str] | None = None) -> None:
    """Cut [start, end] from video with a label capsule in the top right corner.

    size is the video's (width, height).
    """
    label = workdir / (out.stem + "_label.png")
    mask = workdir / (out.stem + "_mask.png")
    video_w, video_h = size
    w, h = make_label(parts, video_h, label, mask)
    margin = video_h // 30
    x, y = (video_w - w - margin) // 2 * 2, margin // 2 * 2
    blur = max(4, video_h // 45)
    graph = (f"[0:v]split[base][copy];"
             f"[copy]crop={w}:{h}:{x}:{y},gblur=sigma={blur}[soft];"
             f"[soft][2:v]alphamerge[glass];"
             f"[base][glass]overlay={x}:{y}[frosted];"
             f"[frosted][1:v]overlay={x}:{y}[v]")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(video),
         "-i", str(label), "-i", str(mask),
         "-filter_complex", graph,
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
    info = probe(video)
    size = (info["width"], info["height"])
    out = mdir / OUTPUT_NAME
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts = []
        for p, score in zip(points, scores_before(points)):
            part = tmpdir / f"part_{p['index']:03d}.mp4"
            # Same frame rate and audio format for every part so they join cleanly.
            cut_labelled(video, p["clip_start_s"], p["clip_end_s"], stitched_parts(p, score),
                         size, part, tmpdir, extra=["-r", "30"])
            parts.append(part)
        listing = tmpdir / "list.txt"
        listing.write_text("".join(f"file '{x.as_posix()}'\n" for x in parts))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)],
            check=True,
        )
    return out
