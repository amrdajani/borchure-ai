"""Read the proposal, let Jev pick a template, and prepare a working copy."""

from __future__ import annotations

from pathlib import Path

from brochure_agent.agent.loop import Progress, RunContext
from brochure_agent.config import Settings
from brochure_agent.content.plan import build_plan
from brochure_agent.jev.brain import Brain
from brochure_agent.jev.questions import template_questions
from brochure_agent.models import Proposal, RunOutcome, TemplateInfo
from brochure_agent.proposal.extract import extract_proposal
from brochure_agent.proposal.reader import ReadError, read_source
from brochure_agent.safety import SafetyError, sha256_file
from brochure_agent.templates.library import copy_working_file, discover_templates


def prepare_run(
    *,
    proposal_path: Path,
    template_root: Path,
    output_dir: Path,
    preview_dir: Path,
    log_dir: Path,
    brain: Brain,
    settings: Settings,
    overwrite: bool,
    backend: str,
    ocr: bool,
    progress: Progress,
) -> RunContext | RunOutcome:
    progress.say("Reading proposal.")
    try:
        source = read_source(proposal_path, preview_dir / "proposal-images", ocr=ocr)
    except ReadError as exc:
        return RunOutcome(status="failed", message=str(exc))
    progress.say("Inspecting templates.")
    try:
        templates = discover_templates(template_root)
    except SafetyError as exc:
        return RunOutcome(status="failed", message=str(exc))
    if not templates:
        return RunOutcome(status="failed", message="No .indd or .idml templates were found.")
    proposal = extract_proposal(source, brain)
    progress.say("Selecting template.")
    chosen = _choose_template(proposal, templates, brain)
    if chosen is None:
        return RunOutcome(status="needs_assistance", message="Jev did not find a suitable template.")
    try:
        working = copy_working_file(chosen, output_dir, template_root, overwrite=overwrite)
    except FileExistsError as exc:
        return RunOutcome(status="needs_assistance", message=str(exc))
    except SafetyError as exc:
        return RunOutcome(status="failed", message=str(exc))
    plan = build_plan(proposal, chosen, brain, settings)
    if not plan.blocks:
        return RunOutcome(
            status="needs_assistance",
            message="No source-backed brochure text was available.",
            working_copy=str(working),
        )
    pdf_path = output_dir / f"{working.stem}.pdf"
    if pdf_path.exists() and not overwrite:
        return RunOutcome(
            status="needs_assistance",
            message=f"Output PDF already exists: {pdf_path}",
            working_copy=str(working),
        )
    allowlist = {block.text for block in plan.blocks if block.source_backed}
    allowlist.add(str(pdf_path))
    allowlist.add(str(working))
    allowlist.update(plan.image_paths)
    brief = proposal.model_dump(exclude={"raw_text"})
    return RunContext(
        goal=(
            "Using the visible InDesign window, put the approved brochure text and images "
            "into the working copy, save it, and export a PDF."
        ),
        proposal_brief=brief,
        plan=plan,
        template_path=Path(chosen.path),
        template_id=chosen.id,
        template_sha256=sha256_file(Path(chosen.path)),
        template_root=template_root,
        proposal_path=proposal_path,
        working_copy=working,
        pdf_path=pdf_path,
        output_root=output_dir,
        preview_dir=preview_dir,
        journal_path=log_dir / "actions.jsonl",
        checkpoint_path=log_dir / "checkpoint.json",
        pause_path=log_dir / "PAUSE",
        allowlist=allowlist,
        overwrite_confirmed=overwrite,
        backend=backend,
    )


def _choose_template(proposal: Proposal, templates: list[TemplateInfo], brain: Brain) -> TemplateInfo | None:
    payload = [
        {
            "id": item.id,
            "name": Path(item.path).name,
            "category": item.category,
            "page_count": item.page_count,
            "image_frame_count": item.image_frame_count,
            "text_preview": item.text_preview,
            "notes": item.notes,
        }
        for item in templates
    ]
    judgment = brain.judge(
        {
            "proposal": proposal.model_dump(exclude={"raw_text"}),
            "templates": payload,
        },
        template_questions(payload),
    )
    pick = judgment.choice("template")
    if pick.choice == "none" or pick.confidence < 0.35:
        return None
    for item in templates:
        if item.id == pick.choice:
            return item
    return None
