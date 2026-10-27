"""Finishing pass for a filled brochure.

`story.setText` replaces a whole story and keeps only the style of its first
character. A host panel that was a light 18 pt title above bold 11 pt lines
comes back as five light 18 pt lines. The price bars also keep the template's
frame sizes, so text sits off-centre or runs past the bar.

This pass puts the look back. It runs once, after every fill and before the
first save or export. It sends only formatting commands. It never writes text
that is not already in the approved plan, apart from the cover line that names
the host, which is built from the approved host line.

The numbers below are the ones the approved HLMTC1127 brochure uses.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from designcraft_brochure.craft.assets import picture_frames, place_brand_images
from designcraft_brochure.craft.qr import register_url, write_qr_png
from designcraft_brochure.models import BrochurePlan

YELLOW = "C=3 M=20 Y=99 K=0"

TITLE = {"fontFamily": "Montserrat", "fontStyle": "Regular", "size": 18, "fill": "[Paper]", "capitalization": "normal", "tracking": 0}
BODY = {"fontFamily": "Montserrat", "fontStyle": "Bold", "size": 11, "fill": "[Paper]", "capitalization": "normal", "tracking": 0}
BAR_TEXT = {"fontFamily": "Montserrat", "fontStyle": "Bold", "size": 11, "fill": "[Black]", "capitalization": "allCaps", "tracking": 0}

_PAYMENT = re.compile(r"^(first|final)\s+payment|^deadline", re.I)


# The cover's "Ready to Register?" code. The page-2 code (terms and conditions) is elsewhere and stays.
REGISTER_QR_BOUNDS = (36.0, 514.8, 104.9, 581.6)
REGISTER_QR_TOLERANCE = 12.0


def polish_document(
    client: Any,
    plan: BrochurePlan,
    *,
    assets_dir: Path | None = None,
    work_dir: Path | None = None,
) -> list[str]:
    """Run each step on its own. A step that fails is reported and skipped."""
    notes: list[str] = []
    steps: list[tuple[str, Callable[[_Doc], list[str]]]] = [
        ("host panel", lambda doc: _host_panel(doc)),
        ("price bars", lambda doc: _bars(doc, plan)),
        ("cover line", lambda doc: _cover_line(doc, plan)),
        ("headline width", lambda doc: _headline_width(doc)),
        ("images", lambda doc: place_brand_images(doc.client, doc.inspect(), assets_dir)),
        ("register code", lambda doc: _register_qr(doc, plan, work_dir)),
    ]
    for label, step in steps:
        try:
            doc = _Doc(client)
            notes.extend(step(doc))
        except Exception as exc:  # DesignCraft rejects a command: keep the rest of the pass going.
            notes.append(f"{label}: skipped ({str(exc)[:160]})")
    return notes


class _Doc:
    def __init__(self, client: Any) -> None:
        self.client = client
        self._info: dict | None = None

    def inspect(self) -> dict:
        self._info = self.client.call("document.inspect", {})
        return self._info

    @property
    def info(self) -> dict:
        return self._info if self._info is not None else self.inspect()

    def run(self, command: str, params: dict) -> Any:
        return self.client.call("engine.execute", {"command": command, "params": params})

    def story(self, story_id: int) -> dict:
        return self.run("story.get", {"story": story_id})

    def stories(self) -> list[dict]:
        return [item for item in self.info.get("stories", []) if isinstance(item, dict)]

    def items(self) -> list[dict]:
        found: list[dict] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if "id" in node and "kind" in node and "bounds" in node:
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.info.get("spreads", []))
        return found

    def frame(self, frame_id: int) -> dict | None:
        return next((item for item in self.items() if item["id"] == frame_id), None)

    def page_size(self) -> tuple[float, float]:
        for spread in self.info.get("spreads", []):
            for page in spread.get("pages", []):
                x0, y0, x1, y1 = page["bounds"]
                return x1 - x0, y1 - y0
        return 792.0, 612.0

    def style(self, story_id: int, start: int, end: int, attrs: dict, para: dict | None = None) -> None:
        self.run("text.select", {"story": story_id, "anchor": start, "focus": end})
        self.run("type.char", {"attrs": attrs})
        if para:
            self.run("text.select", {"story": story_id, "anchor": start, "focus": end})
            self.run("type.para", {"attrs": para})

    def overset(self, story_id: int) -> bool:
        return bool(self.story(story_id).get("overset"))


def _lines_with_offsets(text: str) -> list[tuple[str, int, int]]:
    offset = 0
    rows = []
    for line in text.split("\n"):
        size = len(line.encode("utf-8"))
        rows.append((line, offset, offset + size))
        offset += size + 1
    return rows


def _host_panel(doc: _Doc) -> list[str]:
    notes: list[str] = []
    for story in doc.stories():
        preview = str(story.get("preview") or "").strip()
        if not preview.upper().startswith("CONTACT INFO"):
            continue
        story_id = int(story["id"])
        text = str(doc.story(story_id).get("text") or "")
        lines = [line.strip() for line in text.replace("\u2028", "\n").split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        clean = "\n".join(lines)
        if clean != text:
            doc.run("story.setText", {"story": story_id, "text": clean})
        yellow = _bar_yellow(doc) or YELLOW
        for index, (line, start, end) in enumerate(_lines_with_offsets(clean)):
            if index == 0:
                attrs, para = dict(TITLE), {"align": "left", "spaceAfter": 12}
            elif _PAYMENT.match(line):
                attrs, para = {**BODY, "fill": yellow}, {"align": "left", "spaceAfter": 0}
            else:
                attrs, para = dict(BODY), {"align": "left", "spaceAfter": 12}
            doc.style(story_id, start, end, attrs, para)
        notes.append("host panel restyled")
        break
    return notes


def _parse_cmyk(fill: str) -> tuple[float, float, float, float] | None:
    match = re.fullmatch(r"C=(\d+(?:\.\d+)?) M=(\d+(?:\.\d+)?) Y=(\d+(?:\.\d+)?) K=(\d+(?:\.\d+)?)", str(fill).strip())
    if match is None:
        return None
    return tuple(float(part) for part in match.groups())  # type: ignore[return-value]


def _is_yellow(fill: str) -> bool:
    cmyk = _parse_cmyk(fill)
    return cmyk is not None and cmyk[2] >= 90 and cmyk[0] <= 10 and cmyk[3] <= 10


def _bar_rects(doc: _Doc) -> list[dict]:
    bars = []
    for item in doc.items():
        if item.get("kind") != "<rectangle>" or not _is_yellow(item.get("fill", "")):
            continue
        x0, y0, x1, y1 = item["bounds"]
        if 120 <= x1 - x0 <= 260 and 20 <= y1 - y0 <= 60:
            bars.append(item)
    return bars


def _bar_yellow(doc: _Doc) -> str | None:
    bars = _bar_rects(doc)
    return str(bars[0]["fill"]) if bars else None


def _bars(doc: _Doc, plan: BrochurePlan) -> list[str]:
    wanted: dict[str, str] = {}
    for block in plan.blocks:
        if block.role in {"airfare", "tour_code", "trip_date"} and block.text.strip():
            wanted.setdefault(" ".join(block.text.split()).lower(), block.role)
    if not wanted:
        return []
    bars = _bar_rects(doc)
    notes: list[str] = []
    for story in doc.stories():
        key = " ".join(str(story.get("preview") or "").split()).lower()
        role = wanted.get(key)
        frames = story.get("frames") or []
        if role is None or not frames:
            continue
        frame = doc.frame(int(frames[0]))
        if frame is None:
            continue
        x0, y0, x1, y1 = frame["bounds"]
        centre_x, centre_y = (x0 + x1) / 2, (y0 + y1) / 2
        bar = next(
            (b for b in bars if b["bounds"][0] <= centre_x <= b["bounds"][2] and b["bounds"][1] <= centre_y <= b["bounds"][3]),
            None,
        )
        if bar is None:
            continue
        _align_in_bar(doc, int(story["id"]), frame, bar)
        notes.append(f"{role.replace('_', ' ')} bar aligned")
    return notes


def _align_in_bar(doc: _Doc, story_id: int, frame: dict, bar: dict) -> None:
    """Left-align a story inside its yellow bar, centred vertically, shrunk until it fits."""
    text = doc.story(story_id).get("text") or ""
    length = len(str(text).encode("utf-8"))
    bx0, by0, bx1, by1 = bar["bounds"]
    page_width, _page_height = doc.page_size()
    # Bars bleed past the trim. Keep the words inside the page.
    right = min(bx1 - 4.6, page_width - 12)
    target = [round(bx0 + 8.2, 2), round(by0 + 4, 2), round(right, 2), round(by1 - 4, 2)]
    doc.run("transform.resize", {"ids": [frame["id"]], "from": frame["bounds"], "to": target})
    doc.run("object.textFrameOptions", {"ids": [frame["id"]], "verticalJustification": "center", "inset": 0})
    tracking = -35 if len(str(text)) >= 24 else 0
    for size in (11, 10.5, 10, 9.5, 9, 8.5, 8):
        doc.style(story_id, 0, length, {**BAR_TEXT, "size": size, "tracking": tracking}, {"align": "left"})
        if not doc.overset(story_id):
            break


def bar_for_frame(doc: _Doc, frame: dict) -> dict | None:
    x0, y0, x1, y1 = frame["bounds"]
    centre_x, centre_y = (x0 + x1) / 2, (y0 + y1) / 2
    return next(
        (b for b in _bar_rects(doc) if b["bounds"][0] <= centre_x <= b["bounds"][2] and b["bounds"][1] <= centre_y <= b["bounds"][3]),
        None,
    )


def _host_name(plan: BrochurePlan) -> str | None:
    for block in plan.blocks:
        if block.role == "host_panel":
            match = re.match(r"HOST:\s*(.+)$", block.text.strip(), re.I)
            if match:
                return match.group(1).strip().upper()
    return None


def _cover_line(doc: _Doc, plan: BrochurePlan) -> list[str]:
    name = _host_name(plan)
    if not name:
        return []
    new_text = f"JOIN {name} IN"
    for story in doc.stories():
        preview = " ".join(str(story.get("preview") or "").split())
        if not preview.upper().startswith("JOIN") or not story.get("frames"):
            continue
        if preview.upper() == new_text:
            return []
        story_id = int(story["id"])
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": 4})
        found = doc.run("type.selectionAttrs", {}).get("chars", {})
        keep = {
            key: found[key]
            for key in ("fontFamily", "size", "fill", "tracking")
            if key in found
        }
        doc.run("story.setText", {"story": story_id, "text": new_text})
        length = len(new_text.encode("utf-8"))
        # The finished brochures set this line in bold caps, a step heavier than the template.
        doc.style(
            story_id,
            0,
            length,
            {**keep, "fontStyle": "Bold", "capitalization": "allCaps"},
            {"align": "left"},
        )
        return [f"cover line names {name.title()}"]
    return []


def _headline_width(doc: _Doc) -> list[str]:
    """Widen a one-line headline that the converted frame cuts short, within its own third of the page."""
    page_width, page_height = doc.page_size()
    panel = page_width / 3
    notes: list[str] = []
    for story in doc.stories():
        preview = str(story.get("preview") or "").strip()
        frames = story.get("frames") or []
        if not preview or len(preview) > 40 or len(frames) != 1:
            continue
        if not story.get("overset") or not story.get("lines"):
            continue
        frame_id = int(frames[0])
        frame = doc.frame(frame_id)
        if frame is None:
            continue
        x0, y0, x1, y1 = frame["bounds"]
        if x0 < 0 or y0 < 0 or x1 > page_width or y1 > page_height:
            continue
        story_id = int(story["id"])
        limit = (int(x0 // panel) + 1) * panel - 6
        right = x1
        current = list(frame["bounds"])
        while right < limit and doc.overset(story_id):
            right = min(right + 8, limit)
            target = [x0, y0, right, y1]
            doc.run("transform.resize", {"ids": [frame_id], "from": current, "to": target})
            current = target
        if doc.overset(story_id):
            doc.run("transform.resize", {"ids": [frame_id], "from": current, "to": [x0, y0, x1, y1]})
        else:
            notes.append(f"widened the frame for {preview!r}")
    return notes


def _tour_code(plan: BrochurePlan) -> str | None:
    for block in plan.blocks:
        if block.role == "tour_code":
            match = re.match(r"Tour\s+Code:\s*(\S+)", block.text.strip(), re.I)
            return match.group(1) if match else None
    return None


def _register_qr(doc: _Doc, plan: BrochurePlan, work_dir: Path | None) -> list[str]:
    """Point the cover's register code at /host-tour/<tour code>. Without a code the template's code stays."""
    code = _tour_code(plan)
    url = register_url(code) if code else None
    if url is None or work_dir is None:
        return []
    frame = next(
        (
            item
            for item in picture_frames(doc.inspect())
            if max(abs(a - b) for a, b in zip(REGISTER_QR_BOUNDS, item["bounds"])) <= REGISTER_QR_TOLERANCE
        ),
        None,
    )
    if frame is None:
        return []
    # The picture stays linked to this file, so it lives with the job's other output.
    png = write_qr_png(url, Path(work_dir) / "qr" / f"register-{code.lower()}.png")
    doc.run("file.place", {"path": str(png.resolve()), "frame": int(frame["id"])})
    doc.run("object.fit", {"mode": "fitProportionally", "ids": [int(frame["id"])]})
    return [f"register code points at {url}"]
