"""Screenshot description.

The vision model returns structured text. Jev receives that text and chooses
an action. Bounding boxes are fractions of the screenshot so they survive
resolution differences.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from brochure_agent.content.openai_compat import ProviderError, chat_completion
from brochure_agent.models import Observation, UIElement


class VisionElement(BaseModel):
    role: str = "unknown"
    name: str = ""
    bbox: list[float] = Field(default_factory=list)
    selected: bool = False
    issues: list[str] = Field(default_factory=list)


class VisionDescription(BaseModel):
    application: str = ""
    dialog: str | None = None
    page_label: str | None = None
    text_editing: bool = False
    notes: str = ""
    elements: list[VisionElement] = Field(default_factory=list)


def interpret_screenshot(
    image_path: str,
    *,
    api_base: str,
    api_key: str,
    model: str,
    width: int,
    height: int,
) -> tuple[list[UIElement], VisionDescription]:
    encoded = base64.standard_b64encode(Path(image_path).read_bytes()).decode("ascii")
    messages = [
        {
            "role": "system",
            "content": (
                "Describe this desktop screenshot as JSON. Do not decide the next click. "
                "Keys: application, dialog, page_label, text_editing, notes, elements. "
                "elements is a list of objects with role, name, bbox, selected, issues. "
                "bbox is [x, y, width, height] as fractions from 0 to 1 of the image. "
                "Report only controls and frames you can see. Use an empty string when unsure."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Image size is {width} by {height} pixels."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        },
    ]
    raw = chat_completion(api_base=api_base, api_key=api_key, model=model, messages=messages)
    description = _parse(raw)
    elements = _to_pixels(description, width, height)
    return elements, description


def merge_observation(
    *,
    screenshot_path: str | None,
    width: int,
    height: int,
    mouse_x: int,
    mouse_y: int,
    windows: list[str],
    active_window: str | None,
    dialog: str | None,
    accessible: list[UIElement],
    vision_elements: list[UIElement],
    vision: VisionDescription | None,
    vision_error: str | None,
    working_stem: str | None,
) -> Observation:
    limits: list[str] = []
    notes = ""
    application = _guess_application(windows, active_window)
    text_editing = any(element.selected and _text_role(element.role) for element in accessible)
    page_label = None
    if vision is not None:
        notes = vision.notes
        if vision.application:
            application = vision.application
        if vision.dialog and not dialog:
            dialog = vision.dialog
        text_editing = text_editing or vision.text_editing
        page_label = vision.page_label
    elif vision_error:
        limits.append(vision_error)
    else:
        limits.append("No vision model is configured. Canvas frames are visible only through accessibility.")
    if not accessible and not vision_elements:
        limits.append("No controls were listed for this screen.")
    if page_label:
        notes = f"Page {page_label}. {notes}".strip()
    titles = " ".join(windows + ([active_window] if active_window else []))
    document_open = bool(working_stem and working_stem.lower() in titles.lower())
    return Observation(
        screenshot_path=screenshot_path,
        image_width=width,
        image_height=height,
        mouse_x=mouse_x,
        mouse_y=mouse_y,
        windows=windows,
        active_window=active_window,
        dialog=dialog,
        application_guess=application,
        elements=[*accessible, *vision_elements],
        vision_notes=notes[:1000],
        vision_available=vision is not None,
        text_editing=text_editing,
        document_open=document_open,
        limits=limits,
    )


def _parse(raw: str) -> VisionDescription:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
        return VisionDescription.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ProviderError("Vision model did not return the expected JSON.") from exc


def _to_pixels(description: VisionDescription, width: int, height: int) -> list[UIElement]:
    elements: list[UIElement] = []
    for index, item in enumerate(description.elements[:15], start=1):
        bbox = _pixel_bbox(item.bbox, width, height)
        if bbox is None:
            continue
        elements.append(
            UIElement(
                id=f"v{index}",
                source="vision",
                role=item.role[:40] or "unknown",
                name=item.name[:80],
                bbox=bbox,
                selected=item.selected,
                issues=item.issues[:4],
            )
        )
    return elements


def _pixel_bbox(values: list[float], width: int, height: int) -> tuple[float, float, float, float] | None:
    if len(values) != 4:
        return None
    x, y, box_w, box_h = (float(value) for value in values)
    if min(x, y, box_w, box_h) < 0 or max(x, y, box_w, box_h) > 1:
        return None
    if box_w <= 0 or box_h <= 0:
        return None
    return (x * width, y * height, box_w * width, box_h * height)


def _guess_application(windows: list[str], active: str | None) -> str:
    blob = " ".join(windows + ([active] if active else [])).lower()
    if "indesign" in blob:
        return "Adobe InDesign"
    return "unknown"


def _text_role(role: str) -> bool:
    lowered = role.lower()
    return "text" in lowered or "edit" in lowered
