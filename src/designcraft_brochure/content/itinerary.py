"""Itinerary days and tour photos taken from the proposal, never invented."""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path

_DAY = re.compile(r"^day\s+(\d{1,2})\b[:.\-\s]*(.*)$", re.I)
_STOP = re.compile(
    r"^(payment|inclusion|exclusion|what'?s included|not included|terms|pricing|prices|deposit|"
    r"hotel|accommodation|optional)\b",
    re.I,
)
_BULLET = re.compile(r"^[-*•]\s*")
_MIN_PHOTO_EDGE = 350


@dataclass(frozen=True)
class ItineraryDay:
    number: int
    title: str
    body: str


def parse_itinerary(source: str) -> list[ItineraryDay]:
    """Day headings and the lines under them, in day-number order.

    A heading is its own line, such as "Day 2: Jerusalem". The list stops at
    the next section (payments, inclusions, hotels). Fewer than two days is
    not treated as an itinerary.
    """
    days: list[ItineraryDay] = []
    number: int | None = None
    title = ""
    body: list[str] = []

    def finish() -> None:
        nonlocal number, title, body
        if number is None:
            return
        cleaned_title = " ".join(title.split()).strip(" :-")
        cleaned_body = "\n".join(line for line in body if line.strip())
        if cleaned_title:
            days.append(ItineraryDay(number, cleaned_title[:80], cleaned_body[:700]))
        number = None
        title = ""
        body = []

    for raw in source.replace("\u2028", "\n").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if number is not None and _STOP.match(line):
            break
        match = _DAY.match(line)
        if match:
            finish()
            found = int(match.group(1))
            if not 1 <= found <= 16:
                number = None
                continue
            number = found
            title = match.group(2).strip(" :-")
            continue
        if number is None:
            continue
        if not title and len(line) <= 70 and not _BULLET.match(line):
            title = line
            continue
        body.append(_BULLET.sub("", line).strip() or line)

    finish()
    ordered = sorted(days, key=lambda day: day.number)
    unique: list[ItineraryDay] = []
    seen: set[int] = set()
    for day in ordered:
        if day.number in seen:
            continue
        seen.add(day.number)
        unique.append(day)
    return unique if len(unique) >= 2 else []


def format_day_title(title: str) -> str:
    """The brochure prints the day name in capitals, like DEPART THE USA."""
    return " ".join(title.split()).upper()[:80]


def format_day_points(body: str) -> str:
    """Turn the day's lines into the brochure's bullet list."""
    points: list[str] = []
    for raw in body.replace("\u2028", "\n").splitlines():
        line = _BULLET.sub("", " ".join(raw.split())).strip(" \t-")
        if line:
            points.append(f"•\t{line}")
    return "\n".join(points[:8])


def tour_photo_paths(paths: list[str]) -> list[str]:
    """Proposal pictures large enough to be a tour photo, one file per image."""
    kept: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen:
            continue
        size = _image_size(path)
        if size is None or min(size) < _MIN_PHOTO_EDGE:
            continue
        seen.add(digest)
        kept.append(str(path))
    return kept


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        if marker == 0xD8 or marker == 0xD9:
            index += 2
            continue
        if index + 4 > len(data):
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + length
    return None
