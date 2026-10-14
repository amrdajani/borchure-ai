"""Shared data shapes for the brochure agent."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ActionName(str, Enum):
    take_screenshot = "take_screenshot"
    list_windows = "list_windows"
    focus_window = "focus_window"
    click = "click"
    double_click = "double_click"
    move_mouse = "move_mouse"
    type_text = "type_text"
    press_key = "press_key"
    scroll = "scroll"
    wait = "wait"
    inspect_visible_state = "inspect_visible_state"
    open_file_dialog = "open_file_dialog"
    open_working_copy = "open_working_copy"
    stop_agent = "stop_agent"
    request_human = "request_human"


class SourceDocument(BaseModel):
    path: str
    text: str
    images: list[str] = Field(default_factory=list)
    ocr_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    source_path: str
    raw_text: str
    company_name: str | None = None
    project_name: str | None = None
    category: str | None = None
    description: str | None = None
    services: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    specifications: list[str] = Field(default_factory=list)
    prices: list[str] = Field(default_factory=list)
    statistics: list[str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    calls_to_action: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    unclassified: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    injection_flagged: bool = False
    confirmed_by_jev: bool = False


class CopyBlock(BaseModel):
    id: str
    role: str
    text: str
    source_backed: bool = False


class BrochurePlan(BaseModel):
    template_id: str
    blocks: list[CopyBlock] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class TemplateInfo(BaseModel):
    id: str
    path: str
    category: str
    suffix: str
    page_count: int | None = None
    text_preview: str = ""
    image_frame_count: int | None = None
    notes: str = ""


class UIElement(BaseModel):
    id: str
    source: Literal["accessibility", "vision"]
    role: str
    name: str
    bbox: tuple[float, float, float, float] | None = None
    selected: bool = False
    issues: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    screenshot_path: str | None = None
    image_width: int
    image_height: int
    mouse_x: int
    mouse_y: int
    windows: list[str] = Field(default_factory=list)
    active_window: str | None = None
    dialog: str | None = None
    application_guess: str = "unknown"
    elements: list[UIElement] = Field(default_factory=list)
    vision_notes: str = ""
    vision_available: bool = False
    text_editing: bool = False
    document_open: bool = False
    limits: list[str] = Field(default_factory=list)

    def summary(self) -> dict:
        return {
            "active_window": self.active_window,
            "dialog": self.dialog,
            "application_guess": self.application_guess,
            "document_open": self.document_open,
            "text_editing": self.text_editing,
            "vision_available": self.vision_available,
            "vision_notes": self.vision_notes[:800],
            "windows": self.windows[:12],
            "limits": self.limits,
            "mouse": [self.mouse_x, self.mouse_y],
            "elements": [
                {
                    "id": element.id,
                    "source": element.source,
                    "role": element.role,
                    "name": element.name[:120],
                    "bbox": element.bbox,
                    "selected": element.selected,
                    "issues": element.issues,
                }
                for element in self.elements[:40]
            ],
        }


class CandidateAction(BaseModel):
    id: str
    name: ActionName
    description: str
    risk: Literal["low", "medium", "high"]
    args: dict = Field(default_factory=dict)


class ActionResult(BaseModel):
    action_id: str
    name: str
    attempted: bool
    local_ok: bool | None = None
    verified: bool | None = None
    ok: bool | None = None
    detail: str = ""
    error: str | None = None


class QualityReport(BaseModel):
    path: str
    exists: bool = False
    opened: bool = False
    page_count: int = 0
    page_sizes: list[tuple[float, float]] = Field(default_factory=list)
    blank_pages: list[int] = Field(default_factory=list)
    clipped_pages: list[int] = Field(default_factory=list)
    pages_missing_images: bool = False
    missing_phrases: list[str] = Field(default_factory=list)
    preview_paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    status: Literal["missing", "ok", "issues", "unverified"] = "missing"


class ChoicePick(BaseModel):
    choice: str
    confidence: float
    probabilities: dict[str, float] = Field(default_factory=dict)


class RunOutcome(BaseModel):
    status: Literal["completed", "needs_assistance", "stopped", "failed"]
    message: str
    pdf_path: str | None = None
    working_copy: str | None = None
    actions: int = 0
    quality_status: str | None = None
    simulated: bool = False


class Checkpoint(BaseModel):
    version: int = 1
    proposal_path: str
    template_path: str
    template_id: str
    template_sha256: str
    working_copy: str
    pdf_path: str
    plan: BrochurePlan
    proposal_brief: dict
    allowlist: list[str]
    action_count: int = 0
    history: list[dict] = Field(default_factory=list)
    used_block_ids: list[str] = Field(default_factory=list)
    overwrite_confirmed: bool = False
    edits_attempted: int = 0
    backend: str = "simulated"
