"""Put the brand marks and photos back into empty picture frames.

An .indd links its pictures by path (a Dropbox folder, an Adobe library, a
Downloads folder on the designer's PC). Once the template is converted and
opened here those files are gone, so every photo and logo frame is empty.

`assets/manifest.json` lists the pictures that belong in the Travel With
Friends brochure and the page position of the frame each one fills. Only
frames that line up with a slot are touched. The files come from the project's
own `assets` folder, never from the proposal or the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_OFFSET = 30.0
_FITS = {"fillProportionally", "fitProportionally"}


def default_assets_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "assets"


def load_slots(assets_dir: Path) -> list[dict]:
    manifest = assets_dir / "manifest.json"
    if not manifest.is_file():
        return []
    slots = json.loads(manifest.read_text(encoding="utf-8")).get("slots", [])
    usable = []
    for slot in slots:
        bounds = slot.get("bounds")
        file = assets_dir / str(slot.get("file", ""))
        if (
            isinstance(bounds, list)
            and len(bounds) == 4
            and slot.get("fit") in _FITS
            and file.is_file()
            and assets_dir.resolve() in file.resolve().parents
        ):
            usable.append({**slot, "path": file.resolve()})
    return usable


def match_frames(slots: list[dict], frames: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each slot with the unused picture frame whose edges are closest."""
    pairs = []
    for slot in slots:
        for frame in frames:
            offset = max(abs(a - b) for a, b in zip(slot["bounds"], frame["bounds"]))
            if offset <= MAX_OFFSET:
                pairs.append((offset, slot["name"], slot, frame))
    pairs.sort(key=lambda item: (item[0], item[1]))
    used_slots: set[str] = set()
    used_frames: set[int] = set()
    chosen = []
    for _offset, name, slot, frame in pairs:
        if name in used_slots or frame["id"] in used_frames:
            continue
        used_slots.add(name)
        used_frames.add(frame["id"])
        chosen.append((slot, frame))
    return chosen


def picture_frames(info: dict) -> list[dict]:
    found: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "<image>" and "bounds" in node and "id" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(info.get("spreads", []))
    return found


def place_brand_images(client: Any, info: dict, assets_dir: Path | None = None) -> list[str]:
    root = (assets_dir or default_assets_dir()).expanduser()
    slots = load_slots(root)
    if not slots:
        return []
    notes: list[str] = []
    for slot, frame in match_frames(slots, picture_frames(info)):
        frame_id = int(frame["id"])
        client.call(
            "engine.execute",
            {"command": "file.place", "params": {"path": str(slot["path"]), "frame": frame_id}},
        )
        client.call(
            "engine.execute",
            {"command": "object.fit", "params": {"mode": slot["fit"], "ids": [frame_id]}},
        )
        notes.append(f"placed {slot['name']}")
    return notes
