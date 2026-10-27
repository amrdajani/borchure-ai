"""Read the proposal, let Jev pick a template, and prepare a working copy."""

from __future__ import annotations

from pathlib import Path

from designcraft_brochure.agent.loop import Progress, RunContext
from designcraft_brochure.config import Settings
from designcraft_brochure.content.plan import build_plan
from designcraft_brochure.jev.brain import Brain
from designcraft_brochure.jev.questions import template_questions
from designcraft_brochure.models import Proposal, RunOutcome, TemplateInfo
from designcraft_brochure.proposal.extract import extract_proposal
from designcraft_brochure.proposal.reader import ReadError, read_source
from designcraft_brochure.safety import SafetyError, sha256_file
from designcraft_brochure.templates.indd import convert_indd_to_idml
from designcraft_brochure.templates.library import copy_working_file, discover_templates, openable_templates


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
    use_uploaded_template: bool = False,
    tour_code: str | None = None,
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
    usable = openable_templates(templates)
    if not usable:
        if any(item.suffix == ".indd" for item in templates):
            return RunOutcome(
                status="failed",
                message="Only .indd templates were found, and indd-utils is not installed to convert them.",
            )
        return RunOutcome(status="failed", message="No .idml or .designcraft templates were found.")
    proposal = extract_proposal(source, brain)
    if use_uploaded_template and len(usable) == 1:
        progress.say("Using the uploaded template.")
        chosen = usable[0]
    else:
        progress.say("Selecting template.")
        chosen = _choose_template(proposal, usable, brain)
        if chosen is None:
            return RunOutcome(status="needs_assistance", message="Jev did not find a suitable template.")
    try:
        working = copy_working_file(chosen, output_dir, template_root, overwrite=overwrite)
        if working.suffix.lower() == ".indd":
            progress.say("Converting the working copy from INDD to IDML.")
            converted = working.with_suffix(".idml")
            if converted.exists() and not overwrite:
                return RunOutcome(
                    status="needs_assistance",
                    message=f"Converted IDML already exists: {converted}",
                    working_copy=str(working),
                )
            template_hash = sha256_file(Path(chosen.path))
            convert_indd_to_idml(working, converted)
            if sha256_file(Path(chosen.path)) != template_hash:
                return RunOutcome(status="failed", message="The original .indd template changed during conversion.")
            working = converted
    except FileExistsError as exc:
        return RunOutcome(status="needs_assistance", message=str(exc))
    except SafetyError as exc:
        return RunOutcome(status="failed", message=str(exc))
    plan = build_plan(proposal, chosen, brain, settings, tour_code=tour_code)
    if not plan.blocks:
        return RunOutcome(
            status="needs_assistance",
            message="No source-backed brochure text was available.",
            working_copy=str(working),
        )
    native_path = output_dir / "working" / f"{working.stem}.designcraft"
    pdf_path = output_dir / f"{working.stem}.pdf"
    idml_export_path = output_dir / "working" / f"{working.stem}-edited.idml"
    for destination in (native_path, pdf_path, idml_export_path):
        if destination.exists() and not overwrite:
            return RunOutcome(
                status="needs_assistance",
                message=f"Output already exists: {destination}",
                working_copy=str(working),
            )
    allowlist = {block.text for block in plan.blocks if block.source_backed}
    brief = proposal.model_dump(exclude={"raw_text"})
    return RunContext(
        goal=(
            "Using DesignCraft, put the approved brochure text and images into the working copy, "
            "save a native .designcraft file, and export a PDF."
        ),
        proposal_brief=brief,
        plan=plan,
        template_path=Path(chosen.path),
        template_id=chosen.id,
        template_sha256=sha256_file(Path(chosen.path)),
        template_root=template_root,
        proposal_path=proposal_path,
        working_copy=working,
        native_path=native_path,
        pdf_path=pdf_path,
        idml_export_path=idml_export_path,
        output_root=output_dir,
        preview_dir=preview_dir,
        journal_path=log_dir / "actions.jsonl",
        checkpoint_path=log_dir / "checkpoint.json",
        pause_path=log_dir / "PAUSE",
        allowlist=allowlist,
        image_paths=list(plan.image_paths),
        overwrite_confirmed=overwrite,
        backend=backend,
        control_host=settings.control_host,
        control_port=settings.control_port,
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
