"""Windows desktop control through the visible UI.

PyAutoGUI sends pointer and keyboard input. UI Automation, via pywinauto,
reads window titles and accessible controls. Opening the working copy uses
os.startfile, which is the ShellExecute path used when a person double-clicks
a document. This module does not call InDesign's scripting APIs.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

from brochure_agent.computer.controller import Capture
from brochure_agent.computer.validate import chord
from brochure_agent.models import ActionName, ActionResult, CandidateAction, UIElement


class WindowsComputer:
    backend_name = "windows"

    def __init__(self, screenshot_dir: Path, settle_seconds: float = 0.6) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsComputer can only run on Windows.")
        self.screenshot_dir = screenshot_dir
        self.settle_seconds = settle_seconds
        self._pyautogui = None
        self._desktop = None
        self._prepare()

    def capture(self) -> Capture:
        pyautogui = self._gui()
        image = pyautogui.screenshot()
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"screen-{time.time_ns()}.png"
        image.save(path)
        point = pyautogui.position()
        return Capture(str(path), image.width, image.height, int(point.x), int(point.y))

    def list_windows(self) -> list[str]:
        titles = []
        for window in self._desktop.windows():
            title = window.window_text()
            if title:
                titles.append(title)
        return titles[:30]

    def active_window(self) -> str | None:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or None

    def dialog_name(self) -> str | None:
        for title in self.list_windows():
            lowered = title.lower()
            for label in ("export", "place", "open a file", "save as"):
                if label in lowered:
                    return title
        return None

    def accessibility_elements(self) -> list[UIElement]:
        elements: list[UIElement] = []
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            window = self._desktop.window(handle=hwnd)
            for index, control in enumerate(window.descendants()):
                if index >= 60:
                    break
                info = control.element_info
                rect = info.rectangle
                name = (info.name or "")[:80]
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                elements.append(
                    UIElement(
                        id=f"u{index + 1}",
                        source="accessibility",
                        role=str(info.control_type or "unknown"),
                        name=name,
                        bbox=(float(rect.left), float(rect.top), float(rect.width()), float(rect.height())),
                    )
                )
        except Exception:
            return []
        return elements

    def execute(self, action: CandidateAction) -> ActionResult:
        try:
            detail = self._dispatch(action)
            time.sleep(self.settle_seconds)
        except Exception as exc:
            return ActionResult(
                action_id=action.id,
                name=action.name.value,
                attempted=bool(getattr(exc, "attempted", False)),
                error=f"{exc.__class__.__name__}: {exc}"[:200],
            )
        return ActionResult(action_id=action.id, name=action.name.value, attempted=True, detail=detail)

    def _dispatch(self, action: CandidateAction) -> str:
        name = action.name
        gui = self._gui()
        if name == ActionName.take_screenshot:
            shot = self.capture()
            return shot.path or "captured"
        if name == ActionName.list_windows:
            return f"{len(self.list_windows())} windows"
        if name == ActionName.inspect_visible_state:
            return f"{len(self.accessibility_elements())} controls"
        if name == ActionName.focus_window:
            self._focus(str(action.args["title"]))
            return "focused"
        if name == ActionName.click:
            gui.click(int(action.args["x"]), int(action.args["y"]))
            return "clicked"
        if name == ActionName.double_click:
            gui.doubleClick(int(action.args["x"]), int(action.args["y"]))
            return "double-clicked"
        if name == ActionName.move_mouse:
            gui.moveTo(int(action.args["x"]), int(action.args["y"]), duration=0.15)
            return "moved"
        if name == ActionName.type_text:
            _type_unicode(str(action.args["text"]))
            return "typed allowlisted text"
        if name == ActionName.press_key:
            keys = chord(str(action.args["key"]))
            if len(keys) == 1:
                gui.press(keys[0])
            else:
                gui.hotkey(*keys)
            return "key pressed"
        if name == ActionName.scroll:
            gui.scroll(int(action.args["amount"]))
            return "scrolled"
        if name == ActionName.wait:
            time.sleep(float(action.args["seconds"]))
            return "waited"
        if name == ActionName.open_file_dialog:
            keys = chord(str(action.args["shortcut"]))
            gui.hotkey(*keys)
            return "dialog shortcut sent"
        if name == ActionName.open_working_copy:
            os.startfile(str(action.args["path"]))  # noqa: S606 - validated document path, not a shell command
            return "asked Windows to open the working copy"
        raise RuntimeError(f"Unsupported action {name.value}")

    def _focus(self, title: str) -> None:
        needle = title.lower()
        for window in self._desktop.windows():
            if needle in window.window_text().lower():
                window.set_focus()
                return
        raise RuntimeError("window not found")

    def _prepare(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            import pyautogui
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("pyautogui and pywinauto are required on Windows.") from exc
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1
        self._pyautogui = pyautogui
        self._desktop = Desktop(backend="uia")

    def _gui(self):
        if self._pyautogui is None:
            raise RuntimeError("Windows input is not initialized.")
        return self._pyautogui


def _type_unicode(text: str) -> None:
    """Send BMP characters with Win32 SendInput. Newlines are already removed."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_ushort),
            ("wParamH", ctypes.c_ushort),
        ]

    class INPUT(ctypes.Structure):
        class _UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _UNION)]

    if ctypes.sizeof(INPUT) not in (28, 40):
        raise OSError("Unexpected Win32 INPUT size.")
    key_up = 0x0002
    unicode_flag = 0x0004
    for character in text:
        code = ord(character)
        if code > 0xFFFF:
            continue
        events = (INPUT * 2)()
        events[0].type = 1
        events[0].ki = KEYBDINPUT(wScan=code, dwFlags=unicode_flag)
        events[1].type = 1
        events[1].ki = KEYBDINPUT(wScan=code, dwFlags=unicode_flag | key_up)
        sent = user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT))
        if sent != 2:
            raise OSError("SendInput did not accept the character.")
