"""Media helpers: download a match and split it into frames and audio.

Everything for one match lives in data/matches/<match_id>/:
    video.mp4      the source video (re-encoded copy only if needed)
    audio.wav      mono 16 kHz audio, used later for whistle detection
    frames/        JPEG frames sampled at a low rate, used later for
                   ball and player detection
    meta.json      source URL, title, duration, fps, resolution
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

DATA_DIR = Path("data") / "matches"


@dataclass
class MatchMeta:
    match_id: str
    source: str
    title: str
    duration_s: float
    fps: float
    width: int
    height: int
    frame_rate_sampled: float
    frame_width: int
    frame_count: int


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        hint = " Install it with: brew install ffmpeg" if name in ("ffmpeg", "ffprobe") else ""
        raise SystemExit(f"'{name}' was not found on your PATH.{hint}")
    return path


def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:60] or "match"


def match_dir(match_id: str, root: Path = DATA_DIR) -> Path:
    return root / match_id


def download_youtube(url: str, dest: Path, max_height: int = 720) -> str:
    """Download a YouTube video to dest/video.mp4. Returns the video title."""
    import yt_dlp  # imported lazily so local-file ingest works without it

    require_tool("ffmpeg")
    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        # Prefer H.264 (avc1): YouTube's AV1/VP9 streams play badly in QuickTime
        "format": (f"bv*[vcodec^=avc1][height<={max_height}]+ba[ext=m4a]/"
                   f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(dest / "video.%(ext)s"),
        "quiet": False,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return info.get("title") or url


def probe(video: Path) -> dict:
    require_tool("ffprobe")
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration",
            "-of", "json", str(video),
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(num) / float(den or 1),
        "duration_s": float(data["format"]["duration"]),
    }


def extract_audio(video: Path, out: Path, sample_rate: int = 16000) -> None:
    require_tool("ffmpeg")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(sample_rate), str(out)],
        check=True,
    )


def extract_frames(video: Path, out_dir: Path, rate: float = 5.0, width: int = 960) -> int:
    """Sample frames at `rate` per second, scaled to `width` px wide.

    Frame N (1-based file name) is at time (N - 1) / rate seconds.
    """
    require_tool("ffmpeg")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={rate},scale={width}:-2", "-q:v", "3",
         str(out_dir / "%06d.jpg")],
        check=True,
    )
    return len(list(out_dir.glob("*.jpg")))


def ingest(source: str, match_id: str | None = None, root: Path = DATA_DIR,
           frame_rate: float = 5.0, frame_width: int = 960) -> MatchMeta:
    """Download (or copy) a match and prepare frames, audio and metadata."""
    local = Path(source).expanduser()
    is_local = local.exists()

    if match_id is None:
        match_id = slugify(local.stem) if is_local else (youtube_id(source) or slugify(source))
    mdir = match_dir(match_id, root)
    mdir.mkdir(parents=True, exist_ok=True)
    video = mdir / "video.mp4"

    if is_local:
        title = local.stem
        if local.resolve() != video.resolve():
            shutil.copyfile(local, video)
    elif video.exists():
        print(f"Video already downloaded at {video}, skipping download.")
        old = mdir / "meta.json"
        title = json.loads(old.read_text())["title"] if old.exists() else source
    else:
        print(f"Downloading {source} ...")
        title = download_youtube(source, mdir)

    info = probe(video)
    print(f"Video: {info['width']}x{info['height']} @ {info['fps']:.2f} fps, "
          f"{info['duration_s'] / 60:.1f} min")

    print("Extracting audio ...")
    extract_audio(video, mdir / "audio.wav")

    print(f"Extracting frames at {frame_rate}/s ...")
    count = extract_frames(video, mdir / "frames", rate=frame_rate, width=frame_width)

    meta = MatchMeta(
        match_id=match_id, source=source, title=title,
        duration_s=info["duration_s"], fps=info["fps"],
        width=info["width"], height=info["height"],
        frame_rate_sampled=frame_rate, frame_width=frame_width, frame_count=count,
    )
    (mdir / "meta.json").write_text(json.dumps(asdict(meta), indent=2))
    print(f"Done. {count} frames saved to {mdir}")
    return meta
