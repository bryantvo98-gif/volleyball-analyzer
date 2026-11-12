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

One time only. Run each line on its own in Terminal.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'export PATH=/opt/homebrew/bin:$PATH' > ~/.zprofile
source ~/.zprofile
brew install python ffmpeg gh
gh auth login
gh repo clone bryantvo98-gif/volleyball-analyzer ~/volleyball-analyzer
cd ~/volleyball-analyzer
/opt/homebrew/bin/python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

Each time you open a new Terminal window:

```bash
cd ~/volleyball-analyzer
source .venv/bin/activate
```

To get the latest code: `git pull`, then `pip install -e .`

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

This finds each point and writes `points.json`, one clip per point in
`clips/`, and `all_points.mp4`: every clip back to back, with the point number
and its start time in the original video shown in the top right corner. Use it
to skim the whole match and spot wrong clips quickly. `vb stitch <id>` rebuilds
just that video. It prints one line per point with the serve whistle, end whistle and
clip times.

How it decides (default whistle mode):

- **Whistles**: a loud tone between about 2 and 4.5 kHz. A clear whistle lasts
  at least 0.4 seconds. Cheering can drown out part of a whistle, so shorter
  whistle-like sounds (0.15 to 0.4 seconds) are kept too, but trusted less:
  they can only end a point, and only once 6 seconds have passed since the
  serve whistle, so a squeak just after the serve doesn't end it. Whistles
  less than 2 seconds apart count as one.
- **Points**: whistles alternate between "serve" and "point over". A point
  runs from a serve whistle to the next whistle. If that would be longer than
  40 seconds (a missed whistle or a timeout), the tool starts pairing again
  from the later whistle.
- **Clips**: from 2 seconds before the serve whistle to 2 seconds after the
  end whistle, so the serve and the finish are always included.
- Who served and who won are not guessed yet. That needs ball tracking.

Options:

- `--min-whistle 0.5` sets how long a sound must be to count as a clear whistle.
- `--pad 3` keeps 3 seconds before and after each point instead of 2.
- `--no-clips` skips clip cutting so you can retune quickly.
- `--mode motion` uses the older motion-based approach, for videos without a
  referee.

## Tests

```bash
pytest
```
