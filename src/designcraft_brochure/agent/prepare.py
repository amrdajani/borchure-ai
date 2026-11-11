"""Read the proposal, let Jev pick a template, and prepare a working copy."""

from __future__ import annotations

from pathlib import Path

from designcraft_brochure.agent.loop import Progress, RunContext
from designcraft_brochure.config import Settings
from designcraft_brochure.content.plan import ConfirmedFacts, build_plan
from designcraft_brochure.jev.brain import Brain
from designcraft_brochure.jev.questions import template_questions
from designcraft_brochure.models import Proposal, RunOutcome, TemplateInfo
from designcraft_brochure.proposal.extract import extract_proposal
from designcraft_brochure.proposal.reader import ReadError, read_source
from designcraft_brochure.safety import SafetyError, sha256_file
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
    facts: ConfirmedFacts | None = None,
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
        return RunOutcome(status="failed", message="No .indd, .idml, or .designcraft templates were found.")
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
    except FileExistsError as exc:
        return RunOutcome(status="needs_assistance", message=str(exc))
    except SafetyError as exc:
        return RunOutcome(status="failed", message=str(exc))
    plan = build_plan(proposal, chosen, brain, settings, tour_code=tour_code, facts=facts)
    _stage_template_links(working, plan)
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


def _stage_template_links(working: Path, plan) -> None:
    """Leave fetched pictures beside the working file, where a missing link is looked up."""
    if not plan.template_links:
        return
    folder = working.parent / "Links"
    folder.mkdir(parents=True, exist_ok=True)
    for path in plan.template_links:
        source = Path(path)
        if not source.is_file():
            continue
        target = folder / source.name
        if target.resolve() == source.resolve():
            continue
        target.write_bytes(source.read_bytes())


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
