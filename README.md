# Volleyball match analyzer

A semi-automatic tool for reviewing fixed-camera volleyball footage. It finds each
rally, suggests who won it, what action ended it and which near-side player was
involved, and lets you confirm or fix those guesses before building a match summary.

## Plan

1. **Set up and pull in a match** (done): download a YouTube match and split it
   into frames and audio.
2. **Find the rallies** (done): detect point start and end from ball movement plus
   whistle and crowd audio, cut a clip per point, guess which side won.
3. **Identify players and review points** (local web pages):
   - *Players screen*: the tool groups near-side players by outfit (colour and
     look of top, shorts, shoes and so on) and shows a gallery for each one.
     You name them, merge duplicates and split mix-ups.
   - *Points screen*: each point's clip with the tool's guesses for who won,
     the ending action (kill, ace, block, service error, attack or net error and
     so on) and the player involved. Confirm or fix each one.
4. **Match summary**: point-by-point score with clip links, how points ended,
   and each player's winners and errors. A shareable live webpage comes later.

Only the team closest to the camera is tracked.

## Setup on a Mac

```bash
brew install ffmpeg python
git clone <this repo> && cd volleyball-analyzer
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
vb ingest "https://www.youtube.com/watch?v=g5GqP4kEs_0"
# or a file you already have
vb ingest ~/Movies/match.mp4 --id league-week3
```

This creates `data/matches/<id>/` with `video.mp4`, `audio.wav`, `frames/`
(5 frames per second at 960 px wide by default) and `meta.json`. The `data/`
folder is not committed to git.

```bash
vb rallies g5GqP4kEs_0
```

This finds each point and writes `points.json` plus one clip per point in
`clips/`. It prints a line per point with its start and end time, who it thinks
served and who it thinks won.

How it decides:

- **Rally start and end**: a rally is a stretch of high motion on court. The
  cutoff adapts to each video. If a referee whistle comes within 3 seconds of the
  motion stopping, the end moves to the whistle.
- **Who served**: where the motion is in the first 1.5 seconds. A near-side serve
  shows up at the bottom of the frame.
- **Who won**: with rally scoring, the next point's server won this point. The
  last point is left as unknown for you to fill in.

Tuning, if points are merged, split or missed:

- `--threshold 0.3` catches quieter rallies, `0.5` ignores more walking around.
- `--min-rally 2` keeps very short points such as aces and service errors.
- `--net-y 0.45` sets where the net sits in the image (0 is the top), which
  matters for the serve guess.
- `--no-clips` skips clip cutting so you can retune quickly.

## Tests

```bash
pytest
```
