"""A desktop stand-in for tests and for machines that are not Windows.

It records actions and pretends to be InDesign only far enough to exercise
the decision loop. It does not claim to be Adobe InDesign.
"""

from __future__ import annotations

import time
from pathlib import Path

from brochure_agent.computer.controller import Capture
from brochure_agent.computer.validate import chord
from brochure_agent.models import ActionName, ActionResult, CandidateAction, UIElement


class SimulatedComputer:
    backend_name = "simulated"

    def __init__(self, screenshot_dir: Path) -> None:
        self.screenshot_dir = screenshot_dir
        self.mouse_x = 100
        self.mouse_y = 100
        self.windows = ["Program Manager"]
        self.active = "Program Manager"
        self.dialog: str | None = None
        self.document_open = False
        self.selected = False
        self.document_text = ""
        self.pending_path: str | None = None
        self.opened_path: str | None = None
        self.typed: list[str] = []
        self.keys: list[str] = []
        self.frame = UIElement(
            id="frame-1",
            source="accessibility",
            role="text_frame",
            name="Headline placeholder",
            bbox=(100, 120, 420, 80),
            selected=False,
        )

    def capture(self) -> Capture:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"sim-{len(list(self.screenshot_dir.glob('sim-*.png'))) + 1}.png"
        self._draw(path)
        return Capture(str(path), 1280, 720, self.mouse_x, self.mouse_y)

    def list_windows(self) -> list[str]:
        return list(self.windows)

    def active_window(self) -> str | None:
        return self.active

    def dialog_name(self) -> str | None:
        return self.dialog

    def accessibility_elements(self) -> list[UIElement]:
        if not self.document_open or self.dialog:
            return []
        self.frame.selected = self.selected
        return [self.frame.model_copy(deep=True)]

    def execute(self, action: CandidateAction) -> ActionResult:
        try:
            detail = self._dispatch(action)
        except Exception as exc:
            return ActionResult(
                action_id=action.id,
                name=action.name.value,
                attempted=False,
                error=f"{exc.__class__.__name__}: {exc}"[:200],
            )
        return ActionResult(
            action_id=action.id,
            name=action.name.value,
            attempted=True,
            detail=detail,
        )

    def _dispatch(self, action: CandidateAction) -> str:
        name = action.name
        if name == ActionName.move_mouse:
            self.mouse_x = int(action.args["x"])
            self.mouse_y = int(action.args["y"])
            return "moved"
        if name == ActionName.click:
            self.selected = False
            return "clicked"
        if name == ActionName.double_click:
            self.selected = True
            return "double-clicked"
        if name == ActionName.type_text:
            text = str(action.args["text"])
            self.typed.append(text)
            if self.dialog:
                self.pending_path = text
            elif self.selected:
                self.document_text = (self.document_text + " " + text).strip()
            return "typed allowlisted text"
        if name == ActionName.press_key:
            key = str(action.args["key"])
            chord(key)
            self.keys.append(key)
            return self._key(key)
        if name == ActionName.scroll:
            return "scrolled"
        if name == ActionName.wait:
            return "waited"
        if name == ActionName.focus_window:
            title = str(action.args["title"])
            if title not in self.windows:
                raise RuntimeError("window not found")
            self.active = title
            return "focused"
        if name == ActionName.open_file_dialog:
            shortcut = str(action.args["shortcut"])
            self.dialog = "Place" if shortcut == "ctrl+d" else "Open a File"
            return "dialog opened"
        if name == ActionName.open_working_copy:
            return self._open(str(action.args["path"]))
        if name in {ActionName.take_screenshot, ActionName.list_windows, ActionName.inspect_visible_state}:
            return "observed"
        raise RuntimeError(f"simulated backend does not run {name.value}")

    def _open(self, path: str) -> str:
        file_path = Path(path)
        title = f"{file_path.name} - Adobe InDesign"
        if title not in self.windows:
            self.windows.append(title)
        self.active = title
        self.document_open = True
        self.opened_path = path
        self.dialog = None
        return "opened working copy"

    def _key(self, key: str) -> str:
        if key == "escape":
            self.dialog = None
            self.pending_path = None
            return "dismissed dialog"
        if key == "ctrl+e" and self.document_open and not self.dialog:
            self.dialog = "Export"
            return "export dialog"
        if key == "ctrl+d" and self.document_open and not self.dialog:
            self.dialog = "Place"
            return "place dialog"
        if key == "ctrl+o" and not self.dialog:
            self.dialog = "Open a File"
            return "open dialog"
        if key == "enter" and self.dialog == "Export" and self.pending_path:
            self._write_pdf(Path(self.pending_path), self.document_text or " ")
            self.dialog = None
            return "exported"
        if key == "enter" and self.dialog:
            self.dialog = None
            return "confirmed dialog"
        return "key pressed"

    def _write_pdf(self, path: Path, text: str) -> None:
        import fitz

        path.parent.mkdir(parents=True, exist_ok=True)
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text[:500])
        document.save(path)
        document.close()

    def _draw(self, path: Path) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1280, 720), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 36), fill="black")
        draw.text((12, 10), self.active or "desktop", fill="white")
        image.save(path)
        time.sleep(0)
