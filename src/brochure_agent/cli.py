"""Command line for the brochure agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brochure_agent.agent.loop import Progress, RunContext, run_loop, run_poc
from brochure_agent.agent.prepare import prepare_run
from brochure_agent.computer.factory import create_controller
from brochure_agent.config import Settings
from brochure_agent.jev.brain import TypeSafeBrain
from brochure_agent.journal import load_checkpoint
from brochure_agent.models import RunOutcome
from brochure_agent.proposal.extract import extract_proposal
from brochure_agent.proposal.reader import ReadError, read_source
from brochure_agent.safety import sha256_file
from brochure_agent.templates.library import discover_templates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brochure-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Show what this machine can run")
    doctor.set_defaults(func=cmd_doctor)

    templates = sub.add_parser("list-templates", help="List InDesign templates")
    templates.add_argument("--templates", type=Path, default=Path("templates"))
    templates.set_defaults(func=cmd_templates)

    inspect = sub.add_parser("inspect", help="Read a proposal without calling Jev")
    inspect.add_argument("proposal", type=Path)
    inspect.add_argument("--ocr", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    poc = sub.add_parser("poc", help="Let Jev choose one harmless action")
    _add_backend(poc)
    poc.set_defaults(func=cmd_poc)

    run = sub.add_parser("run", help="Build a brochure from a proposal")
    run.add_argument("--proposal", type=Path, required=True)
    run.add_argument("--templates", type=Path, default=Path("templates"))
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--yes", action="store_true", help="Allow replacing an existing working copy or PDF")
    run.add_argument("--ocr", action="store_true")
    _add_backend(run)
    run.add_argument("--max-actions", type=int, default=None)
    run.set_defaults(func=cmd_run)

    resume = sub.add_parser("resume", help="Continue from logs/checkpoint.json")
    resume.add_argument("--checkpoint", type=Path, default=Path("logs/checkpoint.json"))
    _add_backend(resume)
    resume.add_argument("--max-actions", type=int, default=None)
    resume.set_defaults(func=cmd_resume)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Stopped by the operator.", flush=True)
        return 3
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_doctor(_args) -> int:
    settings = Settings.from_env()
    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    print(f"TypeSafe key configured: {bool(settings.typesafe_api_key)}")
    print(f"Jev model: {settings.model}")
    print(f"Copy model configured: {bool(settings.copy_enabled)}")
    print(f"Vision model configured: {bool(settings.vision_enabled)}")
    print(f"typesafe-sdk import: {_imports('typesafe_sdk')}")
    print(f"PyMuPDF import: {_imports('fitz')}")
    print(f"python-docx import: {_imports('docx')}")
    print(f"Pillow import: {_imports('PIL')}")
    print(f"pyautogui import: {_imports('pyautogui')}")
    print(f"pywinauto import: {_imports('pywinauto')}")
    print(f"pytesseract import: {_imports('pytesseract')}")
    if sys.platform != "win32":
        print("InDesign control was not tested here because this is not Windows.")
    return 0


def cmd_templates(args) -> int:
    found = discover_templates(args.templates)
    if not found:
        print("No templates found.")
        return 0
    for item in found:
        pages = item.page_count if item.page_count is not None else "?"
        print(f"{item.id}\t{item.category}\tpages={pages}\t{item.path}")
    return 0


def cmd_inspect(args) -> int:
    try:
        source = read_source(args.proposal, Path("previews") / "proposal-images", ocr=args.ocr)
    except ReadError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    proposal = extract_proposal(source, brain=None)
    print(proposal.model_dump_json(indent=2, exclude={"raw_text"}))
    return 0


def cmd_poc(args) -> int:
    settings = Settings.from_env()
    if not settings.typesafe_api_key:
        print("Set TYPESAFE_API_KEY before running the proof of concept.", file=sys.stderr)
        return 1
    computer = create_controller(args.backend, Path("logs") / "screens", settings.settle_seconds)
    _warn_backend(computer.backend_name)
    with TypeSafeBrain(settings.model) as brain:
        outcome = run_poc(brain, computer, settings)
    print(outcome.message)
    return _code(outcome)


def cmd_run(args) -> int:
    settings = Settings.from_env()
    if args.max_actions is not None:
        settings.max_actions = args.max_actions
    if not settings.typesafe_api_key:
        print("Set TYPESAFE_API_KEY before running the agent.", file=sys.stderr)
        return 1
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    log_dir = Path("logs")
    computer = create_controller(args.backend, log_dir / "screens", settings.settle_seconds)
    _warn_backend(computer.backend_name)
    progress = Progress()
    with TypeSafeBrain(settings.model) as brain:
        prepared = prepare_run(
            proposal_path=args.proposal,
            template_root=args.templates,
            output_dir=output,
            preview_dir=Path("previews"),
            log_dir=log_dir,
            brain=brain,
            settings=settings,
            overwrite=args.yes,
            backend=computer.backend_name,
            ocr=args.ocr,
            progress=progress,
        )
        if isinstance(prepared, RunOutcome):
            print(prepared.message)
            return _code(prepared)
        outcome = run_loop(prepared, brain, computer, settings, progress)
    _print_outcome(outcome)
    return _code(outcome)


def cmd_resume(args) -> int:
    settings = Settings.from_env()
    if args.max_actions is not None:
        settings.max_actions = args.max_actions
    if not settings.typesafe_api_key:
        print("Set TYPESAFE_API_KEY before resuming.", file=sys.stderr)
        return 1
    checkpoint = load_checkpoint(args.checkpoint)
    template = Path(checkpoint.template_path)
    if sha256_file(template) != checkpoint.template_sha256:
        print("The original template changed since the checkpoint. Not resuming.", file=sys.stderr)
        return 1
    working = Path(checkpoint.working_copy)
    if not working.is_file():
        print("The working copy is missing. Not resuming.", file=sys.stderr)
        return 1
    context = _context_from_checkpoint(checkpoint, args.checkpoint.parent)
    computer = create_controller(args.backend, Path("logs") / "screens", settings.settle_seconds)
    context.backend = computer.backend_name
    _warn_backend(computer.backend_name)
    with TypeSafeBrain(settings.model) as brain:
        outcome = run_loop(context, brain, computer, settings, Progress())
    _print_outcome(outcome)
    return _code(outcome)


def _context_from_checkpoint(checkpoint, log_dir: Path) -> RunContext:
    working = Path(checkpoint.working_copy)
    return RunContext(
        goal="Continue the brochure from the saved checkpoint.",
        proposal_brief=checkpoint.proposal_brief,
        plan=checkpoint.plan,
        template_path=Path(checkpoint.template_path),
        template_id=checkpoint.template_id,
        template_sha256=checkpoint.template_sha256,
        template_root=Path(checkpoint.template_path).parent,
        proposal_path=Path(checkpoint.proposal_path),
        working_copy=working,
        pdf_path=Path(checkpoint.pdf_path),
        output_root=working.parent.parent,
        preview_dir=Path("previews"),
        journal_path=log_dir / "actions.jsonl",
        checkpoint_path=log_dir / "checkpoint.json",
        pause_path=log_dir / "PAUSE",
        allowlist=set(checkpoint.allowlist),
        overwrite_confirmed=checkpoint.overwrite_confirmed,
        backend=checkpoint.backend,
        used_block_ids=set(checkpoint.used_block_ids),
        edits_attempted=checkpoint.edits_attempted,
        action_count=checkpoint.action_count,
        history=list(checkpoint.history),
    )


def _add_backend(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=("auto", "windows", "simulated"),
        default="auto",
        help="auto uses Windows on Windows and refuses elsewhere",
    )


def _warn_backend(backend: str) -> None:
    if backend == "simulated":
        print("Computer backend: simulated. This run will not control Adobe InDesign.", flush=True)


def _print_outcome(outcome: RunOutcome) -> None:
    if outcome.status == "completed":
        print("Completed.")
    elif outcome.status == "needs_assistance":
        print("Requires assistance.")
    elif outcome.status == "stopped":
        print("Stopped.")
    else:
        print("Failed.")
    print(outcome.message)
    if outcome.pdf_path:
        print(f"PDF: {outcome.pdf_path}")
    if outcome.working_copy:
        print(f"Working copy: {outcome.working_copy}")


def _code(outcome: RunOutcome) -> int:
    return {"completed": 0, "failed": 1, "needs_assistance": 2, "stopped": 3}[outcome.status]


def _imports(name: str) -> str:
    try:
        __import__(name)
    except Exception as exc:
        return f"no ({exc.__class__.__name__})"
    return "yes"
