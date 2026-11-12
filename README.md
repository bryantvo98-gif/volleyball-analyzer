# Volleyball match analyzer

A semi-automatic tool for reviewing fixed-camera volleyball footage. It finds each
rally, suggests who won it, what action ended it and which near-side player was
involved, and lets you confirm or fix those guesses before building a match summary.

## Plan

1. **Set up and pull in a match** (done): download a YouTube match and split it
   into frames and audio.
2. **Find the rallies** (done): detect point start and end from ball movement plus
   whistle and crowd audio, cut a clip per point, guess which side won.
3. **Identify players and review points** (local web pages; points screen done):
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

- **Whistles**: first, unmistakable whistles (most of the sound's energy in
  the 2 to 4.5 kHz band). From those it learns the referee's whistle pitch
  (about 3.2 kHz in the first match), then also picks up quieter sounds with a
  sharp peak at that same pitch. That catches whistles blown while the crowd is
  cheering, since crowd noise sits lower, around 1 to 1.5 kHz.
- **Points**: each whistle is labelled serve, end of point or "something else",
  choosing the most plausible labelling for the whole match at once, so one
  missed whistle can't shift every point after it. What counts as plausible:
  - a point (serve whistle to end whistle) usually lasts 4 to 30 seconds
  - the next serve whistle comes 7 to 25 seconds after an end whistle
  - serve whistles are usually long blasts, end whistles are often short
  - ignoring a long, clear whistle is unlikely; ignoring a short blip is fine
  - a whistle before or after a long silence is likely a timeout or set break
  - if an end whistle is never heard, the point is still kept, ending 6
    seconds before the next serve whistle, and is marked "no whistle heard"
- **Clips**: from 2 seconds before the serve whistle to 2 seconds after the
  end whistle, so the serve and the finish are always included.
- Who served and who won are not guessed yet. That needs ball tracking.

Options:

- `--min-whistle 0.5` sets how long a sound must be to count as a clear whistle.
- `--pad 3` keeps 3 seconds before and after each point instead of 2.
- `--no-clips` skips clip cutting so you can retune quickly.
- `--mode motion` uses the older motion-based approach, for videos without a
  referee.

### Review and fix points

```bash
vb review g5GqP4kEs_0
```

Opens a review screen in your browser (it runs only on your Mac). Pick a point
on the right and its clip plays in the full match video. For each point you can:

- **Fix it**: "Not a point", "Merge with next", "Split here" (at the current
  video time), "Add a point here", or move the start or end by half a second or
  to the current video time.
- **Record it**: who won (near = your team, far = the other team), what ended
  it (ace, kill, service error, attack error, block and so on), whether a near
  or far player did it, the near-side player's name, and notes.

The running score shows at the top. Changes save automatically to
`points.json`, and the tool's original guesses are kept in `points.auto.json`.
Press Control+C in Terminal when you're done. Keyboard shortcuts are listed
under the video.

After fixing points, recut the clips and the stitched video:

```bash
vb clips g5GqP4kEs_0
```

`vb rallies` won't overwrite points you've reviewed unless you add `--force`
(and it keeps a backup).

## Tests

```bash
pytest
```
