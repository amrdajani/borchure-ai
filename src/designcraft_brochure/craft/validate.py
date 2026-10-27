"""Check a chosen command before it is sent.

Jev picks an id. The path, story id, and text were built by this program
from the live document and the approved plan. A choice cannot invent them.
"""

from __future__ import annotations

from pathlib import Path

from designcraft_brochure.craft.client import ALLOWED_COMMANDS, ALLOWED_METHODS
from designcraft_brochure.models import CandidateAction
from designcraft_brochure.safety import SafetyError, assert_not_template_target, is_suspicious


class CommandContext:
    def __init__(
        self,
        *,
        working_copy: Path,
        native_path: Path,
        pdf_path: Path,
        idml_export_path: Path,
        preview_dir: Path,
        template_root: Path,
        allowlist: set[str],
        image_paths: list[str],
        overwrite: bool,
        story_ids: set[int],
        frame_ids: set[int],
    ) -> None:
        self.working_copy = working_copy
        self.native_path = native_path
        self.pdf_path = pdf_path
        self.idml_export_path = idml_export_path
        self.preview_dir = preview_dir
        self.template_root = template_root
        self.allowlist = allowlist
        self.image_paths = {str(Path(path).resolve()) for path in image_paths}
        self.overwrite = overwrite
        self.story_ids = story_ids
        self.frame_ids = frame_ids


def validate_action(action: CandidateAction, context: CommandContext) -> None:
    if action.method in {"agent.stop", "agent.assist"}:
        return
    if action.method not in ALLOWED_METHODS:
        raise SafetyError(f"Refusing to send control method {action.method!r}.")
    if action.method == "document.inspect":
        if action.params:
            raise SafetyError("document.inspect takes no parameters.")
        return
    if action.method == "ui.render":
        _validate_render(action.params, context)
        return
    if action.method != "engine.execute":
        raise SafetyError(f"Refusing to send control method {action.method!r}.")
    command = action.command
    if command not in ALLOWED_COMMANDS:
        raise SafetyError(f"Refusing to run DesignCraft command {command!r}.")
    if command in {"file.open", "file.openIdml"}:
        _expect_path(action.params, context.working_copy)
        _open_command_matches_suffix(command, context.working_copy)
        return
    if command == "story.setText":
        _validate_story_text(action.params, context)
        return
    if command == "file.place":
        _validate_place(action.params, context)
        return
    if command == "file.saveAs":
        _expect_output(action.params, context.native_path, context, suffix=".designcraft")
        return
    if command == "file.exportPdf":
        _expect_output(action.params, context.pdf_path, context, suffix=".pdf")
        return
    if command == "file.exportIdml":
        _expect_output(action.params, context.idml_export_path, context, suffix=".idml")
        return
    raise SafetyError(f"Refusing to run DesignCraft command {command!r}.")


def _validate_render(params: dict, context: CommandContext) -> None:
    allowed = {"page", "scale", "path"}
    if set(params) - allowed:
        raise SafetyError("ui.render received an unexpected parameter.")
    path = params.get("path")
    if path is not None:
        _must_equal(Path(str(path)), context.preview_dir / "page-render.png")


def _validate_story_text(params: dict, context: CommandContext) -> None:
    story = params.get("story")
    text = params.get("text")
    if not isinstance(story, int) or isinstance(story, bool) or story not in context.story_ids:
        raise SafetyError("story.setText must name a story from the current document.")
    if not isinstance(text, str) or not text.strip():
        raise SafetyError("Refusing to send empty story text.")
    if len(text) > 8000:
        raise SafetyError("Refusing to send story text over the length limit.")
    parts = [part for part in text.split("\n") if part.strip()]
    if not parts:
        raise SafetyError("Refusing to send empty story text.")
    if any(part not in context.allowlist for part in parts):
        raise SafetyError("Refusing to send text that is not an approved source block.")
    if any(is_suspicious(part) for part in parts):
        raise SafetyError("Refusing to send instruction-like text.")


def _validate_place(params: dict, context: CommandContext) -> None:
    path = params.get("path")
    if not isinstance(path, str):
        raise SafetyError("file.place needs an image path from the proposal.")
    resolved = str(Path(path).resolve())
    if resolved not in context.image_paths:
        raise SafetyError("Refusing to place an image that did not come from the proposal.")
    frame = params.get("frame")
    if frame is not None:
        if not isinstance(frame, int) or isinstance(frame, bool) or frame not in context.frame_ids:
            raise SafetyError("file.place must name a frame from the current document.")
        return
    if params.get("x") != 36 or params.get("y") not in {36, 220, 400} or params.get("width") != 180:
        raise SafetyError("file.place is only allowed at a prepared position.")


def _expect_path(params: dict, expected: Path) -> None:
    raw = params.get("path")
    if not isinstance(raw, str):
        raise SafetyError("This command needs a path.")
    _must_equal(Path(raw), expected)


def _expect_output(params: dict, expected: Path, context: CommandContext, *, suffix: str) -> None:
    _expect_path(params, expected)
    if expected.suffix.lower() != suffix:
        raise SafetyError(f"Refusing to write a destination that is not {suffix}.")
    assert_not_template_target(context.working_copy, expected, context.template_root)
    if expected.exists() and not context.overwrite:
        raise SafetyError(f"Refusing to overwrite {expected.name} without --yes.")


def _open_command_matches_suffix(command: str, working_copy: Path) -> None:
    suffix = working_copy.suffix.lower()
    if suffix == ".idml" and command != "file.openIdml":
        raise SafetyError("An IDML working copy must be opened with file.openIdml.")
    if suffix == ".designcraft" and command != "file.open":
        raise SafetyError("A native working copy must be opened with file.open.")
    if suffix not in {".idml", ".designcraft"}:
        raise SafetyError("DesignCraft cannot open this working copy.")


def _must_equal(actual: Path, expected: Path) -> None:
    if actual.expanduser().resolve() != expected.expanduser().resolve():
        raise SafetyError("Refusing a path that was not prepared for this run.")
