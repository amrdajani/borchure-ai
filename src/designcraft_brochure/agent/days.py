"""Reorder the itinerary days and lay the page out again.

Each day on the itinerary page is three pieces: a "DAY 3" label, a title, and
a list of bullets. The labels count 1, 2, 3 down the page and stay put. A
reorder moves each day's title and bullets to a new slot. Moving them leaves
the page wrong in three ways, so this module also:

* stacks the days again with an even gap, because a short day moved into a
  long day's place leaves a hole and the reverse overlaps the next day;
* sizes each bullet frame to its text, so no bullet is cut off;
* gives a day that crosses to the other column that column's colours.

If the days cannot fit with the usual gap, the gap tightens, then the bullet
text steps down half a point at a time. If it still cannot fit, nothing
changes and the person is told.
"""

from __future__ import annotations

import re
from typing import Any

from designcraft_brochure.craft import polish

TITLE_TO_BODY = 2.0
GAPS = (7.0, 6.0, 5.0, 4.0)
SIZES = (11.0, 10.5, 10.0, 9.5)
LINE_AT_11 = 13.85  # one line of DesignCraft's auto leading at 11 pt
BOTTOM_MARGIN = 20.0
_LABEL = re.compile(r"DAY\s+(\d+)", re.I)
_FILL = re.compile(r"pt\s+(.+)$")


class DaysError(Exception):
    """The days cannot be reordered. The message is safe to show to the person."""


def find_days(stories: list[dict]) -> list[dict]:
    """The day blocks on the page that has them, in slot order. Empty when the page has none."""
    by_page: dict[int, list[dict]] = {}
    for story in stories:
        by_page.setdefault(story["page"], []).append(story)
    for page_stories in by_page.values():
        labels = [s for s in page_stories if _LABEL.fullmatch(" ".join(s["text"].split()))]
        if len(labels) < 3:
            continue
        blocks = []
        for label in labels:
            number = int(_LABEL.fullmatch(" ".join(label["text"].split())).group(1))  # type: ignore[union-attr]
            row = [
                s
                for s in page_stories
                if s is not label and abs(s["bounds"][1] - label["bounds"][1]) <= 4 and s["bounds"][0] > label["bounds"][2] - 5
            ]
            if not row:
                continue
            title = min(row, key=lambda s: s["bounds"][0])
            below = [
                s
                for s in page_stories
                if s is not title
                and s is not label
                and abs(s["bounds"][0] - title["bounds"][0]) <= 3
                and 0 <= s["bounds"][1] - title["bounds"][3] <= 6
            ]
            body = min(below, key=lambda s: s["bounds"][1]) if below else None
            blocks.append({"day": number, "label": label, "title": title, "body": body, "page": label["page"]})
        if len(blocks) >= 3:
            return sorted(blocks, key=lambda b: b["day"])
    return []


def describe_days(stories: list[dict]) -> list[dict]:
    """What the model is told about the days: the number, the title and the bullets."""
    return [
        {
            "day": block["day"],
            "title": " ".join(block["title"]["text"].split()),
            "bullets": [line for line in (block["body"]["text"].split("\n") if block["body"] else []) if line.strip()][:12],
        }
        for block in find_days(stories)
    ]


def _fill_of(story: dict) -> str | None:
    found = _FILL.search(story.get("style") or "")
    return found.group(1) if found else None


def _column(block: dict) -> int:
    return round(block["label"]["bounds"][0])


def reorder_days(doc: Any, snapshot: dict, order: list[int]) -> list[str]:
    """Show the days in `order` (the day numbers, top to bottom). Raises DaysError if they cannot fit."""
    doc.inspect()
    blocks = find_days(snapshot["stories"])
    if not blocks:
        raise DaysError("I can't find a list of days in this brochure.")
    numbers = [block["day"] for block in blocks]
    if sorted(order) != sorted(numbers):
        raise DaysError(f"The days are {', '.join(map(str, numbers))}. Name each one once.")
    if order == numbers:
        return []

    by_day = {block["day"]: block for block in blocks}
    columns = sorted({_column(block) for block in blocks})
    slots = {column: [b["day"] for b in blocks if _column(b) == column] for column in columns}
    palette = {column: _palette([by_day[d] for d in numbers_in]) for column, numbers_in in slots.items()}
    page_width, page_height = snapshot["page_size"]
    original_size = _body_size(blocks)

    placed = {block["day"]: _current(doc, block) for block in blocks}
    limits = {column: _limit(snapshot, blocks, by_day, slots[column], page_height) for column in columns}

    chosen: tuple[float, float, dict[int, float]] | None = None
    for size in SIZES:
        heights = _measure(doc, blocks, placed, size, original_size)
        for gap in GAPS:
            plan = _layout(blocks, slots, order, numbers, heights, gap, snapshot)
            if all(plan["bottom"][column] <= limits[column] for column in columns):
                chosen = (size, gap, heights)
                break
        if chosen:
            break
    if chosen is None:
        raise DaysError("Those days are too long to fit in that order, even with smaller text.")

    size, gap, heights = chosen
    plan = _layout(blocks, slots, order, numbers, heights, gap, snapshot)
    _apply(doc, blocks, by_day, slots, order, numbers, plan, palette, heights, placed)
    notes = ["days reordered"]
    if size < original_size - 0.01:
        notes.append(f"itinerary text set to {size:g} pt to fit")
    return notes


def _body_size(blocks: list[dict]) -> float:
    for block in blocks:
        if block["body"]:
            found = re.search(r"([\d.]+)pt", block["body"].get("style") or "")
            if found:
                return float(found.group(1))
    return 11.0


def _palette(blocks: list[dict]) -> dict:
    """The title and bullet colours this column uses."""
    def mode(values: list[str | None]) -> str | None:
        clean = [v for v in values if v]
        return max(set(clean), key=clean.count) if clean else None

    return {
        "title": mode([_fill_of(b["title"]) for b in blocks]),
        "body": mode([_fill_of(b["body"]) for b in blocks if b["body"]]),
    }


def _current(doc: Any, block: dict) -> dict:
    """Live bounds of each frame in the block, by frame id."""
    frames = {}
    for key in ("label", "title", "body"):
        part = block[key]
        if part is None:
            continue
        frame = doc.frame(part["frame"])
        frames[key] = list(frame["bounds"]) if frame else list(part["bounds"])
    return frames


def _limit(snapshot: dict, blocks: list[dict], by_day: dict, column_days: list[int], page_height: float) -> float:
    """Lowest y the column's text may reach: above anything sitting below it, or the page margin."""
    column_blocks = [by_day[d] for d in column_days]
    x0 = min(b["title"]["bounds"][0] for b in column_blocks)
    x1 = max(b["title"]["bounds"][2] for b in column_blocks)
    top = min(b["label"]["bounds"][1] for b in column_blocks)
    page = column_blocks[0]["page"]
    in_blocks = {id(b[k]) for b in blocks for k in ("label", "title", "body") if b[k]}
    below: list[float] = []
    for item in [*snapshot["shapes"], *snapshot["pictures"], *snapshot["stories"]]:
        if item["page"] != page or id(item) in in_blocks:
            continue
        ix0, iy0, ix1, _iy1 = item["bounds"]
        if ix1 > x0 and ix0 < x1 and iy0 > top + 100:
            below.append(iy0 - 8)
    return min([page_height - BOTTOM_MARGIN, *below])


def _measure(doc: Any, blocks: list[dict], placed: dict, size: float, original_size: float) -> dict[int, float]:
    """Height each day's bullets need, found by letting each frame grow tall and counting its lines."""
    heights: dict[int, float] = {}
    for block in blocks:
        body = block["body"]
        if body is None:
            continue
        frame_id, story_id = body["frame"], body["story"]
        bounds = placed[block["day"]]["body"]
        if abs(size - original_size) > 0.01:
            length = len(str(doc.story(story_id).get("text") or "").encode("utf-8"))
            doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
            doc.run("type.char", {"attrs": {"size": size}})
        tall = [bounds[0], bounds[1], bounds[2], bounds[1] + 500]
        doc.run("transform.resize", {"ids": [frame_id], "from": bounds, "to": tall})
        placed[block["day"]]["body"] = tall
        doc.inspect()
        story = next(s for s in doc.stories() if int(s["id"]) == story_id)
        text = str(doc.story(story_id).get("text") or "")
        # A template can end a list with empty paragraphs. They take no room on the page.
        trailing = len(text) - len(text.rstrip("\n"))
        lines = max(1, int(story.get("lines") or 1) - trailing)
        height = lines * LINE_AT_11 * (size / 11.0) + 1.0
        for _ in range(8):
            doc.run("transform.resize", {"ids": [frame_id], "from": placed[block["day"]]["body"], "to": [bounds[0], bounds[1], bounds[2], bounds[1] + height]})
            placed[block["day"]]["body"] = [bounds[0], bounds[1], bounds[2], bounds[1] + height]
            doc.inspect()
            now = next(s for s in doc.stories() if int(s["id"]) == story_id)
            if not now.get("overset") or int(now.get("lines") or 0) >= lines:
                break
            height += 0.7
        heights[block["day"]] = round(height, 2)
    return heights


def _layout(
    blocks: list[dict],
    slots: dict[int, list[int]],
    order: list[int],
    numbers: list[int],
    heights: dict[int, float],
    gap: float,
    snapshot: dict,
) -> dict:
    """Row y for every slot. Slot `d` shows the content of day order[position of d]."""
    by_day = {block["day"]: block for block in blocks}
    rows: dict[int, float] = {}
    bottom: dict[int, float] = {}
    for column, column_days in slots.items():
        y = min(by_day[d]["label"]["bounds"][1] for d in column_days)
        for slot_day in column_days:
            content_day = order[numbers.index(slot_day)]
            content = by_day[content_day]
            rows[slot_day] = y
            title_height = content["title"]["bounds"][3] - content["title"]["bounds"][1]
            end = y + title_height
            if content["body"] is not None:
                end = y + title_height + TITLE_TO_BODY + heights[content_day]
            bottom[column] = end
            y = end + gap
    return {"rows": rows, "bottom": bottom}


def _apply(
    doc: Any,
    blocks: list[dict],
    by_day: dict,
    slots: dict[int, list[int]],
    order: list[int],
    numbers: list[int],
    plan: dict,
    palette: dict,
    heights: dict[int, float],
    placed: dict,
) -> None:
    for column, column_days in slots.items():
        for slot_day in column_days:
            content_day = order[numbers.index(slot_day)]
            content, slot = by_day[content_day], by_day[slot_day]
            y = plan["rows"][slot_day]
            _move_to(doc, slot["label"]["frame"], placed[slot_day]["label"], slot["label"]["bounds"][0], y)
            target_x = slot["title"]["bounds"][0]
            _move_to(doc, content["title"]["frame"], placed[content_day]["title"], target_x, y)
            if content["body"] is not None:
                body_y = y + (content["title"]["bounds"][3] - content["title"]["bounds"][1]) + TITLE_TO_BODY
                _move_to(doc, content["body"]["frame"], placed[content_day]["body"], target_x, body_y)
            if _column(content) != column:
                _recolour(doc, content["title"]["story"], palette[column]["title"])
                if content["body"] is not None:
                    _recolour(doc, content["body"]["story"], palette[column]["body"])


def _move_to(doc: Any, frame_id: int, bounds: list[float], x: float, y: float) -> None:
    dx, dy = round(x - bounds[0], 3), round(y - bounds[1], 3)
    if abs(dx) > 0.01 or abs(dy) > 0.01:
        doc.run("transform.move", {"dx": dx, "dy": dy, "ids": [frame_id]})
    bounds[0:4] = [bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy]


def _recolour(doc: Any, story_id: int, swatch: str | None) -> None:
    if not swatch:
        return
    length = len(str(doc.story(story_id).get("text") or "").encode("utf-8"))
    doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
    doc.run("type.char", {"attrs": {"fill": swatch}})


__all__ = ["DaysError", "describe_days", "find_days", "reorder_days", "polish"]
