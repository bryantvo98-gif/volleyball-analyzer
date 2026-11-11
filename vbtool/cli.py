"""Command-line entry point: `vb <command>`."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import media


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="vb", description="Volleyball match analysis")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Download a match (YouTube URL or local file) and prepare it")
    ing.add_argument("source", help="YouTube URL or path to a video file")
    ing.add_argument("--id", dest="match_id", help="Name for this match (default: YouTube id or file name)")
    ing.add_argument("--fps", type=float, default=5.0, help="Frames per second to sample (default 5)")
    ing.add_argument("--width", type=int, default=960, help="Width of sampled frames in px (default 960)")
    ing.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    args = p.parse_args(argv)
    if args.cmd == "ingest":
        media.ingest(args.source, match_id=args.match_id, root=args.data,
                     frame_rate=args.fps, frame_width=args.width)


if __name__ == "__main__":
    main()
