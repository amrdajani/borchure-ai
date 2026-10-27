"""Command line for the DesignCraft brochure agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from designcraft_brochure.agent.loop import Progress, RunContext, run_loop
from designcraft_brochure.api import serve
from designcraft_brochure.agent.prepare import prepare_run
from designcraft_brochure.config import Settings
from designcraft_brochure.craft.client import ControlClient
from designcraft_brochure.craft.session import SessionError, open_session
from designcraft_brochure.jev.brain import TypeSafeBrain
from designcraft_brochure.journal import load_checkpoint
from designcraft_brochure.models import RunOutcome
from designcraft_brochure.proposal.extract import extract_proposal
from designcraft_brochure.proposal.reader import ReadError, read_source
from designcraft_brochure.safety import SafetyError, sha256_file
from designcraft_brochure.templates.library import discover_templates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="designcraft-brochure")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Show what this machine can run")
    doctor.set_defaults(func=cmd_doctor)

    templates = sub.add_parser("list-templates", help="List brochure templates")
    templates.add_argument("--templates", type=Path, default=Path("templates"))
    templates.set_defaults(func=cmd_templates)

    inspect = sub.add_parser("inspect", help="Read a proposal without calling Jev")
    inspect.add_argument("proposal", type=Path)
    inspect.add_argument("--ocr", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    run = sub.add_parser("run", help="Build a brochure from a proposal")
    run.add_argument("--proposal", type=Path, required=True)
    run.add_argument("--templates", type=Path, default=Path("templates"))
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--yes", action="store_true", help="Allow replacing an existing output file")
    run.add_argument("--ocr", action="store_true")
    _add_backend(run)
    run.add_argument("--max-actions", type=int, default=None)
    run.set_defaults(func=cmd_run)

    serve_parser = sub.add_parser("serve", help="Accept a proposal and a brochure file over HTTP")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=7980)
    serve_parser.add_argument("--jobs", type=Path, default=Path("output/jobs"))
    serve_parser.add_argument(
        "--saved-templates",
        type=Path,
        default=Path("templates/saved"),
        help="where templates added through the API are kept",
    )
    serve_parser.add_argument(
        "--backend",
        choices=("auto", "live", "simulated", "headless"),
        default="headless",
        help="headless drives designcraft-cli and does not open the desktop app",
    )
    serve_parser.set_defaults(func=cmd_serve)

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
    except (RuntimeError, SessionError, SafetyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_doctor(_args) -> int:
    settings = Settings.from_env()
    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    print(f"TypeSafe key configured: {bool(settings.typesafe_api_key)}")
    print(f"Jev model: {settings.model}")
    print(f"Anthropic key configured: {bool(settings.anthropic_api_key)}")
    if settings.anthropic_api_key:
        print(f"Anthropic model: {settings.anthropic_model}")
    print(f"Copy model configured: {bool(settings.copy_enabled)}")
    print(f"typesafe-sdk import: {_imports('typesafe_sdk')}")
    print(f"PyMuPDF import: {_imports('fitz')}")
    print(f"python-docx import: {_imports('docx')}")
    print(f"Pillow import: {_imports('PIL')}")
    print(f"DesignCraft control: {settings.control_host}:{settings.control_port}")
    print(f"DesignCraft reachable: {_reachable(settings)}")
    from designcraft_brochure.templates.indd import find_indd_binary

    converter = find_indd_binary()
    print(f"INDD converter: {converter if converter else 'not installed'}")
    return 0


def cmd_templates(args) -> int:
    found = discover_templates(args.templates)
    if not found:
        print("No templates found.")
        return 0
    for item in found:
        pages = item.page_count if item.page_count is not None else "?"
        if item.suffix == ".indd" and item.openable:
            flag = "convert"
        elif item.openable:
            flag = "openable"
        else:
            flag = "not-openable"
        print(f"{item.id}\t{item.category}\t{flag}\tpages={pages}\t{item.path}")
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


def cmd_run(args) -> int:
    settings = _settings(args)
    if not settings.typesafe_api_key:
        print("Set TYPESAFE_API_KEY before running the agent.", file=sys.stderr)
        return 1
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    progress = Progress()
    with TypeSafeBrain(settings.model) as brain:
        prepared = prepare_run(
            proposal_path=args.proposal,
            template_root=args.templates,
            output_dir=output,
            preview_dir=Path("previews"),
            log_dir=Path("logs"),
            brain=brain,
            settings=settings,
            overwrite=args.yes,
            backend=args.backend,
            ocr=args.ocr,
            progress=progress,
        )
        if isinstance(prepared, RunOutcome):
            print(prepared.message)
            return _code(prepared)
        _warn_backend(args.backend)
        session = open_session(
            args.backend,
            settings.control_host,
            settings.control_port,
            work_root=output,
        )
        outcome = run_loop(prepared, brain, session, settings, progress)
    _print_outcome(outcome)
    return _code(outcome)


def cmd_serve(args) -> int:
    settings = Settings.from_env()
    if not settings.typesafe_api_key:
        print("Set TYPESAFE_API_KEY before serving.", file=sys.stderr)
        return 1
    if args.backend == "live" and _reachable(settings) != "yes":
        print(
            f"DesignCraft is not answering on {settings.control_host}:{settings.control_port}.",
            file=sys.stderr,
        )
        return 1
    if args.backend == "headless":
        from designcraft_brochure.craft.headless import cli_installed

        if not cli_installed():
            print("designcraft-cli is not on PATH.", file=sys.stderr)
            return 1
    server = serve(
        args.host,
        args.port,
        args.jobs,
        settings,
        backend=args.backend,
        library_root=args.saved_templates,
    )
    print(f"Brochure API on http://{server.server_address[0]}:{server.server_address[1]}", flush=True)
    print("POST /brochures with form files 'proposal' and 'template' (or a saved 'template_id').", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def cmd_resume(args) -> int:
    settings = _settings(args)
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
    context.backend = args.backend
    _warn_backend(args.backend)
    with TypeSafeBrain(settings.model) as brain:
        session = open_session(
            args.backend,
            settings.control_host,
            settings.control_port,
            work_root=working.parent,
        )
        outcome = run_loop(context, brain, session, settings, Progress())
    _print_outcome(outcome)
    return _code(outcome)


def _context_from_checkpoint(checkpoint, log_dir: Path) -> RunContext:
    return RunContext(
        goal="Continue the brochure from the saved checkpoint.",
        proposal_brief=checkpoint.proposal_brief,
        plan=checkpoint.plan,
        template_path=Path(checkpoint.template_path),
        template_id=checkpoint.template_id,
        template_sha256=checkpoint.template_sha256,
        template_root=Path(checkpoint.template_root),
        proposal_path=Path(checkpoint.proposal_path),
        working_copy=Path(checkpoint.working_copy),
        native_path=Path(checkpoint.native_path),
        pdf_path=Path(checkpoint.pdf_path),
        idml_export_path=Path(checkpoint.idml_export_path),
        output_root=Path(checkpoint.working_copy).parent.parent,
        preview_dir=Path("previews"),
        journal_path=log_dir / "actions.jsonl",
        checkpoint_path=log_dir / "checkpoint.json",
        pause_path=log_dir / "PAUSE",
        allowlist=set(checkpoint.allowlist),
        image_paths=list(checkpoint.image_paths),
        overwrite_confirmed=checkpoint.overwrite_confirmed,
        backend=checkpoint.backend,
        control_host=checkpoint.control_host,
        control_port=checkpoint.control_port,
        action_count=checkpoint.action_count,
        history=list(checkpoint.history),
        failures=dict(checkpoint.failures),
    )


def _settings(args) -> Settings:
    settings = Settings.from_env()
    if getattr(args, "max_actions", None) is not None:
        settings.max_actions = args.max_actions
    return settings


def _add_backend(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=("auto", "live", "simulated", "headless"),
        default="auto",
        help="headless drives designcraft-cli; auto uses the desktop app when it is running",
    )


def _warn_backend(backend: str) -> None:
    if backend == "simulated":
        print("Backend: simulated. This run will not control the DesignCraft app.", flush=True)
    elif backend == "headless":
        print("Backend: designcraft-cli, no desktop window.", flush=True)
    elif backend == "live":
        print("Backend: DesignCraft control channel.", flush=True)


def _print_outcome(outcome: RunOutcome) -> None:
    labels = {
        "completed": "Completed.",
        "needs_assistance": "Requires assistance.",
        "stopped": "Stopped.",
        "failed": "Failed.",
    }
    print(labels[outcome.status])
    print(outcome.message)
    if outcome.pdf_path:
        print(f"PDF: {outcome.pdf_path}")
    if outcome.native_path:
        print(f"Native file: {outcome.native_path}")
    if outcome.idml_path:
        print(f"Edited IDML: {outcome.idml_path}")
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


def _reachable(settings: Settings) -> str:
    client = ControlClient(settings.control_host, settings.control_port, timeout=1.5)
    try:
        client.call("engine.commands", {})
    except Exception as exc:
        return f"no ({exc.__class__.__name__})"
    finally:
        client.close()
    return "yes"
