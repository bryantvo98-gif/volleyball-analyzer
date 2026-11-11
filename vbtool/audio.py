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


def whistle_score(x: np.ndarray, sr: int, p: WhistleParams) -> tuple[np.ndarray, np.ndarray]:
    """Return (times, is_whistle) for each analysis window."""
    win = int(sr * p.window_s)
    n = len(x) // win
    if n == 0:
        return np.zeros(0), np.zeros(0, dtype=bool)
    frames = x[: n * win].reshape(n, win) * np.hanning(win)
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = (freqs >= p.band_hz[0]) & (freqs <= p.band_hz[1])
    total = spec.sum(axis=1) + 1e-12
    in_band = spec[:, band]
    ratio = in_band.sum(axis=1) / total
    peak = in_band.max(axis=1) / (np.median(in_band, axis=1) + 1e-12)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    hit = (ratio > p.band_ratio) & (peak > p.peakiness) & (rms > p.min_loudness)
    times = (np.arange(n) + 0.5) * p.window_s
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


def detect_whistles(wav: Path, p: WhistleParams | None = None) -> list[tuple[float, float]]:
    p = p or WhistleParams()
    x, sr = load_wav(wav)
    _, hit = whistle_score(x, sr, p)
    return runs(hit, p.window_s, p.merge_gap_s, p.min_duration_s)
