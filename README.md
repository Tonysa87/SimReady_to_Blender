# SimReady to Blender

The ~2,700 **NVIDIA SimReady** assets ([`nvidia/simready-assets`](https://huggingface.co/datasets/nvidia/simready-assets),
CC-BY-4.0) directly in **Blender 5.2's Asset Browser**, as a remote asset library like any other.
All assets show up right away, with thumbnails and catalogs; when you download one, it is fetched
from Hugging Face and **converted to .blend on the spot**.

> Status: **0.5.0** (0.4.1 tested on Blender 5.2, Windows 11). Experimental. Made with Claude.
> Download the add-on from the [Releases](https://github.com/Tonysa87/SimReady_to_Blender/releases).

## Credits

All 3D assets are by **NVIDIA Corporation**: the
[**NVIDIA SimReady Assets**](https://huggingface.co/datasets/nvidia/simready-assets) dataset
(`nvidia/simready-assets` on Hugging Face), released under the
[**Creative Commons Attribution 4.0 International (CC-BY-4.0)**](https://creativecommons.org/licenses/by/4.0/) license.

- The assets are **not included** in this repository or in the add-on: they are downloaded from
  Hugging Face to the user's computer when needed.
- **Changes made** (as required by CC-BY-4.0): USD files are converted to `.blend`;
  OmniPBR/OmniGlass/UsdPreviewSurface materials are translated to Principled BSDF; textures can be
  downscaled (2K/1K); physics variants and USD instancing are removed; thumbnails are resized to
  256 px; single-mesh assets take the asset's name.
- This project is not affiliated with or endorsed by NVIDIA.
- Every converted asset carries author (NVIDIA), license (CC-BY-4.0), copyright and a link to its
  Hugging Face folder in its Blender asset metadata.
- Trademarks and logos visible on the assets are not covered by the license.
- `simready/server/data/seed_index.json.gz` contains only metadata derived from the dataset
  (folder names, file sizes and SHA-256 digests), under the same license and attribution.

## Installation

1. Blender 5.2 → *Edit → Preferences → Get Extensions → ⌄ (top right) → Install from Disk…* →
   `simready_library-<version>.zip`.
2. *Preferences → System*: enable **Allow Online Access**.
3. In the add-on preferences choose a **Data Folder** (index, thumbnails and downloaded assets),
   then restart Blender once (this also moves Blender's own download cache there).
4. Asset Browser → **NVIDIA SimReady** library: download an asset with the button on its tile,
   then drag it into the scene.

### Preferences

| Option | What it does |
|---|---|
| Data Folder | Where everything is stored: server index, thumbnails, Blender's cache of downloaded assets |
| Texture Size | Max (original, up to 4K) / 2K / 1K, applied when an asset is converted |
| JPEG Color Textures | Off by default. On: opaque albedo/emission textures are packed as JPEG (quality 95), .blend files ~25% smaller with no visible difference; normal, roughness and other data textures always stay PNG |
| Import | *Pack* (Blender's default) or *Append, editable* (instances made real, SimReady data made local) |
| Prepare all thumbnails | Builds every thumbnail in the background (~1.3 GB from Hugging Face once, ~25 MB on disk) |
| Start automatically / Port | The local server starts with Blender on `127.0.0.1:8765` |
| Hugging Face Token | Optional, only for higher download rate limits; not needed |

### Good to know

- The first download of an asset takes from a few seconds to a few minutes (download +
  conversion). Big assets can be hundreds of MB, mostly because of 4K textures: use 2K or 1K if
  you don't need them.
- The conversion progress is shown in Blender's status bar; the listing refreshes by itself
  afterwards (no Shift+Refresh needed).
- Drag & drop of an asset that is not downloaded yet is blocked by Blender itself: download it
  first with the button on its tile.
- The biggest assets (~1 GB of 4K textures) need 4-5 GB of free RAM while they are converted at
  Max; at 2K about 2 GB. The server checks the free disk space before downloading and tells you
  how much is needed.
- If the connection drops, downloads resume where they stopped; errors (offline, Hugging Face
  rate limit, disk full...) are shown in the status bar and in the add-on preferences.
- Converting the same asset again gives the very same file (same hash), so a copy already in
  Blender's cache stays valid.

## How it works

```
Blender (Asset Browser, remote library)
   │  GET _asset-library-meta.json, _v1/*.json, thumbs/*, blend/*.blend?hash=...
   ▼
Local server 127.0.0.1:8765 (Blender's own Python: stdlib + pxr + OpenImageIO)
   ├── listing built from the NVIDIA index ("placeholder" hashes for .blend files not converted yet)
   ├── first request of a .blend: download from Hugging Face → headless conversion → response
   └── /_status: the add-on reloads the listing with the real hash → asset "verified" in Blender
```

Conversions longer than Blender's 300 s download timeout are streamed as gzip with empty deflate
blocks as keep-alive, so even the biggest assets arrive in a single request.

The conversion (`simready/convert/convert_asset.py`, run by `blender -b`):

1. a USD "wrapper" layer: physics variants disabled, USD instances turned into regular prims;
2. Blender's native USD import for geometry, UVs and scale (meters);
3. **materials converted by this project** through `bpy.types.USDHook`: OmniPBR, OmniGlass and
   UsdPreviewSurface → an intermediate description with **OpenPBR** names → Principled BSDF.
   The semantics follow NVIDIA's MDL code (tint, influences, ORM, opacity); SimReady normal maps
   are **DirectX** (verified) and use the Normal Map node in DirectX mode;
4. single-mesh assets → **Object** (named as the asset), multi-part assets → **Collection**
   (parts keep their NVIDIA names);
5. textures resized to Max/2K/1K and packed; NVIDIA metadata and thumbnail embedded.

## Repository layout

| Path | Content |
|---|---|
| `addon/` | Blender add-on (preferences, server start, library, cache, editable import) |
| `simready/convert/` | USD → .blend converter |
| `simready/server/` | remote library server: HF index, catalogs, thumbnails, conversions, listing |
| `simready/server/data/seed_index.json.gz` | ready index of the 2,677 assets (NVIDIA file names, sizes, SHA-256, CC-BY-4.0); rebuilt with `tools/make_seed_index.py` |
| `tests/` | tests for materials, server, structure prediction and cache link |
| `tools/build_extension.py` | builds the extension .zip in `dist/` |
| `tools/render_check.py`, `tools/montage.py` | Cycles renders of a converted asset and side-by-side comparisons |
| `tools/gui_test_addon.py` | GUI test of the installed add-on (Blender with interface) |
| `tools/validate_structure.py` | checks the Object/Collection prediction against the real converter |
| `tools/measure_jpeg.py` | PNG vs JPEG size and quality on albedo textures |
| `tools/make_seed_index.py` | rebuilds the seed index from a server's index |
| (git tag `v0.4.1`) | the phase 1-4 prototypes and dataset reconnaissance scripts cited in `docs/` |
| `docs/` | development reports and research notes (in Italian) |
| `SEGNALIBRO.md`, `CLAUDE.md` | development notes for the author and Claude Code (in Italian) |

## Development (Linux/WSL)

Blender 5.2 Linux in `~/blender/blender-5.2.2-linux-x64/blender` (`$B` below).

```bash
# Tests (the pxr ones also run with Blender's Python)
python3 -m unittest discover -s tests
~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 -s -E -m unittest discover -s tests

# Convert an asset already downloaded in cache/raw/<asset>
$B -b --factory-startup --python simready/convert/convert_asset.py -- \
   --asset-dir cache/raw/food_banana_a01 --out out/test/banana.blend \
   --max-res 2048 --report out/test/banana.json

# Run the server by hand with Blender's Python
PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
$PY -s -E -m simready.server --state-dir <folder> --port 8790 --max-res 2048 --blender $B

# Build the add-on zip
python3 tools/build_extension.py
```

To use a hand-started server in Blender: *Preferences → File Paths → Asset Libraries → + →
Add Remote Asset Library*, URL `http://127.0.0.1:<port>/` (exactly, no spaces). Only one library
per URL, and don't edit it during a session (see the Blender 5.2 behaviours documented in
`docs/fase1-report.md`).

## License

- Code: GPL-3.0-or-later (see `LICENSE`).
- Assets: © NVIDIA Corporation, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/), not
  included in this repository (downloaded from Hugging Face on the user's computer). The seed
  index in `simready/server/data/` contains only metadata derived from the NVIDIA dataset, under
  the same license and attribution. Trademarks and logos on the assets are not covered by the license.
