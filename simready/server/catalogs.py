# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset catalogs from the NVIDIA name prefix (editable table in catalogs.json).

Catalog UUIDs are uuid5(path): the same path always gets the same UUID, so
renaming a group only moves the assets, it never breaks a downloaded .blend
whose catalog path still exists.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path

CATALOG_NS = uuid.UUID("2b9c1f3e-6a57-4f0e-9c1d-5e1d2e9a5172")  # same namespace as phase 2
DEFAULT_TABLE = Path(__file__).with_name("catalogs.json")
OTHER = "Other"


def catalog_uuid(path: str) -> str:
    return str(uuid.uuid5(CATALOG_NS, path))


def _label(token: str, labels: dict[str, str]) -> str:
    if token in labels:
        return labels[token]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token).split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or token


class Catalogs:
    def __init__(self, table: dict) -> None:
        self.root = table.get("root", "SimReady")
        self.labels = table.get("labels", {})
        self.split = set(table.get("split", []))
        self.split_min = int(table.get("split_min", 3))
        self.group_of: dict[str, str] = {}
        for group, prefixes in table.get("groups", {}).items():
            for p in prefixes:
                self.group_of.setdefault(p, group)

    @classmethod
    def load(cls, path: Path = DEFAULT_TABLE) -> Catalogs:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def assign(self, asset_ids: list[str]) -> dict[str, str]:
        """asset id -> catalog path, for the whole dataset at once (sub-catalogs need counts)."""
        second = Counter((a.split("_")[0], a.split("_")[1]) for a in asset_ids if a.count("_") >= 2)
        out = {}
        for a in asset_ids:
            parts = a.split("_")
            prefix = parts[0]
            path = [self.root, self.group_of.get(prefix, OTHER), _label(prefix, self.labels)]
            if prefix in self.split and len(parts) >= 3 and second[(prefix, parts[1])] >= self.split_min:
                path.append(_label(parts[1], self.labels))
            out[a] = "/".join(path)
        return out

    @staticmethod
    def listing_entries(paths) -> list[dict]:
        """Catalog definitions for the listing, including every parent path."""
        seen: dict[str, None] = {}
        for p in sorted(set(paths)):
            parts = p.split("/")
            for n in range(1, len(parts) + 1):
                seen.setdefault("/".join(parts[:n]), None)
        return [{"path": p, "simple_name": p.replace("/", "-"), "uuids": [catalog_uuid(p)]} for p in seen]
