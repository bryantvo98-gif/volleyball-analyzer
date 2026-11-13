"""Command-line entry point: `vb <command>`."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import media, rallies, review, stitch


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="vb", description="Volleyball match analysis")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Download a match (YouTube URL or local file) and prepare it")
    ing.add_argument("source", help="YouTube URL or path to a video file")
    ing.add_argument("--id", dest="match_id", help="Name for this match (default: YouTube id or file name)")
    ing.add_argument("--fps", type=float, default=5.0, help="Frames per second to sample (default 5)")
    ing.add_argument("--width", type=int, default=960, help="Width of sampled frames in px (default 960)")
    ing.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    ral = sub.add_parser("rallies", help="Find points in an ingested match and cut a clip for each")
    ral.add_argument("match_id", help="Match id used with `vb ingest`")
    ral.add_argument("--mode", choices=["whistle", "motion"], default="whistle",
                     help="whistle (default): points run from serve whistle to end whistle. "
                          "motion: older approach for videos without whistles")
    ral.add_argument("--min-whistle", type=float, default=0.4,
                     help="Length in seconds of a clear whistle (default 0.4). Shorter whistle-like "
                          "sounds can still end a point")
    ral.add_argument("--pad", type=float, default=2.0,
                     help="Seconds of video to keep before and after each point (default 2)")
    ral.add_argument("--force", action="store_true",
                     help="Replace points you've already reviewed (a backup copy is kept)")
    ral.add_argument("--no-clips", action="store_true", help="Skip cutting clips (faster, for tuning)")
    ral.add_argument("--threshold", type=float, default=0.4,
                     help="Motion threshold between quiet (0) and busy (1) levels (default 0.4)")
    ral.add_argument("--min-rally", type=float, default=3.0, help="Shortest rally in seconds (default 3)")
    ral.add_argument("--net-y", type=float, default=0.5,
                     help="Net height in the image, 0 = top, 1 = bottom (default 0.5)")
    ral.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    st = sub.add_parser("stitch", help="Rebuild the all_points.mp4 review video from existing clips")
    st.add_argument("match_id", help="Match id used with `vb ingest`")
    st.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    rv = sub.add_parser("review", help="Open the review screen to check and fix points")
    rv.add_argument("match_id", help="Match id used with `vb ingest`")
    rv.add_argument("--port", type=int, default=8765, help="Local port (default 8765)")
    rv.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    cl = sub.add_parser("clips", help="Recut clips and the stitched video from your reviewed points")
    cl.add_argument("match_id", help="Match id used with `vb ingest`")
    cl.add_argument("--data", type=Path, default=media.DATA_DIR, help="Where match folders live")

    args = p.parse_args(argv)
    if args.cmd == "ingest":
        media.ingest(args.source, match_id=args.match_id, root=args.data,
                     frame_rate=args.fps, frame_width=args.width)
    elif args.cmd == "rallies":
        params = rallies.RallyParams(threshold_mix=args.threshold, min_rally_s=args.min_rally,
                                     net_y=args.net_y, min_whistle_s=args.min_whistle,
                                     clip_pad_before_s=args.pad, clip_pad_after_s=args.pad)
        rallies.run(args.match_id, root=args.data, clips=not args.no_clips, p=params, mode=args.mode,
                    force=args.force)
    elif args.cmd == "review":
        review.serve(args.match_id, root=args.data, port=args.port)
    elif args.cmd == "clips":
        rallies.recut(args.match_id, root=args.data)
    elif args.cmd == "stitch":
        out = stitch.stitch(media.match_dir(args.match_id, args.data))
        print(f"Review video: {out}" if out else "No clips found. Run `vb rallies` first.")


if __name__ == "__main__":
    main()
