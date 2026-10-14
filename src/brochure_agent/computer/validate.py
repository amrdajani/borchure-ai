"""Allowed keys and checks shared by every computer backend.

The validator does not ask a model what a coordinate means. It checks that
the action still matches an element or string from the current observation.
"""

from __future__ import annotations

from pathlib import Path

from brochure_agent.models import ActionName, CandidateAction, Observation
from brochure_agent.safety import SafetyError, clean_typed_text, control_is_destructive

ALLOWED_KEYS = {
    "escape": ("escape",),
    "enter": ("enter",),
    "tab": ("tab",),
    "backspace": ("backspace",),
    "up": ("up",),
    "down": ("down",),
    "left": ("left",),
    "right": ("right",),
    "home": ("home",),
    "end": ("end",),
    "pageup": ("pageup",),
    "pagedown": ("pagedown",),
    "ctrl+z": ("ctrl", "z"),
    "ctrl+s": ("ctrl", "s"),
    "ctrl+o": ("ctrl", "o"),
    "ctrl+d": ("ctrl", "d"),
    "ctrl+e": ("ctrl", "e"),
    "ctrl+0": ("ctrl", "0"),
    "ctrl+=": ("ctrl", "="),
    "ctrl+-": ("ctrl", "-"),
    "ctrl+add": ("ctrl", "add"),
    "ctrl+a": ("ctrl", "a"),
    "alt+n": ("alt", "n"),
    "alt+d": ("alt", "d"),
}

_DOCUMENT_KEYS = {"ctrl+s", "ctrl+e", "ctrl+d", "ctrl+0", "ctrl+=", "ctrl+-", "ctrl+add", "pageup", "pagedown"}


class ValidationError(SafetyError):
    pass


def chord(key: str) -> tuple[str, ...]:
    try:
        return ALLOWED_KEYS[key]
    except KeyError as exc:
        raise ValidationError(f"Key is not allowed: {key}") from exc


def validate_action(
    action: CandidateAction,
    observation: Observation,
    *,
    allowlist: set[str],
    working_copy: Path | None,
    output_root: Path,
    overwrite_confirmed: bool,
) -> None:
    name = action.name
    if name in {ActionName.stop_agent, ActionName.request_human}:
        return
    if name in {ActionName.click, ActionName.double_click, ActionName.move_mouse}:
        _point_inside_screen(action, observation)
    if name in {ActionName.click, ActionName.double_click}:
        _point_on_element(action, observation)
    if name == ActionName.type_text:
        _validate_text(action, observation, allowlist)
    if name == ActionName.press_key:
        key = str(action.args.get("key", ""))
        chord(key)
        if key in _DOCUMENT_KEYS:
            _require_working_document(observation, working_copy)
        if key == "enter" and observation.dialog and not _enter_allowed(action, overwrite_confirmed):
            raise ValidationError("Enter in this dialog needs overwrite confirmation.")
    if name == ActionName.open_file_dialog:
        shortcut = str(action.args.get("shortcut", ""))
        if shortcut not in {"ctrl+o", "ctrl+d"}:
            raise ValidationError("File dialog shortcut is not allowed.")
        if "indesign" not in (observation.active_window or "").lower():
            raise ValidationError("File dialogs are only opened from an InDesign window.")
    if name == ActionName.open_working_copy:
        _validate_working_copy(action, working_copy, output_root)
    if name == ActionName.scroll:
        amount = action.args.get("amount")
        if not isinstance(amount, int) or amount == 0 or abs(amount) > 3:
            raise ValidationError("Scroll amount must be a small non-zero integer.")
    if name == ActionName.wait:
        seconds = action.args.get("seconds")
        if not isinstance(seconds, (int, float)) or not 0.1 <= float(seconds) <= 5:
            raise ValidationError("Wait must be between 0.1 and 5 seconds.")
    if name == ActionName.focus_window:
        title = str(action.args.get("title", ""))
        if title not in observation.windows and title != observation.active_window:
            raise ValidationError("Focus target is not a listed window.")


def _point_inside_screen(action: CandidateAction, observation: Observation) -> None:
    x = action.args.get("x")
    y = action.args.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise ValidationError("Pointer action is missing coordinates.")
    if not (0 <= float(x) < observation.image_width and 0 <= float(y) < observation.image_height):
        raise ValidationError("Pointer target is outside the captured screen.")


def _point_on_element(action: CandidateAction, observation: Observation) -> None:
    element_id = action.args.get("element_id")
    element = next((item for item in observation.elements if item.id == element_id), None)
    if element is None or element.bbox is None:
        raise ValidationError("Click target is not in the current observation.")
    if control_is_destructive(element.name):
        raise ValidationError("Refusing a control whose name is destructive.")
    x, y, width, height = element.bbox
    px = float(action.args["x"])
    py = float(action.args["y"])
    if not (x - 2 <= px <= x + width + 2 and y - 2 <= py <= y + height + 2):
        raise ValidationError("Click is not inside the observed element.")


def _validate_text(action: CandidateAction, observation: Observation, allowlist: set[str]) -> None:
    kind = action.args.get("kind")
    text = action.args.get("text")
    if not isinstance(text, str) or text not in allowlist:
        raise ValidationError("Typed text is not on the allowlist.")
    clean_typed_text(text)
    if kind == "copy":
        if observation.dialog:
            raise ValidationError("Brochure copy is not typed into a file dialog.")
        if not observation.text_editing:
            raise ValidationError("Brochure copy is typed only into a selected text frame.")
    elif kind == "path":
        if not observation.dialog:
            raise ValidationError("Paths are typed only into a file dialog.")
    else:
        raise ValidationError("Typed text needs kind copy or path.")


def _validate_working_copy(action: CandidateAction, working_copy: Path | None, output_root: Path) -> None:
    if working_copy is None:
        raise ValidationError("No working copy is available.")
    raw = action.args.get("path")
    if raw != str(working_copy):
        raise ValidationError("Only the prepared working copy can be opened.")
    path = Path(str(raw)).resolve()
    if path.suffix.lower() not in {".indd", ".idml"}:
        raise ValidationError("Working copy must be an InDesign document.")
    try:
        path.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValidationError("Working copy is outside the output folder.") from exc


def _require_working_document(observation: Observation, working_copy: Path | None) -> None:
    if working_copy is None or not observation.document_open:
        raise ValidationError("Document keys require the working copy to be the open document.")
    stem = working_copy.stem.lower()
    title = (observation.active_window or "").lower()
    if stem not in title and stem not in " ".join(observation.windows).lower():
        raise ValidationError("Document keys require the working-copy window.")


def _enter_allowed(action: CandidateAction, overwrite_confirmed: bool) -> bool:
    target = action.args.get("target")
    if not target:
        return True
    if Path(str(target)).exists() and not overwrite_confirmed:
        return False
    return True
