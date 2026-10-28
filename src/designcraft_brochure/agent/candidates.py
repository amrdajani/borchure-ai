"""Closed command list for one document inspection.

Jev chooses an id from this list. It cannot supply a path, a story id, or
free text. Every parameter below was taken from the inspection or the plan.
"""

from __future__ import annotations

import re
from pathlib import Path

from designcraft_brochure.content.plan import format_trip_date
from designcraft_brochure.models import BrochurePlan, CandidateAction, CopyBlock, Observation, StoryView


def build_candidates(
    observation: Observation,
    plan: BrochurePlan,
    *,
    working_copy: Path,
    native_path: Path,
    pdf_path: Path,
    idml_export_path: Path,
    preview_path: Path,
    overwrite: bool,
) -> list[CandidateAction]:
    working_copy = working_copy.expanduser().resolve()
    native_path = native_path.expanduser().resolve()
    pdf_path = pdf_path.expanduser().resolve()
    idml_export_path = idml_export_path.expanduser().resolve()
    preview_path = preview_path.expanduser().resolve()
    actions = [
        CandidateAction(
            id="inspect_document",
            description="Read pages, stories, frames, and overset from the active DesignCraft document.",
            risk="low",
            method="document.inspect",
        ),
        CandidateAction(
            id="request_human",
            description="Stop and ask a person to continue.",
            risk="low",
            method="agent.assist",
        ),
        CandidateAction(
            id="stop_agent",
            description="Stop because the PDF passed inspection and the native file is saved.",
            risk="low",
            method="agent.stop",
        ),
    ]
    if not observation.document_open:
        actions.append(_open_action(working_copy))
        return _cap(actions)

    fills = _fill_actions(observation, plan)
    if fills:
        return _cap(fills)
    actions.append(
        CandidateAction(
            id="render_page",
            description="Render the current page to a PNG preview.",
            risk="low",
            method="ui.render",
            params={"page": 0, "scale": 1, "path": str(preview_path)},
        )
    )
    actions.extend(_place_actions(observation, plan))
    if not native_path.exists() or overwrite:
        actions.append(
            CandidateAction(
                id="save_native",
                description="Save the edited document as a native .designcraft file in the output folder.",
                risk="medium",
                method="engine.execute",
                command="file.saveAs",
                params={"path": str(native_path)},
            )
        )
    if not pdf_path.exists() or overwrite:
        actions.append(
            CandidateAction(
                id="export_pdf",
                description="Export the document to the prepared PDF path.",
                risk="medium",
                method="engine.execute",
                command="file.exportPdf",
                params={"path": str(pdf_path)},
            )
        )
    if not idml_export_path.exists() or overwrite:
        actions.append(
            CandidateAction(
                id="export_idml",
                description="Export an IDML interchange copy of the edited document.",
                risk="medium",
                method="engine.execute",
                command="file.exportIdml",
                params={"path": str(idml_export_path)},
            )
        )
    return _cap(actions)


def _open_action(working_copy: Path) -> CandidateAction:
    suffix = working_copy.suffix.lower()
    command = "file.openIdml" if suffix == ".idml" else "file.open"
    return CandidateAction(
        id="open_working",
        description=(
            f"Open the working copy with {command}. The file is a copy. "
            "The original template is not opened, and nothing is deleted."
        ),
        risk="low",
        method="engine.execute",
        command=command,
        params={"path": str(working_copy)},
    )


_MONEY = re.compile(r"^\$\s?\d[\d,]*(?:\.\d{2})?$")
_RANGE = re.compile(r"\d{1,2}(?:st|nd|rd|th)?\s*[-–—]\s*\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}", re.I)
_RANGE_TO = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\w*"
    r"\s+\d{1,2}(?:st|nd|rd|th)?\s+(?:to|through)\s+"
    r"(?:(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\w*\s+)?"
    r"\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}",
    re.I,
)
_TOUR_CODE_STORY = re.compile(r"^(?:tour code:\s*)?[A-Za-z]{2,}[A-Za-z0-9]*\d[A-Za-z0-9]*$", re.I)
_SINGLE_DATE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+\d{1,2},\s+\d{4})$",
    re.I,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")


def _fill_actions(observation: Observation, plan: BrochurePlan) -> list[CandidateAction]:
    slotted = _slot_actions(observation, plan)
    if slotted or len(observation.stories) > 8:
        return slotted
    blocks = [block for block in plan.blocks if block.source_backed and block.text.strip()]
    stories = list(observation.stories)
    if not blocks or not stories:
        return []
    groups: list[list[CopyBlock]] = [[] for _ in stories]
    for index, block in enumerate(blocks):
        groups[min(index, len(stories) - 1)].append(block)
    actions = []
    for story, group in zip(stories, groups):
        if not group:
            continue
        text = "\n".join(block.text for block in group)
        if story.preview == text or story.preview == text[:500]:
            continue
        roles = ", ".join(block.role for block in group[:6])
        actions.append(
            CandidateAction(
                id=f"fill_story_{story.id}",
                description=(
                    f"Replace story {story.id} with {len(group)} approved source "
                    f"block{'s' if len(group) != 1 else ''} ({roles})."
                ),
                risk="medium",
                method="engine.execute",
                command="story.setText",
                params={"story": story.id, "text": text},
            )
        )
    return actions


def _slot_actions(observation: Observation, plan: BrochurePlan) -> list[CandidateAction]:
    """Replace a story only when the whole story is one price, date, or contact."""
    backed = [block for block in plan.blocks if block.source_backed and block.text.strip()]
    prices = [block.text for block in backed if block.role == "price"]
    dates = [block.text for block in backed if block.role == "date"]
    ranges = [text for text in dates if _RANGE.search(text)]
    singles = [text for text in dates if text not in ranges]
    money_stories = [story for story in observation.stories if _MONEY.match(_plain(story))]
    range_stories = [story for story in observation.stories if _is_range(_plain(story))]
    single_stories = [story for story in observation.stories if _SINGLE_DATE.match(_plain(story))]
    actions: list[CandidateAction] = []
    actions.extend(_paired(money_stories, _match_money(money_stories, prices), "price"))
    slash = [story for story in single_stories if "/" in _plain(story)]
    written = [story for story in single_stories if story not in slash]
    actions.extend(_paired(slash, list(reversed(singles)), "date"))
    actions.extend(_paired(written, singles, "date"))
    trip_dates = [block.text for block in backed if block.role == "trip_date"]
    if not trip_dates:
        for text in ranges:
            formatted = format_trip_date(text)
            if formatted and formatted not in trip_dates:
                trip_dates.append(formatted)
    actions.extend(_paired(range_stories, trip_dates or ranges, "date"))
    tour_code = next((block.text for block in backed if block.role == "tour_code"), "")
    if tour_code:
        for story in observation.stories:
            if _tour_code_story(story):
                actions.append(_replace(story, tour_code, "tour code"))
    airfare = next((block.text for block in backed if block.role == "airfare"), "")
    if airfare:
        for story in observation.stories:
            if _airfare_story(story):
                actions.append(_replace(story, airfare, "airfare"))
    panel = [block.text for block in backed if block.role == "host_panel"]
    if panel:
        text = "\n".join(panel)
        for story in observation.stories:
            if _host_info_story(story):
                actions.append(_replace(story, text, "contact"))
    return [item for item in actions if not _already_applied(_story_for(observation, item), item.params["text"])]


def _is_range(text: str) -> bool:
    for pattern in (_RANGE, _RANGE_TO):
        match = pattern.search(text)
        if match is None:
            continue
        leftover = (text[: match.start()] + text[match.end() :]).strip(" ,")
        if leftover == "" or re.fullmatch(r"[A-Za-z]+", leftover) is not None:
            return True
    return False


def _airfare_story(story: StoryView) -> bool:
    text = _plain(story)
    if not text or len(text) > 60:
        return False
    low = text.lower()
    if "transfers" in low or low.startswith("your "):
        return False
    return "airfare" in low


def _tour_code_story(story: StoryView) -> bool:
    text = _plain(story)
    if not text or len(text) > 40 or _is_range(text):
        return False
    return text.lower().startswith("tour code") or _TOUR_CODE_STORY.match(text) is not None


def _already_applied(story: StoryView, text: str) -> bool:
    current = _plain(story)
    wanted = " ".join(text.split())
    if re.search(r"round\s*trip", current, re.I) and not re.search(r"round\s*trip", wanted, re.I):
        return False
    if current == wanted:
        return True
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    folded = current.lower()
    if len(lines) > 1:
        if all(line.lower() in folded for line in lines):
            return True
        # Inspect only returns the start of a long story. That start is enough
        # once it matches the text we already wrote.
        if wanted.lower().startswith(folded):
            head = " ".join(lines[:2]).lower()
            return bool(head) and head in folded
        return False
    first = lines[0] if lines else ""
    return bool(first) and first.lower() in folded


def _plain(story: StoryView) -> str:
    return " ".join(story.preview.split())


def _story_for(observation: Observation, action: CandidateAction) -> StoryView:
    story_id = action.params.get("story")
    for story in observation.stories:
        if story.id == story_id:
            return story
    return StoryView(id=0, preview="")


_COMPANY_CONTACT = ("friends@travelwithfriends.com", "501-379-9226", "(501) 379-9226")


def _company_contact(story: StoryView) -> bool:
    text = _plain(story).lower()
    return any(marker.lower() in text for marker in _COMPANY_CONTACT)


def _host_info_story(story: StoryView) -> bool:
    if _company_contact(story):
        return False
    text = _plain(story).lower()
    if "contact info" in text or "host contact" in text:
        return True
    return "name:" in text and "email:" in text


def _contact_story(story: StoryView) -> bool:
    text = _plain(story)
    if not text or len(text) > 180 or _company_contact(story):
        return False
    return bool(_EMAIL.search(text) and _PHONE.search(text))


def _match_money(stories: list[StoryView], prices: list[str]) -> list[str]:
    remaining = list(prices)
    assigned: dict[int, str] = {}
    for story in stories:
        current = _plain(story)
        same = next((price for price in remaining if _amount(price) == _amount(current)), None)
        if same is None:
            continue
        assigned[story.id] = same
        remaining.remove(same)
    leftover_stories = sorted(
        (story for story in stories if story.id not in assigned),
        key=lambda story: _amount(_plain(story)),
        reverse=True,
    )
    leftover_prices = sorted(remaining, key=_amount, reverse=True)
    for story, price in zip(leftover_stories, leftover_prices):
        assigned[story.id] = price
    return [assigned.get(story.id, "") for story in stories]


def _amount(text: str) -> float:
    digits = re.sub(r"[^\d.]", "", text)
    try:
        return float(digits) if digits else 0
    except ValueError:
        return 0


def _paired(stories: list[StoryView], replacements: list[str], kind: str) -> list[CandidateAction]:
    actions = []
    for story, replacement in zip(stories, replacements):
        if not replacement or _plain(story) == replacement:
            continue
        actions.append(_replace(story, replacement, kind))
    return actions


def _replace(story: StoryView, text: str, kind: str) -> CandidateAction:
    current = _plain(story)
    return CandidateAction(
        id=f"fill_story_{story.id}",
        description=(
            f"Replace the {kind} story {story.id}, which currently reads {current!r}, "
            f"with this exact proposal fact: {text!r}."
        ),
        risk="low",
        method="engine.execute",
        command="story.setText",
        params={"story": story.id, "text": text},
    )


def _place_actions(observation: Observation, plan: BrochurePlan) -> list[CandidateAction]:
    if not plan.image_paths:
        return []
    graphics = [
        frame
        for frame in observation.frames
        if frame.kind.lower() in {"graphic", "image", "rectangleframe"}
    ]
    actions = []
    for index, image in enumerate(plan.image_paths[:3]):
        image = str(Path(image).expanduser().resolve())
        name = Path(image).name
        if index < len(graphics):
            frame = graphics[index]
            actions.append(
                CandidateAction(
                    id=f"place_image_{index + 1}",
                    description=f"Place proposal image {name} into frame {frame.id}.",
                    risk="medium",
                    method="engine.execute",
                    command="file.place",
                    params={"path": image, "frame": frame.id},
                )
            )
        else:
            y = (36, 220, 400)[index]
            actions.append(
                CandidateAction(
                    id=f"place_image_{index + 1}",
                    description=f"Place proposal image {name} in a new frame on the page.",
                    risk="medium",
                    method="engine.execute",
                    command="file.place",
                    params={"path": image, "x": 36, "y": y, "width": 180},
                )
            )
    return actions


def _cap(actions: list[CandidateAction]) -> list[CandidateAction]:
    return actions[:40]
