# SPDX-License-Identifier: GPL-3.0-or-later
"""Values shared by the converter (inside Blender) and the server (outside).

The asset metadata in the listing and inside the converted .blend must match:
after a download Blender shows what it reads from the file.
"""

CONVERTER_VERSION = "3.1"  # bump when the converter output changes: every asset becomes "updatable"
BLENDER_VERSION = "5.2"
AUTHOR = "NVIDIA"
LICENSE = "CC-BY-4.0"
COPYRIGHT = "© NVIDIA Corporation"
HF_TREE_URL = "https://huggingface.co/datasets/nvidia/simready-assets/tree/main/{}"


def asset_description(asset_id: str) -> str:
    return f"NVIDIA SimReady asset '{asset_id}'.\n{HF_TREE_URL.format(asset_id)}"


def asset_tags(asset_id: str) -> list[str]:
    return [asset_id.split("_")[0], "simready"]
