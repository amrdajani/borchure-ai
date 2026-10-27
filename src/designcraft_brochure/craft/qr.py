"""The register QR code on the cover points at the host-tour page for this tour."""

from __future__ import annotations

import re
from pathlib import Path

import segno

REGISTER_BASE = "https://www.travelwithfriends.com/host-tour/"
_CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{1,23}")


def register_url(tour_code: str) -> str | None:
    """https://www.travelwithfriends.com/host-tour/<tour code, lower case>, or None for an unusable code."""
    code = tour_code.strip().upper()
    if not _CODE.fullmatch(code):
        return None
    return REGISTER_BASE + code.lower()


def write_qr_png(url: str, destination: Path) -> Path:
    """A black-on-white QR with a one-module quiet zone, sharp enough to print."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    segno.make(url, error="m", micro=False).save(str(destination), kind="png", scale=16, border=1)
    return destination
