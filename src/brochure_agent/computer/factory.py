"""Select the Windows desktop or the simulated desktop."""

from __future__ import annotations

import sys
from pathlib import Path

from brochure_agent.computer.controller import ComputerController
from brochure_agent.computer.simulated import SimulatedComputer


def create_controller(backend: str, screenshot_dir: Path, settle_seconds: float) -> ComputerController:
    selected = backend
    if selected == "auto":
        selected = "windows" if sys.platform == "win32" else "simulated"
        if selected == "simulated":
            raise RuntimeError(
                "This computer is not Windows. Adobe InDesign control requires Windows. "
                "Re-run with --backend simulated to exercise the decision loop without InDesign."
            )
    if selected == "windows":
        if sys.platform != "win32":
            raise RuntimeError("The windows backend only runs on Windows.")
        from brochure_agent.computer.windows import WindowsComputer

        return WindowsComputer(screenshot_dir, settle_seconds=settle_seconds)
    if selected == "simulated":
        return SimulatedComputer(screenshot_dir)
    raise RuntimeError(f"Unknown computer backend: {backend}")
