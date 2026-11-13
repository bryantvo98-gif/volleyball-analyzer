"""Court motion signal from the sampled frames.

During a rally players move fast and the ball flies; between points they
walk, so frame-to-frame change drops. We measure the mean absolute
difference between consecutive downscaled grayscale frames, split into the
near half (bottom of the image, the team we track) and the far half.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_gray(path: Path, width: int = 160) -> np.ndarray:
    im = Image.open(path).convert("L")
    h = max(1, round(im.height * width / im.width))
    return np.asarray(im.resize((width, h), Image.BILINEAR), dtype=np.float32)


def motion_signal(frames_dir: Path, net_y: float = 0.5) -> dict[str, np.ndarray]:
    """Per-frame motion for the whole image, near side and far side.

    net_y is the net's height in the image as a fraction from the top
    (0.5 = middle). Value i is the change between frame i-1 and frame i.
    """
    files = sorted(frames_dir.glob("*.jpg"))
    total = np.zeros(len(files), dtype=np.float32)
    near = np.zeros_like(total)
    far = np.zeros_like(total)
    prev = None
    for i, f in enumerate(files):
        g = load_gray(f)
        if prev is not None and prev.shape == g.shape:
            d = np.abs(g - prev)
            cut = int(d.shape[0] * net_y)
            total[i] = d.mean()
            far[i] = d[:cut].mean() if cut > 0 else 0.0
            near[i] = d[cut:].mean() if cut < d.shape[0] else 0.0
        prev = g
    if len(total) > 1:
        total[0], near[0], far[0] = total[1], near[1], far[1]
    return {"total": total, "near": near, "far": far}


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) == 0:
        return x.astype(np.float32)
    k = np.ones(n, dtype=np.float32) / n
    pad = n // 2
    xp = np.pad(x.astype(np.float32), (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, k, mode="valid")
