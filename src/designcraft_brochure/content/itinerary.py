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
_BULLET_CHARS = re.compile(r"^[\s•·∙●◦▪►\-–—*]+")
_LEAD_JUNK = re.compile(
    r"^(?:(?:\d+\s*[;.)]\s*)|(?:(?:\d+\s+)?[A-Za-z]{2,12}\.?\s+\d+(?::\d+)?\)\s*))+",
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_POINT_FILLER = re.compile(
    r"^(?:you will be|you will|we will|we'll|afterwards we|afterwards|then we|then|guests will)\s+",
    re.I,
)
_TITLE_FILLERS = (
    "have the chance for an optional ",
    "have the chance for an ",
    "have the chance for a ",
    "have the chance to ",
    "have the chance for ",
    "you will have the chance to ",
    "enjoy your time at ",
    "enjoy your time in ",
    "enjoy your time ",
    "together and time to ",
    "visit the ",
    "explore the ",
    "see the ",
)
_WEAK_START = {"a", "an", "the", "and", "or", "to", "of", "in", "at", "on", "for", "with", "our", "your", "we", "then"}
_WEAK_END = {"a", "an", "the", "and", "or", "to", "of", "in", "at", "on", "for", "with", "our", "your"}
# The day title frame holds one line. A longer title paints over the details under it.
TITLE_LIMIT = 26
POINT_LIMIT = 34
MAX_POINTS = 3
_DANGLING = {"beautiful", "famous", "ancient", "lovely", "great", "special", "nearby", "local"}
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


def format_day_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """One capital line that fits the day title frame, like DEPART THE USA."""
    text = _clean_line(title).replace("“", '"').replace("”", '"').replace("’", "'")
    stripped = _LEAD_JUNK.sub("", text).strip(" :-\"'")
    if len(stripped) >= 4:
        text = stripped
    text = text.replace('"', "").strip()
    clauses = [part.strip() for part in text.split(".") if part.strip()]
    if len(clauses) >= 2 and len(clauses[0].split()) <= 2 and len(" ".join(clauses[1:])) > len(clauses[0]):
        text = " ".join(clauses[1:])
    text = text.strip(" .")
    bare = re.sub(r"\s*\([^)]*\)", "", text).strip(" .")
    candidates = [text]
    if bare and bare.lower() != text.lower():
        candidates.append(bare)
    for source in (text, bare):
        folded = source.lower()
        for filler in _TITLE_FILLERS:
            if folded.startswith(filler):
                rest = source[len(filler) :].strip(" .")
                if len(rest) >= 4:
                    candidates.append(rest)
    fitting = [item for item in candidates if 4 <= len(item) <= limit]
    solid = [item for item in fitting if item.split()[0].lower().strip(".,;:") not in _WEAK_START]
    if solid or fitting:
        chosen = max(solid or fitting, key=len)
    else:
        chosen = _fit_ending(max(candidates, key=len), limit) or _fit_words(max(candidates, key=len), limit)
    return " ".join(chosen.split()).upper()


def format_day_points(body: str, *, limit: int = POINT_LIMIT, maximum: int = MAX_POINTS) -> str:
    """A few short activities. A paragraph is split so it cannot run into the next day."""
    pieces: list[str] = []
    for raw in body.replace("\u2028", "\n").splitlines():
        line = _clean_line(raw)
        if not line:
            continue
        for sentence in _SENTENCE.split(line):
            sentence = sentence.strip()
            if sentence:
                pieces.extend(part.strip() for part in re.split(r"\s+(?=Afterwards\b|Then\b)", sentence, flags=re.I) if part.strip())
    points: list[str] = []
    for piece in pieces:
        finished = piece.rstrip().endswith((".", "!", "?"))
        original = piece.strip()
        cut = False
        if len(original) > limit:
            shortened = _POINT_FILLER.sub("", original).strip()
            if shortened and len(shortened) < len(original):
                original = shortened
                cut = True
        if len(original) > limit:
            fitted = _fit_words(original, limit)
            cut = fitted != original.strip(" .")
            original = fitted
        original = original.strip()
        if cut:
            original = original.strip(" .")
            last = original.split()[-1].lower().strip(".,;:") if original else ""
            if not finished and last in _DANGLING:
                continue
        if len(original) < 3:
            continue
        points.append(original)
        if len(points) >= maximum:
            break
    return "\n".join(f"•\t{point}" for point in points)


def present_day(title: str, body: str, *, tight: bool = False) -> tuple[str, str]:
    """The title and points that will be printed. Tight is the shorter pass after Jev asks for one."""
    title_limit = 18 if tight else TITLE_LIMIT
    point_limit = 28 if tight else POINT_LIMIT
    maximum = 2 if tight else MAX_POINTS
    return (
        format_day_title(title, title_limit),
        format_day_points(body, limit=point_limit, maximum=maximum),
    )


def _clean_line(line: str) -> str:
    text = " ".join(line.split())
    while True:
        nxt = _BULLET_CHARS.sub("", text).strip()
        if nxt == text:
            return text.strip(" \t-")
        text = nxt


def _fit_ending(text: str, limit: int) -> str:
    """The place at the end of a long title, when the whole line cannot fit."""
    words = [word for word in text.split() if word]
    for start, word in enumerate(words):
        if word.lower().strip(".,;:") in _WEAK_START:
            continue
        phrase = " ".join(words[start:]).strip(" .")
        if len(phrase) <= limit:
            return phrase
    return ""


def _fit_words(text: str, limit: int) -> str:
    """The longest phrase that fits, starting and ending on a real word."""
    words = [word for word in text.split() if word]
    best = ""
    for start, word in enumerate(words):
        if word.lower().strip(".,;:") in _WEAK_START:
            continue
        chunk: list[str] = []
        for later in words[start:]:
            trial = " ".join([*chunk, later])
            if len(trial) > limit:
                break
            chunk.append(later)
        while chunk and chunk[-1].lower().strip(".,;:") in _WEAK_END:
            chunk.pop()
        phrase = " ".join(chunk).strip(" .")
        if len(phrase) > len(best):
            best = phrase
    if best:
        return best
    cut = " ".join(text.split())[:limit].rsplit(" ", 1)[0]
    return cut.strip(" .") or " ".join(text.split())[:limit].strip(" .")


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
