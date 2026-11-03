# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper for test_color_jpeg.py, run inside Blender: blender -b --python this -- <dir> <result.json>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import OpenImageIO as oiio  # noqa: E402

from simready.convert import convert_asset as ca  # noqa: E402

d, out = (Path(a) for a in sys.argv[sys.argv.index("--") + 1:][:2])
rng = np.random.default_rng(3)


def png(name, px):
    path = d / name
    o = oiio.ImageOutput.create(str(path))
    o.open(str(path), oiio.ImageSpec(px.shape[1], px.shape[0], px.shape[2], oiio.UINT8))
    o.write_image(np.ascontiguousarray(px))
    o.close()
    return path


noise = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8) // 4 + 96   # textured, compresses badly as PNG
flat = np.full((256, 256, 3), 128, np.uint8)                            # flat: PNG is tiny
alpha = np.dstack([noise, np.where(np.arange(256)[None, :] < 128, 255, 0).repeat(256, 0).astype(np.uint8)])
opaque4 = np.dstack([noise, np.full((256, 256), 255, np.uint8)])
res = {}
for name, px, size in (("noise_alb.png", noise, (256, 256)), ("noise_small_alb.png", noise, (128, 128)),
                       ("flat_alb.png", flat, (256, 256)), ("alpha_alb.png", alpha, (256, 256)),
                       ("opaque4_alb.png", opaque4, (256, 256))):
    src = png(name, px)
    tmp = d / "tmp"
    tmp.mkdir(exist_ok=True)
    png_bytes = src.stat().st_size * size[0] * size[1] / (px.shape[0] * px.shape[1])
    j = ca._color_jpeg(src, size, png_bytes, tmp)
    r = {"jpeg": bool(j)}
    if j:
        spec = oiio.ImageInput.open(str(j)).spec()
        r.update(size=[spec.width, spec.height], channels=spec.nchannels, bytes=j.stat().st_size,
                 png=src.stat().st_size, first=j.read_bytes()[:3].hex())
        r["same_again"] = ca._color_jpeg(src, size, png_bytes, d).read_bytes() == j.read_bytes()
    res[name] = r
out.write_text(json.dumps(res))
print("COLOR_JPEG_OK")
