# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 5, point 4: what would albedo textures in JPEG save, and what would they lose?

    python3 -P tools/phase5/measure_jpeg.py <raw folder> [<raw folder> ...] [--out result.json]

For every albedo PNG found under the given folders (downloaded assets, untrusted: only read
as images): size of the original, then at 2048 px and at the original size, PNG (zlib 6)
vs JPEG q90/q95 (4:4:4 and 4:2:0), with PSNR and SSIM (luma, 8x8 windows) against the PNG.
Textures with real transparency (alpha < 255 somewhere) are counted apart: they stay PNG.
Needs numpy and Pillow (system Python).
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ALBEDO = re.compile(r"(alb|albedo|basecolor|base_color|diffuse)", re.I)
Image.MAX_IMAGE_PIXELS = 1 << 28


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def _box(x: np.ndarray, k: int) -> np.ndarray:
    c = np.cumsum(np.cumsum(np.pad(x, ((1, 0), (1, 0))), 0), 1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def ssim(a: np.ndarray, b: np.ndarray, k: int = 8) -> float:
    def luma(x):
        return x[..., :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    x, y = luma(a), luma(b)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box(x, k), _box(y, k)
    vx, vy, cxy = _box(x * x, k) - mx * mx, _box(y * y, k) - my * my, _box(x * y, k) - mx * my
    s = ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))
    return float(s.mean())


def encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def measure(path: Path) -> dict:
    with Image.open(path) as im:
        im.load()
        mode = im.mode
        rgba = im.convert("RGBA")
    alpha = np.asarray(rgba)[..., 3]
    out = {"file": f"{path.parts[-4] if len(path.parts) > 3 else ''}/{path.name}", "size": list(rgba.size),
           "mode": mode, "bytes_orig": path.stat().st_size, "alpha": bool((alpha < 255).any())}
    if out["alpha"]:
        return out
    rgb = rgba.convert("RGB")
    for label, im in (("max", rgb), ("2k", None)):
        if label == "2k":
            if max(rgb.size) <= 2048:
                im = rgb
            else:
                f = 2048 / max(rgb.size)
                im = rgb.resize((max(1, round(rgb.width * f)), max(1, round(rgb.height * f))), Image.LANCZOS)
        ref = np.asarray(im)
        res = {"png": len(encode(im, "PNG", compress_level=6))}
        for q in (90, 95):
            for sub, name in ((0, "444"), (2, "420")):
                data = encode(im, "JPEG", quality=q, subsampling=sub, optimize=True)
                dec = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
                res[f"q{q}_{name}"] = {"bytes": len(data), "psnr": round(psnr(ref, dec), 2),
                                       "ssim": round(ssim(ref, dec), 4)}
        out[label] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    files = sorted({p for r in args.roots for p in r.rglob("*.png")
                    if ALBEDO.search(p.name) and ".thumbs" not in p.parts})
    rows = []
    for p in files:
        try:
            rows.append(measure(p))
        except Exception as e:  # damaged or odd file: skip, report
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        r = rows[-1]
        if r["alpha"]:
            print(f"{r['file']:60s} {r['size']} alpha -> stays PNG")
        else:
            m = r["2k"]
            print(f"{r['file']:60s} {r['size']} 2k png {m['png'] / 1e6:5.1f} MB  q90_444 {m['q90_444']['bytes'] / 1e6:5.2f} MB "
                  f"psnr {m['q90_444']['psnr']} ssim {m['q90_444']['ssim']}")
    ok = [r for r in rows if not r["alpha"]]
    summary = {"albedo_files": len(rows), "with_alpha": len(rows) - len(ok)}
    for label in ("max", "2k"):
        png = sum(r[label]["png"] for r in ok)
        summary[label] = {"png_MB": round(png / 1e6, 1)}
        for v in ("q90_444", "q90_420", "q95_444", "q95_420"):
            b = sum(r[label][v]["bytes"] for r in ok)
            summary[label][v] = {"MB": round(b / 1e6, 1), "ratio": round(b / png, 3) if png else None,
                                 "psnr_min": min((r[label][v]["psnr"] for r in ok), default=None),
                                 "psnr_median": float(np.median([r[label][v]["psnr"] for r in ok])) if ok else None,
                                 "ssim_min": min((r[label][v]["ssim"] for r in ok), default=None)}
    print(json.dumps(summary, indent=1))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "files": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
