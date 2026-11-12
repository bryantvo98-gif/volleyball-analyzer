"""Whistle detection from the match audio.

A referee whistle is a loud, narrow-band tone, usually between about 2 and
4.5 kHz. We compute a short-time spectrum and flag moments where most of the
energy sits in that band and one frequency clearly dominates.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class WhistleParams:
    band_hz: tuple[float, float] = (2000.0, 4500.0)
    window_s: float = 0.05
    band_ratio: float = 0.55      # share of energy inside the band
    peakiness: float = 8.0        # peak bin vs median bin inside the band
    min_loudness: float = 0.005   # RMS floor, ignores quiet hiss
    min_duration_s: float = 0.15
    merge_gap_s: float = 0.3
    # Second pass for whistles buried in crowd noise: learn the referee's
    # whistle pitch from the clear whistles, then also accept quieter windows
    # with a sharp spectral peak at that same pitch.
    pitch_tolerance_hz: float = 150.0
    pitched_peakiness: float = 100.0
    min_clear_windows: int = 20


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        width = w.getsampwidth()
        channels = w.getnchannels()
    if width != 2:
        raise ValueError("Expected 16-bit audio (vb ingest writes this format)")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, sr


def whistle_features(x: np.ndarray, sr: int, p: WhistleParams) -> dict[str, np.ndarray]:
    """Per-window features: band energy share, peak sharpness, peak pitch, loudness."""
    win = int(sr * p.window_s)
    n = len(x) // win
    if n == 0:
        z = np.zeros(0)
        return {"ratio": z, "peak": z, "pitch": z, "rms": z}
    frames = x[: n * win].reshape(n, win) * np.hanning(win)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = (freqs >= p.band_hz[0]) & (freqs <= p.band_hz[1])
    total = spec.sum(axis=1) + 1e-12
    in_band = spec[:, band]
    return {
        "ratio": in_band.sum(axis=1) / total,
        "peak": in_band.max(axis=1) / (np.median(in_band, axis=1) + 1e-12),
        "pitch": freqs[band][np.argmax(in_band, axis=1)],
        "rms": np.sqrt((frames ** 2).mean(axis=1)),
    }


def whistle_masks(x: np.ndarray, sr: int, p: WhistleParams) -> tuple[np.ndarray, np.ndarray]:
    """Return (clear, hit) boolean masks, one value per analysis window.

    clear: most of the window's energy is in the whistle band (an unmistakable
    whistle). hit: clear, or a very sharp peak at the referee's own whistle
    pitch. Crowd noise sits mostly around 1-1.5 kHz and drags the band share
    down even when a whistle is plainly there, so the second test catches
    whistles blown during cheering. The pitch is learned from the clear
    windows, so it adapts to each referee's whistle.
    """
    f = whistle_features(x, sr, p)
    loud = f["rms"] > p.min_loudness
    clear = (f["ratio"] > p.band_ratio) & (f["peak"] > p.peakiness) & loud
    hit = clear.copy()
    if clear.sum() >= p.min_clear_windows:
        pitch = float(np.median(f["pitch"][clear]))
        pitched = (np.abs(f["pitch"] - pitch) <= p.pitch_tolerance_hz) & (f["peak"] > p.pitched_peakiness) & loud
        hit |= pitched
    return clear, hit


def whistle_score(x: np.ndarray, sr: int, p: WhistleParams) -> tuple[np.ndarray, np.ndarray]:
    """Return (times, is_whistle) for each analysis window."""
    _, hit = whistle_masks(x, sr, p)
    times = (np.arange(len(hit)) + 0.5) * p.window_s
    return times, hit


def runs(mask: np.ndarray, step_s: float, merge_gap_s: float, min_len_s: float,
         offset_s: float = 0.0) -> list[tuple[float, float]]:
    """Turn a boolean mask into (start_s, end_s) runs, merging small gaps."""
    out: list[list[float]] = []
    idx = np.flatnonzero(mask)
    for i in idx:
        t0, t1 = offset_s + i * step_s, offset_s + (i + 1) * step_s
        if out and t0 - out[-1][1] <= merge_gap_s:
            out[-1][1] = t1
        else:
            out.append([t0, t1])
    return [(a, b) for a, b in out if b - a >= min_len_s]


def detect_whistles(wav: Path, p: WhistleParams | None = None) -> list[tuple[float, float, float]]:
    """Whistle-like sounds as (start_s, end_s, clear_s).

    clear_s is how many seconds of the sound were an unmistakable whistle;
    it is the main measure of how much to trust each sound.
    """
    p = p or WhistleParams()
    x, sr = load_wav(wav)
    clear, hit = whistle_masks(x, sr, p)
    out = []
    for a, b in runs(hit, p.window_s, p.merge_gap_s, p.min_duration_s):
        i0, i1 = int(round(a / p.window_s)), int(round(b / p.window_s))
        out.append((a, b, round(float(clear[i0:i1].sum()) * p.window_s, 3)))
    return out
