"""Build the closed action list from the current screen.

Candidates exist only when the observation supports them. The decision model
picks one id. It cannot supply its own coordinates or text.
"""

from __future__ import annotations

from pathlib import Path

from brochure_agent.models import ActionName, BrochurePlan, CandidateAction, Observation
from brochure_agent.safety import control_is_destructive, control_replaces

_TEXT_ROLES = ("text", "edit", "document")


def build_candidates(
    observation: Observation,
    *,
    working_copy: Path | None,
    pdf_path: Path,
    plan: BrochurePlan | None,
    allowlist: set[str],
    used_block_ids: set[str],
    overwrite_confirmed: bool,
    text_focus_likely: bool,
) -> list[CandidateAction]:
    items: list[CandidateAction] = [
        _simple("wait", ActionName.wait, "low", "Wait briefly for the application to finish redrawing.", {"seconds": 0.4}),
        _simple(
            "request_human",
            ActionName.request_human,
            "low",
            "Stop and ask a person for help. Choose this when the screen is ambiguous or the next step could discard work.",
            {"reason": "screen or document needs a person"},
        ),
        _simple(
            "stop_agent",
            ActionName.stop_agent,
            "low",
            "Stop because the PDF exists and its quality status is ok. Do not choose this only because a dialog closed.",
            {},
        ),
        _simple("take_screenshot", ActionName.take_screenshot, "low", "Capture a fresh screenshot of the desktop.", {}),
        _simple("list_windows", ActionName.list_windows, "low", "List the titles of visible top-level windows.", {}),
        _simple(
            "inspect_visible_state",
            ActionName.inspect_visible_state,
            "low",
            "Read accessible controls in the foreground window.",
            {},
        ),
    ]
    for title in observation.windows[:5]:
        items.append(
            _simple(
                _slug("focus", title),
                ActionName.focus_window,
                "low",
                f"Bring the window titled {title[:80]!r} to the foreground.",
                {"title": title},
            )
        )
    if observation.dialog:
        items.extend(
            _dialog_actions(
                observation,
                working_copy=working_copy,
                pdf_path=pdf_path,
                plan=plan,
                allowlist=allowlist,
                overwrite_confirmed=overwrite_confirmed,
            )
        )
    else:
        items.extend(_document_actions(observation, working_copy, plan))
        if text_focus_likely or observation.text_editing:
            items.extend(_copy_actions(plan, used_block_ids, allowlist))
        items.extend(_pointer_actions(observation))
    return _cap(items)


def poc_candidates(observation: Observation) -> list[CandidateAction]:
    x = min(observation.image_width - 1, max(0, observation.mouse_x + 12))
    y = min(observation.image_height - 1, max(0, observation.mouse_y + 8))
    return [
        _simple(
            "move_mouse",
            ActionName.move_mouse,
            "low",
            "Move the pointer a few pixels. This does not click, type, or change a document.",
            {"x": x, "y": y},
        ),
        _simple("wait", ActionName.wait, "low", "Wait for 0.2 seconds without changing anything.", {"seconds": 0.2}),
        _simple("take_screenshot", ActionName.take_screenshot, "low", "Capture another screenshot.", {}),
        _simple("stop_agent", ActionName.stop_agent, "low", "End the proof of concept without further input.", {}),
        _simple("request_human", ActionName.request_human, "low", "Stop the proof of concept and ask a person.", {}),
    ]


def _dialog_actions(
    observation: Observation,
    *,
    working_copy: Path | None,
    pdf_path: Path,
    plan: BrochurePlan | None,
    allowlist: set[str],
    overwrite_confirmed: bool,
) -> list[CandidateAction]:
    kind = _dialog_kind(observation.dialog or "")
    paths = _paths_for_dialog(kind, working_copy, pdf_path, plan)
    actions = [
        _simple(
            "key_escape",
            ActionName.press_key,
            "medium",
            "Press Escape to dismiss the current dialog without confirming it.",
            {"key": "escape"},
        ),
        _simple(
            "key_alt_n",
            ActionName.press_key,
            "medium",
            "Press Alt+N to focus the file-name field in a Windows file dialog.",
            {"key": "alt+n"},
        ),
    ]
    for path in paths:
        if path not in allowlist:
            continue
        target = Path(path)
        actions.append(
            CandidateAction(
                id=_slug("typepath", target.name),
                name=ActionName.type_text,
                description=f"Type the allowlisted path {target.name} into the focused file-name field.",
                risk="high",
                args={"text": path, "kind": "path", "block_id": target.name},
            )
        )
        if target.exists() and not overwrite_confirmed:
            continue
        actions.append(
            CandidateAction(
                id=_slug("confirm", target.name),
                name=ActionName.press_key,
                description=(
                    f"Press Enter to confirm the file dialog for {target.name}. "
                    "Choose this only after that path has been typed."
                ),
                risk="high",
                args={"key": "enter", "target": path},
            )
        )
    return actions


def _pointer_actions(observation: Observation) -> list[CandidateAction]:
    actions = []
    for element in observation.elements:
        if element.bbox is None or control_is_destructive(element.name):
            continue
        if control_replaces(element.name):
            continue
        x, y, width, height = element.bbox
        center = {"x": int(x + width / 2), "y": int(y + height / 2), "element_id": element.id}
        label = element.name or element.role
        actions.append(
            CandidateAction(
                id=_slug("click", element.id),
                name=ActionName.click,
                description=(
                    f"Click the {element.source} {element.role} named {label[:60]!r} "
                    f"at the center of its observed box."
                ),
                risk="medium",
                args=center,
            )
        )
        if any(token in element.role.lower() for token in _TEXT_ROLES):
            actions.append(
                CandidateAction(
                    id=_slug("double_click", element.id),
                    name=ActionName.double_click,
                    description=f"Double-click the text frame {label[:60]!r} so its text can be edited.",
                    risk="medium",
                    args=center,
                )
            )
    return actions


def _copy_actions(
    plan: BrochurePlan | None,
    used_block_ids: set[str],
    allowlist: set[str],
) -> list[CandidateAction]:
    if plan is None:
        return []
    actions = []
    for block in plan.blocks:
        if block.id in used_block_ids or block.text not in allowlist or not block.source_backed:
            continue
        actions.append(
            CandidateAction(
                id=_slug("type", block.id),
                name=ActionName.type_text,
                description=(
                    f"Type the approved {block.role} block {block.id} into the selected text frame. "
                    f"Preview: {block.text[:80]!r}."
                ),
                risk="high",
                args={"text": block.text, "kind": "copy", "block_id": block.id},
            )
        )
    return actions


def _document_actions(
    observation: Observation,
    working_copy: Path | None,
    plan: BrochurePlan | None,
) -> list[CandidateAction]:
    actions: list[CandidateAction] = []
    indesign = "indesign" in observation.application_guess.lower() or _titles_have(observation, "indesign")
    if working_copy is not None and not observation.document_open:
        actions.append(
            CandidateAction(
                id="open_working_copy",
                name=ActionName.open_working_copy,
                description=(
                    "Open the working copy with the Windows file association. "
                    "The original template is not opened."
                ),
                risk="medium",
                args={"path": str(working_copy)},
            )
        )
    if indesign and not observation.document_open:
        actions.append(
            _simple(
                "open_dialog",
                ActionName.open_file_dialog,
                "medium",
                "Open InDesign's Open dialog with Ctrl+O so the working copy can be chosen.",
                {"shortcut": "ctrl+o"},
            )
        )
    if observation.document_open:
        actions.extend(
            [
                _simple("scroll_down", ActionName.scroll, "low", "Scroll down a small amount inside the document window.", {"amount": -1}),
                _simple("scroll_up", ActionName.scroll, "low", "Scroll up a small amount inside the document window.", {"amount": 1}),
                _simple("key_pageup", ActionName.press_key, "low", "Go to the previous page with Page Up.", {"key": "pageup"}),
                _simple("key_pagedown", ActionName.press_key, "low", "Go to the next page with Page Down.", {"key": "pagedown"}),
                _simple("key_fit", ActionName.press_key, "low", "Fit the page in the window with Ctrl+0.", {"key": "ctrl+0"}),
                _simple("key_zoom_in", ActionName.press_key, "low", "Zoom in with Ctrl+plus.", {"key": "ctrl+add"}),
                _simple("key_undo", ActionName.press_key, "medium", "Undo the last InDesign edit with Ctrl+Z.", {"key": "ctrl+z"}),
                _simple("key_save", ActionName.press_key, "high", "Save the open working copy with Ctrl+S. This does not save over the template.", {"key": "ctrl+s"}),
                _simple(
                    "key_export",
                    ActionName.press_key,
                    "high",
                    "Open InDesign's Export dialog with Ctrl+E. Prefer this after the approved copy is in the document.",
                    {"key": "ctrl+e"},
                ),
            ]
        )
        if plan and plan.image_paths:
            actions.append(
                _simple(
                    "place_dialog",
                    ActionName.open_file_dialog,
                    "medium",
                    "Open InDesign's Place dialog with Ctrl+D so an approved image can be chosen.",
                    {"shortcut": "ctrl+d"},
                )
            )
    return actions


def _paths_for_dialog(
    kind: str,
    working_copy: Path | None,
    pdf_path: Path,
    plan: BrochurePlan | None,
) -> list[str]:
    if kind == "export":
        return [str(pdf_path)]
    if kind == "place" and plan is not None:
        return list(plan.image_paths)
    if kind in {"open", "save"} and working_copy is not None:
        return [str(working_copy)]
    return []


def _dialog_kind(name: str) -> str:
    lowered = name.lower()
    if "export" in lowered:
        return "export"
    if "place" in lowered:
        return "place"
    if "open" in lowered:
        return "open"
    if "save" in lowered:
        return "save"
    return "other"


def _titles_have(observation: Observation, needle: str) -> bool:
    blob = " ".join(observation.windows + ([observation.active_window] if observation.active_window else []))
    return needle in blob.lower()


def _simple(action_id: str, name: ActionName, risk: str, description: str, args: dict) -> CandidateAction:
    return CandidateAction(id=action_id, name=name, description=description, risk=risk, args=args)  # type: ignore[arg-type]


def _slug(prefix: str, value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in value.lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return f"{prefix}_{cleaned[:40] or 'item'}"


def _cap(items: list[CandidateAction]) -> list[CandidateAction]:
    seen: set[str] = set()
    kept: list[CandidateAction] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        kept.append(item)
        if len(kept) >= 40:
            break
    return kept
