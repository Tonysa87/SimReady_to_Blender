# SPDX-License-Identifier: GPL-3.0-or-later
"""Side-by-side montage: NVIDIA thumbnail | our renders, with captions.

python3 -P montage.py --out out.png --tile 384 "NVIDIA=thumb.png" "Max=a.png" "1K=b.png" ...
"""

import argparse

from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--tile", type=int, default=384)
ap.add_argument("--title", default="")
ap.add_argument("items", nargs="+", help="caption=path")
a = ap.parse_args()

pad, cap = 8, 22
top = cap if a.title else 0
W = len(a.items) * (a.tile + pad) + pad
H = top + a.tile + cap + 2 * pad
canvas = Image.new("RGB", (W, H), (30, 30, 30))
d = ImageDraw.Draw(canvas)
if a.title:
    d.text((pad, 4), a.title, fill=(230, 230, 230))
for i, item in enumerate(a.items):
    caption, path = item.split("=", 1)
    im = Image.open(path).convert("RGB")
    im.thumbnail((a.tile, a.tile))
    x = pad + i * (a.tile + pad)
    canvas.paste(im, (x, top + pad))
    d.text((x, top + pad + a.tile + 4), caption, fill=(230, 230, 230))
canvas.save(a.out)
print("MONTAGE_OK", a.out)
