"""Shared data shapes for the DesignCraft brochure agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    openable: bool = False


class StoryView(BaseModel):
    id: int
    preview: str = ""
    length: int = 0
    overset: bool = False


class FrameView(BaseModel):
    id: int
    kind: str
    story: int | None = None


class Observation(BaseModel):
    document_open: bool = False
    path: str | None = None
    page_count: int = 0
    stories: list[StoryView] = Field(default_factory=list)
    frames: list[FrameView] = Field(default_factory=list)
    error: str | None = None
    limits: list[str] = Field(default_factory=list)

    def summary(self) -> dict:
        return {
            "document_open": self.document_open,
            "page_count": self.page_count,
            "path_open": bool(self.path),
            "stories": [
                {
                    "id": story.id,
                    "preview": story.preview[:180],
                    "length": story.length,
                    "overset": story.overset,
                }
                for story in self.stories[:24]
            ],
            "frames": [
                {"id": frame.id, "kind": frame.kind, "story": frame.story}
                for frame in self.frames[:24]
            ],
            "error": self.error,
            "limits": self.limits,
        }


class CandidateAction(BaseModel):
    id: str
    description: str
    risk: Literal["low", "medium", "high"]
    method: str
    command: str | None = None
    params: dict = Field(default_factory=dict)


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
    native_path: str | None = None
    idml_path: str | None = None
    actions: int = 0
    quality_status: str | None = None
    simulated: bool = False


class Checkpoint(BaseModel):
    version: int = 1
    proposal_path: str
    template_path: str
    template_root: str
    template_id: str
    template_sha256: str
    working_copy: str
    native_path: str
    pdf_path: str
    idml_export_path: str
    plan: BrochurePlan
    proposal_brief: dict
    allowlist: list[str]
    image_paths: list[str] = Field(default_factory=list)
    action_count: int = 0
    history: list[dict] = Field(default_factory=list)
    failures: dict[str, int] = Field(default_factory=dict)
    overwrite_confirmed: bool = False
    backend: str = "simulated"
    control_host: str = "127.0.0.1"
    control_port: int = 7979
