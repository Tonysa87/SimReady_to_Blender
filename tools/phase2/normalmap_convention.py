# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect whether tangent-space normal maps are OpenGL (+Y) or DirectX (-Y).

A normal map baked from a surface h(u, v) is integrable: with image x = column
(right) and y = row (down), an OpenGL map encodes n ~ (-h_x, +h_y, 1), so
d(nx)/dy = -d(ny)/dx  ->  corr(d(nx)/dy, d(ny)/dx) < 0.
A DirectX map has the green channel inverted -> the correlation is > 0.

python3 -I normalmap_convention.py --selftest
python3 -I normalmap_convention.py file.png [...]
"""

import sys

import numpy as np
from PIL import Image

MAX_SIDE = 1024


def score(rgb: np.ndarray) -> float:
    """Correlation between d(nx)/drow and d(ny)/dcol on decoded, nz-normalised normals."""
    n = rgb.astype(np.float64) * 2.0 - 1.0
    nz = np.clip(n[..., 2], 0.2, None)
    sx, sy = n[..., 0] / nz, n[..., 1] / nz
    a = np.diff(sx, axis=0)[:, :-1]
    b = np.diff(sy, axis=1)[:-1, :]
    mask = (np.abs(a) + np.abs(b)) > 1e-4
    if mask.sum() < 1000:
        return 0.0
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


def load(path: str) -> np.ndarray:
    im = Image.open(path)
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    arr = np.asarray(im.convert("RGB") if im.mode not in ("I;16", "RGB") else im)
    if arr.dtype == np.uint16 or arr.max() > 255:
        return arr[..., :3] / 65535.0
    return arr[..., :3] / 255.0


def verdict(s: float) -> str:
    if abs(s) < 0.05:
        return "undetermined"
    return "OpenGL (+Y)" if s < 0 else "DirectX (-Y)"


def selftest() -> None:
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:512, 0:512] / 512.0
    h = sum(rng.uniform(0.2, 1) * np.sin(2 * np.pi * (rng.integers(1, 9) * x + rng.integers(1, 9) * y)
                                          + rng.uniform(0, 6)) for _ in range(12)) * 0.02
    hx = np.gradient(h, axis=1) * 512
    hrow = np.gradient(h, axis=0) * 512
    # OpenGL: v points up the image, so dh/dv = -dh/drow; n = (-h_u, -h_v, 1).
    n = np.dstack([-hx, hrow, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    gl = (n + 1) / 2
    dx = gl.copy()
    dx[..., 1] = 1 - dx[..., 1]
    s_gl, s_dx = score(gl), score(dx)
    print(f"selftest OpenGL map: {s_gl:+.3f} -> {verdict(s_gl)}")
    print(f"selftest DirectX map: {s_dx:+.3f} -> {verdict(s_dx)}")
    assert s_gl < -0.05 and s_dx > 0.05, "selftest failed"


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        for p in sys.argv[1:]:
            s = score(load(p))
            print(f"{s:+.3f}  {verdict(s):14s}  {p}")
