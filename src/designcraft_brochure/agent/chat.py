"""Edit a finished brochure from a chat message.

The person types what they want ("make the tour code bar red", "the host
phone is 501-555-0100"). A language model turns that into a short list of
operations from a closed set. Nothing the model says is sent to DesignCraft
as it is: every operation is checked against what is really in the document,
and only these operations can run:

    replace_text, set_text, style_text, fill_shape, replace_picture,
    tour_code, move, undo

The model never sees images and it never invents facts: a name, phone, email,
date or price may only come from the person's message or the existing text.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from designcraft_brochure.config import Settings
from designcraft_brochure.content.plan import _complete, normalize_tour_code
from designcraft_brochure.agent import days
from designcraft_brochure.craft import polish
from designcraft_brochure.craft.client import ControlClient
from designcraft_brochure.models import BrochurePlan, CopyBlock
from designcraft_brochure.safety import is_suspicious

MAX_OPS = 12
MAX_STORIES = 70
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
OP_NAMES = {"replace_text", "set_text", "style_text", "fill_shape", "replace_picture", "tour_code", "move", "reorder_days", "undo"}
Completer = Callable[[Settings, list[dict]], str]


class ChatError(Exception):
    """The request cannot be done. The message is safe to show to the person."""


@dataclass
class ChatResult:
    reply: str
    ops: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(op["op"] != "undo" for op in self.ops)

    @property
    def undo(self) -> bool:
        return any(op["op"] == "undo" for op in self.ops)


# --------------------------------------------------------------------------- snapshot


def take_snapshot(client: Any) -> dict:
    """What is on each page: text, shapes and pictures, with the ids the operations use."""
    doc = polish._Doc(client)
    info = doc.inspect()
    page_width, page_height = doc.page_size()
    frames: dict[int, dict] = {}
    for page_index, spread in enumerate(info.get("spreads", [])):
        for item in _walk(spread):
            frames[int(item["id"])] = {**item, "page": page_index + 1}

    stories: list[dict] = []
    for story in doc.stories():
        story_frames = story.get("frames") or []
        if not story_frames:
            continue
        frame = frames.get(int(story_frames[0]))
        if frame is None or not _on_page(frame["bounds"], page_width, page_height):
            continue
        text = str(doc.story(int(story["id"])).get("text") or "")
        if not text.strip():
            continue
        style = _first_char_style(doc, int(story["id"]))
        stories.append(
            {
                "story": int(story["id"]),
                "frame": int(frame["id"]),
                "page": frame["page"],
                "where": _where(frame["bounds"], page_width),
                "bounds": [round(v, 1) for v in frame["bounds"]],
                "text": text,
                "style": style,
            }
        )
        if len(stories) >= MAX_STORIES:
            break

    shapes, pictures = [], []
    for frame in frames.values():
        if not _on_page(frame["bounds"], page_width, page_height):
            continue
        width = frame["bounds"][2] - frame["bounds"][0]
        height = frame["bounds"][3] - frame["bounds"][1]
        entry = {
            "frame": int(frame["id"]),
            "page": frame["page"],
            "where": _where(frame["bounds"], page_width),
            "bounds": [round(v, 1) for v in frame["bounds"]],
        }
        if frame.get("kind") == "<image>":
            share = width * height / (page_width * page_height)
            size = "very large" if share > 0.25 else "large" if share > 0.08 else "small" if share > 0.01 else "tiny"
            pictures.append({**entry, "size": f"{size} ({round(width)} x {round(height)} pt)"})
        elif frame.get("kind") == "<rectangle>" and frame.get("fill") not in (None, "[None]"):
            if width * height < page_width * page_height * 0.6:
                shapes.append({**entry, "fill": frame.get("fill")})
    for shape in shapes:
        x0, y0, x1, y1 = shape["bounds"]
        shape["holds"] = [
            " ".join(item["text"].split())[:60]
            for item in stories
            if item["page"] == shape["page"]
            and x0 <= (item["bounds"][0] + item["bounds"][2]) / 2 <= x1
            and y0 <= (item["bounds"][1] + item["bounds"][3]) / 2 <= y1
        ]
    return {
        "page_size": [page_width, page_height],
        "swatches": [str(name) for name in info.get("swatches", []) if not str(name).startswith("[")],
        "stories": stories,
        "shapes": shapes,
        "pictures": pictures,
        "days": days.describe_days(stories),
    }


def _walk(node: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        if "id" in node and "kind" in node and "bounds" in node:
            found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def _on_page(bounds: list[float], width: float, height: float) -> bool:
    centre_x, centre_y = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    return 0 <= centre_x <= width and 0 <= centre_y <= height


def _where(bounds: list[float], page_width: float) -> str:
    centre = (bounds[0] + bounds[2]) / 2
    panel = ("left", "middle", "right")[min(2, max(0, int(centre // (page_width / 3))))]
    vertical = "top" if bounds[1] < 200 else "bottom" if bounds[1] > 400 else "middle"
    return f"{panel} panel, {vertical}"


def _first_char_style(doc: Any, story_id: int) -> str:
    try:
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": 1})
        found = doc.run("type.selectionAttrs", {}).get("chars", {})
    except Exception:
        return ""
    parts = [found.get("fontStyle"), f"{found.get('size')}pt" if found.get("size") else None, found.get("fill")]
    return " ".join(str(part) for part in parts if part)


# --------------------------------------------------------------------------- planner

_SYSTEM = """You are the editor inside a chat for one finished Travel With Friends brochure.
The person asks for a change. You answer with JSON only, no markdown:
{"reply": "<1 to 3 plain sentences>", "ops": [ ... ]}

"reply" says what you changed, or why you could not, in the words of a helpful designer. Never mention ids, JSON or operations.
Questions that need no change ("what does page 2 say?") get a reply and "ops": [].

Operations. Use only ids from the lists you are given.
- {"op":"replace_text","story":ID,"find":"exact words now in that text","replace":"new words"}  (best for small edits: it keeps the formatting)
- {"op":"set_text","story":ID,"text":"whole new text"}  (only when most of the text changes; lines are separated by \\n)
- {"op":"style_text","story":ID,"bold":true|false,"size":NUMBER,"color":"#rrggbb or a swatch name","caps":true|false,"align":"left|center|right"}  (any subset of the style keys)
- {"op":"fill_shape","frame":ID,"color":"#rrggbb or a swatch name"}  (a coloured bar or box)
- {"op":"replace_picture","frame":ID,"image":N,"fit":"fill|fit"}  (N counts the attached pictures from 0)
- {"op":"tour_code","value":"HLMRS426"}  (sets the tour code bar and points the register QR code at that tour)
- {"op":"move","frame":ID,"dx":NUMBER,"dy":NUMBER}  (points; right and down are positive; at most 200)
- {"op":"reorder_days","order":[1,2,5,4,3,6,7,8,9,10]}  (the itinerary days. "order" lists the day numbers from top to bottom: that example swaps day 3 and day 5. The DAY labels stay 1, 2, 3 in order and each day's title and bullets move; the spacing is redone for you)
- {"op":"undo"}  (go back to before the last change)

Rules:
- Change only what was asked. At most 12 operations.
- Never invent a fact. Every name, phone number, email, date, price or code you write must come from the person's message or from text already in the brochure.
- Keep text short enough to fit where it sits. Headlines and bars are one line.
- A shape's "holds" lists the text sitting on it. Brand colours are the swatches listed: use a swatch name when the person names a brand colour (the yellow, the blue), otherwise a hex.
- You cannot add pages, change the font family, draw new shapes, or translate. If asked, say so and offer what you can do. Use "ops": [].
- On the first page the big photo on the right panel is the cover photo; small pictures are logos or icons. Pick the obvious match and do it. Ask one short question (with "ops": []) only when two things are equally likely."""


def plan_edits(
    settings: Settings,
    message: str,
    snapshot: dict,
    history: list[dict],
    image_names: list[str],
    *,
    complete: Completer | None = None,
) -> tuple[str, list[dict]]:
    brochure = {
        "swatches": snapshot["swatches"],
        "text": [
            {k: v for k, v in {**item, "text": item["text"][:400]}.items() if k not in {"bounds"}}
            for item in snapshot["stories"]
        ],
        "shapes": snapshot["shapes"],
        "pictures": snapshot["pictures"],
        "itinerary_days": snapshot.get("days", []),
    }
    user = json.dumps(
        {
            "message": message,
            "attached_pictures": image_names,
            "earlier_in_this_chat": history[-6:],
            "brochure": brochure,
        },
        ensure_ascii=False,
    )
    raw = (complete or _complete)(
        settings, [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]
    )
    payload = _extract_json(raw)
    reply = payload.get("reply")
    ops = payload.get("ops", [])
    if not isinstance(reply, str) or not isinstance(ops, list):
        raise ChatError("I could not work out what to change. Try saying it another way.")
    return " ".join(reply.split())[:600], [op for op in ops if isinstance(op, dict)]


def _extract_json(raw: str) -> dict:
    """The first JSON object in the reply, even when the model wraps it in words or a code fence."""
    start = raw.find("{")
    if start < 0:
        return {}
    try:
        payload, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------- validation


def validate_ops(ops: list[dict], snapshot: dict, image_count: int) -> list[dict]:
    """Keep only operations that name real things in this document. Raise ChatError on anything else."""
    if len(ops) > MAX_OPS:
        raise ChatError("That is too many changes at once. Ask for a few at a time.")
    stories = {item["story"]: item for item in snapshot["stories"]}
    shape_ids = {item["frame"] for item in snapshot["shapes"]}
    picture_ids = {item["frame"] for item in snapshot["pictures"]}
    movable = {item["frame"] for item in snapshot["stories"]} | shape_ids | picture_ids
    swatches = {name.lower(): name for name in snapshot["swatches"]}
    clean: list[dict] = []
    for raw in ops:
        name = raw.get("op")
        if name not in OP_NAMES:
            raise ChatError("I can't do that kind of change.")
        if name == "undo":
            clean.append({"op": "undo"})
        elif name == "replace_text":
            story = _story(raw, stories)
            find, replace = _text(raw.get("find"), 200), _text(raw.get("replace"), 300, allow_empty=True)
            if find not in stories[story]["text"]:
                raise ChatError(f"I could not find “{find}” where I expected it.")
            clean.append({"op": name, "story": story, "find": find, "replace": replace})
        elif name == "set_text":
            story = _story(raw, stories)
            clean.append({"op": name, "story": story, "text": _text(raw.get("text"), 600, multiline=True)})
        elif name == "style_text":
            story = _story(raw, stories)
            style = _style(raw, swatches)
            if not style:
                raise ChatError("Say what to change about the text: bold, size, colour, capitals or alignment.")
            clean.append({"op": name, "story": story, **style})
        elif name == "fill_shape":
            frame = raw.get("frame")
            if frame not in shape_ids:
                raise ChatError("I can't find that coloured area.")
            clean.append({"op": name, "frame": frame, "color": _color(raw.get("color"), swatches)})
        elif name == "replace_picture":
            frame, image = raw.get("frame"), raw.get("image")
            if frame not in picture_ids:
                raise ChatError("I can't find that picture frame.")
            if not isinstance(image, int) or isinstance(image, bool) or not 0 <= image < image_count:
                raise ChatError("Attach the new picture to your message and I'll swap it in.")
            fit = raw.get("fit") if raw.get("fit") in {"fill", "fit"} else "fill"
            clean.append({"op": name, "frame": frame, "image": image, "fit": fit})
        elif name == "tour_code":
            value = normalize_tour_code(raw.get("value") if isinstance(raw.get("value"), str) else "")
            if value is None:
                raise ChatError("A tour code is 2 to 24 letters, numbers or dashes, for example HLMRS426.")
            clean.append({"op": name, "value": value})
        elif name == "reorder_days":
            numbers = sorted(item["day"] for item in snapshot.get("days", []))
            order = raw.get("order")
            if not numbers:
                raise ChatError("I can't find a list of days in this brochure.")
            ok = isinstance(order, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in order)
            if not ok or sorted(order) != numbers:
                raise ChatError(f"The days are {', '.join(map(str, numbers))}. Tell me which ones to swap or move.")
            clean.append({"op": name, "order": list(order)})
        elif name == "move":
            frame, dx, dy = raw.get("frame"), raw.get("dx"), raw.get("dy")
            if frame not in movable:
                raise ChatError("I can't find that to move.")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= 200 for v in (dx, dy)):
                raise ChatError("I can move things up to 200 points at a time.")
            clean.append({"op": name, "frame": frame, "dx": float(dx), "dy": float(dy)})
    return clean


def _story(raw: dict, stories: dict[int, dict]) -> int:
    story = raw.get("story")
    if story not in stories:
        raise ChatError("I can't find that text in the brochure.")
    return int(story)


def _text(value: Any, limit: int, *, allow_empty: bool = False, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ChatError("I could not read that text.")
    if not multiline:
        value = value.replace("\n", " ")
    value = _CONTROL.sub("", value).strip() if multiline else _CONTROL.sub("", value)
    if not value and not allow_empty:
        raise ChatError("The new text is empty.")
    if len(value) > limit:
        raise ChatError("That text is too long for the brochure.")
    if is_suspicious(value):
        raise ChatError("I can't put that text in the brochure.")
    return value


def _color(value: Any, swatches: dict[str, str]) -> str:
    if isinstance(value, str):
        if _HEX.fullmatch(value.strip()):
            return value.strip().lower()
        if value.strip().lower() in swatches:
            return swatches[value.strip().lower()]
    raise ChatError("I could not tell which colour you mean. Give me a colour name or a hex like #cc2222.")


def _style(raw: dict, swatches: dict[str, str]) -> dict:
    style: dict[str, Any] = {}
    if isinstance(raw.get("bold"), bool):
        style["bold"] = raw["bold"]
    if isinstance(raw.get("caps"), bool):
        style["caps"] = raw["caps"]
    if raw.get("size") is not None:
        size = raw["size"]
        if not isinstance(size, (int, float)) or isinstance(size, bool) or not 6 <= size <= 120:
            raise ChatError("Text size has to be between 6 and 120 points.")
        style["size"] = float(size)
    if raw.get("color") is not None:
        style["color"] = _color(raw["color"], swatches)
    if raw.get("align") is not None:
        if raw["align"] not in {"left", "center", "right"}:
            raise ChatError("Alignment can be left, center or right.")
        style["align"] = raw["align"]
    return style


# --------------------------------------------------------------------------- apply


def apply_ops(
    client: Any,
    ops: list[dict],
    snapshot: dict,
    *,
    images: list[Path],
    work_dir: Path,
    assets_dir: Path | None = None,
) -> list[str]:
    doc = polish._Doc(client)
    doc.inspect()
    stories = {item["story"]: item for item in snapshot["stories"]}
    swatches = {name.lower(): name for name in snapshot["swatches"]}
    notes: list[str] = []
    touched: list[int] = []

    for op in ops:
        kind = op["op"]
        if kind == "replace_text":
            _replace_text(doc, stories[op["story"]], op["find"], op["replace"])
            touched.append(op["story"])
            notes.append("text changed")
        elif kind == "set_text":
            _set_text(doc, stories[op["story"]], op["text"])
            touched.append(op["story"])
            notes.append("text changed")
        elif kind == "style_text":
            _style_text(doc, stories[op["story"]], op, swatches)
            notes.append("text restyled")
        elif kind == "fill_shape":
            name = _swatch(doc, op["color"], swatches)
            doc.run("object.fill", {"swatch": name, "ids": [op["frame"]]})
            notes.append("colour changed")
        elif kind == "replace_picture":
            frame = op["frame"]
            doc.run("file.place", {"path": str(images[op["image"]].resolve()), "frame": frame})
            doc.run("object.fit", {"mode": "fillProportionally" if op["fit"] == "fill" else "fitProportionally", "ids": [frame]})
            notes.append("picture replaced")
        elif kind == "tour_code":
            notes.extend(_tour_code(doc, snapshot, op["value"], work_dir))
        elif kind == "reorder_days":
            try:
                notes.extend(days.reorder_days(doc, take_snapshot(client), op["order"]))
            except days.DaysError as exc:
                raise ChatError(str(exc)) from exc
        elif kind == "move":
            doc.run("transform.move", {"dx": op["dx"], "dy": op["dy"], "ids": [op["frame"]]})
            notes.append("moved")

    if touched:
        notes.extend(_refit(doc, touched))
    return notes


def _replace_text(doc: Any, story: dict, find: str, replace: str) -> None:
    try:
        result = doc.run(
            "find.change",
            {"find": find, "change": replace, "story": story["story"], "first": True, "caseSensitive": True},
        )
        if isinstance(result, dict) and result.get("count") == 0:
            raise RuntimeError("no match")
    except Exception:
        _set_text(doc, story, story["text"].replace(find, replace, 1), keep_lines=True)


def _set_text(doc: Any, story: dict, text: str, *, keep_lines: bool = False) -> None:
    story_id = story["story"]
    doc.run("text.select", {"story": story_id, "anchor": 0, "focus": 1})
    found = doc.run("type.selectionAttrs", {})
    chars = found.get("chars", {}) if isinstance(found, dict) else {}
    keep = {k: chars[k] for k in ("fontFamily", "fontStyle", "size", "fill", "capitalization", "tracking") if k in chars}
    align = (found.get("para", {}) if isinstance(found, dict) else {}).get("align")
    doc.run("story.setText", {"story": story_id, "text": text})
    length = len(text.encode("utf-8"))
    if keep:
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
        doc.run("type.char", {"attrs": keep})
    if align:
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
        doc.run("type.para", {"attrs": {"align": align}})


def _style_text(doc: Any, story: dict, op: dict, swatches: dict[str, str]) -> None:
    attrs: dict[str, Any] = {}
    if "bold" in op:
        attrs["fontStyle"] = "Bold" if op["bold"] else "Regular"
    if "caps" in op:
        attrs["capitalization"] = "allCaps" if op["caps"] else "normal"
    if "size" in op:
        attrs["size"] = op["size"]
    if "color" in op:
        attrs["fill"] = _swatch(doc, op["color"], swatches)
    story_id = story["story"]
    length = len(str(doc.story(story_id).get("text") or "").encode("utf-8"))
    if attrs:
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
        doc.run("type.char", {"attrs": attrs})
    if "align" in op:
        doc.run("text.select", {"story": story_id, "anchor": 0, "focus": length})
        doc.run("type.para", {"attrs": {"align": op["align"]}})


def _swatch(doc: Any, color: str, swatches: dict[str, str]) -> str:
    if color.lower() in swatches:
        return swatches[color.lower()]
    name = f"Chat {color.upper()}"
    try:
        doc.run("swatch.create", {"name": name, "color": color})
    except Exception:
        pass  # already made earlier in this document
    swatches[color.lower()] = name
    return name


def _tour_code(doc: Any, snapshot: dict, value: str, work_dir: Path) -> list[str]:
    notes: list[str] = []
    for story in doc.stories():
        if str(story.get("preview") or "").strip().lower().startswith("tour code"):
            current = next((s for s in snapshot["stories"] if s["story"] == int(story["id"])), None)
            if current is not None:
                _set_text(doc, current, f"Tour Code: {value}")
                _refit(doc, [int(story["id"])])
                notes.append("tour code changed")
            break
    plan = BrochurePlan(
        template_id="chat",
        blocks=[CopyBlock(id="tour_code", role="tour_code", text=f"Tour Code: {value}", source_backed=True)],
    )
    notes.extend(polish._register_qr(doc, plan, work_dir))
    return notes


def _refit(doc: Any, story_ids: list[int]) -> list[str]:
    """Edited text changes length. Put bars, the host panel and one-line headlines back in shape."""
    notes: list[str] = []
    doc.inspect()
    stories = {int(item["id"]): item for item in doc.stories()}
    for story_id in dict.fromkeys(story_ids):
        story = stories.get(story_id)
        if story is None or not story.get("frames"):
            continue
        frame = doc.frame(int(story["frames"][0]))
        if frame is None:
            continue
        if str(story.get("preview") or "").strip().upper().startswith("CONTACT INFO"):
            notes.extend(polish._host_panel(doc))
            continue
        bar = polish.bar_for_frame(doc, frame)
        if bar is not None:
            polish._align_in_bar(doc, story_id, frame, bar)
    notes.extend(polish._headline_width(doc))
    return notes


# --------------------------------------------------------------------------- one chat turn


def save_working_copy(source: Path, revision_dir: Path) -> Path:
    revision_dir.mkdir(parents=True, exist_ok=True)
    working = revision_dir / f"working{source.suffix.lower()}"
    shutil.copyfile(source, working)
    return working


def open_document(client: ControlClient, working: Path) -> None:
    command = "file.openIdml" if working.suffix.lower() == ".idml" else "file.open"
    client.call("engine.execute", {"command": command, "params": {"path": str(working.resolve())}})


def export_all(client: ControlClient, pdf: Path, native: Path, idml: Path) -> None:
    for command, path in (("file.exportPdf", pdf), ("file.saveAs", native), ("file.exportIdml", idml)):
        path.parent.mkdir(parents=True, exist_ok=True)
        client.call("engine.execute", {"command": command, "params": {"path": str(path.resolve())}})
        if not path.is_file():
            raise ChatError(f"{path.name} was not written.")
