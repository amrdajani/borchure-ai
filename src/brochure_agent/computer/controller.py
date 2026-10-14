"""Computer-control surface. Backends execute already validated actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from brochure_agent.models import ActionResult, CandidateAction, UIElement


@dataclass
class Capture:
    path: str | None
    width: int
    height: int
    mouse_x: int
    mouse_y: int


class ComputerController(Protocol):
    backend_name: str

    def capture(self) -> Capture: ...

    def list_windows(self) -> list[str]: ...

    def accessibility_elements(self) -> list[UIElement]: ...

    def active_window(self) -> str | None: ...

    def dialog_name(self) -> str | None: ...

    def execute(self, action: CandidateAction) -> ActionResult: ...
