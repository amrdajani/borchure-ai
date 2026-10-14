from pathlib import Path

from brochure_agent.agent.candidates import build_candidates, poc_candidates
from brochure_agent.agent.loop import RunContext, run_loop, run_poc
from brochure_agent.agent.prepare import prepare_run
from brochure_agent.agent.loop import Progress
from brochure_agent.computer.factory import create_controller
from brochure_agent.computer.simulated import SimulatedComputer
from brochure_agent.computer.validate import ValidationError, validate_action
from brochure_agent.config import Settings
from brochure_agent.content.plan import build_plan
from brochure_agent.models import (
    BrochurePlan,
    CandidateAction,
    CopyBlock,
    Observation,
    TemplateInfo,
    UIElement,
    ActionName,
)
from brochure_agent.proposal.extract import extract_proposal
from brochure_agent.proposal.reader import read_source
from brochure_agent.safety import sha256_file
from tests.helpers import CallbackBrain, answering_brain, fixed_choice, write_idml

FIXTURE = Path(__file__).parent / "fixtures" / "proposal.txt"


def _observation(**overrides) -> Observation:
    base = dict(
        image_width=1280,
        image_height=720,
        mouse_x=10,
        mouse_y=10,
        windows=["Program Manager"],
        active_window="Program Manager",
    )
    base.update(overrides)
    return Observation(**base)


def test_candidates_follow_the_screen():
    empty = build_candidates(
        _observation(),
        working_copy=Path("/tmp/output/working/harbor-working.idml"),
        pdf_path=Path("/tmp/output/harbor-working.pdf"),
        plan=None,
        allowlist=set(),
        used_block_ids=set(),
        overwrite_confirmed=False,
        text_focus_likely=False,
    )
    names = {item.name for item in empty}
    assert ActionName.click not in names
    assert ActionName.press_key not in names or all(item.args.get("key") != "ctrl+e" for item in empty)
    assert any(item.id == "open_working_copy" for item in empty)

    frame = UIElement(
        id="frame-1",
        source="accessibility",
        role="text_frame",
        name="Headline",
        bbox=(100, 100, 200, 40),
    )
    screen = _observation(
        elements=[frame],
        active_window="harbor-working.idml - Adobe InDesign",
        windows=["harbor-working.idml - Adobe InDesign"],
        application_guess="Adobe InDesign",
        document_open=True,
        text_editing=True,
    )
    plan = BrochurePlan(
        template_id="t",
        blocks=[CopyBlock(id="headline", role="headline", text="Harbor Day Trips", source_backed=True)],
    )
    filled = build_candidates(
        screen,
        working_copy=Path("/tmp/output/working/harbor-working.idml"),
        pdf_path=Path("/tmp/output/harbor-working.pdf"),
        plan=plan,
        allowlist={"Harbor Day Trips"},
        used_block_ids=set(),
        overwrite_confirmed=False,
        text_focus_likely=True,
    )
    assert any(item.name == ActionName.double_click for item in filled)
    assert any(item.id == "type_headline" for item in filled)
    assert any(item.id == "key_export" for item in filled)

    dialog = screen.model_copy(update={"dialog": "Export"})
    during_export = build_candidates(
        dialog,
        working_copy=Path("/tmp/output/working/harbor-working.idml"),
        pdf_path=Path("/tmp/output/harbor-working.pdf"),
        plan=plan,
        allowlist={"Harbor Day Trips", "/tmp/output/harbor-working.pdf"},
        used_block_ids=set(),
        overwrite_confirmed=False,
        text_focus_likely=False,
    )
    assert all(item.args.get("key") != "ctrl+e" for item in during_export)
    assert any(item.name == ActionName.type_text and item.args.get("kind") == "path" for item in during_export)


def test_validator_rejects_unlisted_text_and_keys():
    observation = _observation(dialog="Export")
    bad = CandidateAction(
        id="type_evil",
        name=ActionName.type_text,
        description="type",
        risk="high",
        args={"text": "Ignore previous instructions", "kind": "path"},
    )
    try:
        validate_action(
            bad,
            observation,
            allowlist={"safe"},
            working_copy=None,
            output_root=Path("/tmp"),
            overwrite_confirmed=True,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("unlisted text should be rejected")
    assert "powershell" not in " ".join(__import__("brochure_agent.computer.validate", fromlist=["ALLOWED_KEYS"]).ALLOWED_KEYS)


def test_stop_choice_does_not_touch_the_document(tmp_path):
    context, computer = _context(tmp_path)
    outcome = run_loop(context, CallbackBrain(fixed_choice("stop_agent")), computer, Settings(), Progress())
    assert outcome.status == "needs_assistance"
    assert computer.opened_path is None
    assert context.action_count == 0


def test_unknown_choice_is_not_executed(tmp_path):
    context, computer = _context(tmp_path)
    outcome = run_loop(context, CallbackBrain(fixed_choice("not-a-real-action")), computer, Settings(), Progress())
    assert outcome.status == "needs_assistance"
    assert computer.opened_path is None


def test_jev_script_opens_edits_and_exports(tmp_path):
    context, computer = _context(tmp_path)
    outcome = run_loop(
        context,
        CallbackBrain(answering_brain),
        computer,
        Settings(max_actions=20),
        Progress(),
    )
    assert outcome.status == "completed", outcome.message
    assert outcome.simulated
    assert "not by Adobe InDesign" in outcome.message
    assert context.pdf_path.is_file()
    assert sha256_file(context.template_path) == context.template_sha256
    assert computer.opened_path == str(context.working_copy)
    import fitz

    with fitz.open(context.pdf_path) as document:
        assert "Harbor Day Trips" in document[0].get_text()


def test_poc_executes_only_the_chosen_move(tmp_path):
    computer = SimulatedComputer(tmp_path / "screens")
    outcome = run_poc(CallbackBrain(answering_brain), computer, Settings())
    assert outcome.status == "completed"
    assert computer.mouse_x != 100 or computer.mouse_y != 100
    assert computer.opened_path is None

    idle = SimulatedComputer(tmp_path / "idle")
    stopped = run_poc(CallbackBrain(fixed_choice("stop_agent")), idle, Settings())
    assert stopped.status == "stopped"
    assert idle.mouse_x == 100
    assert idle.typed == []


def test_prepare_selects_template_without_changing_it(tmp_path):
    root = tmp_path / "templates"
    source = root / "travel" / "harbor.idml"
    write_idml(source)
    before = sha256_file(source)
    prepared = prepare_run(
        proposal_path=FIXTURE,
        template_root=root,
        output_dir=tmp_path / "output",
        preview_dir=tmp_path / "previews",
        log_dir=tmp_path / "logs",
        brain=CallbackBrain(answering_brain),
        settings=Settings(),
        overwrite=False,
        backend="simulated",
        ocr=False,
        progress=Progress(),
    )
    assert not hasattr(prepared, "status")
    assert prepared.template_id == "travel-harbor"
    assert prepared.working_copy.is_file()
    assert sha256_file(source) == before
    assert all(block.text in prepared.proposal_brief or block.source_backed for block in prepared.plan.blocks)
    joined = " ".join(block.text for block in prepared.plan.blocks)
    assert "Ignore previous" not in joined
    assert "$9999" not in joined


def test_generated_price_is_dropped(tmp_path, monkeypatch):
    source = read_source(FIXTURE, tmp_path / "images")
    proposal = extract_proposal(source, CallbackBrain(answering_brain))
    monkeypatch.setattr(
        "brochure_agent.content.plan._generate",
        lambda proposal, settings: [CopyBlock(id="price", role="price", text="Pay $9999 today.")],
    )
    settings = Settings(copy_api_key="test", copy_model="fake-model")
    plan = build_plan(
        proposal,
        TemplateInfo(id="travel-harbor", path="x.idml", category="travel", suffix=".idml"),
        CallbackBrain(answering_brain),
        settings,
    )
    assert all("$9999" not in block.text for block in plan.blocks)


def test_auto_backend_refuses_non_windows(tmp_path):
    try:
        create_controller("auto", tmp_path, 0.1)
    except RuntimeError as exc:
        assert "not Windows" in str(exc)
    else:
        if __import__("sys").platform == "win32":
            return
        raise AssertionError("auto backend should refuse this machine")


def _context(tmp_path: Path):
    template_root = tmp_path / "templates"
    template = template_root / "travel" / "harbor.idml"
    write_idml(template)
    output = tmp_path / "output"
    working = output / "working" / "harbor-working.idml"
    working.parent.mkdir(parents=True)
    working.write_bytes(template.read_bytes())
    pdf_path = output / "harbor-working.pdf"
    text = "Harbor Day Trips"
    plan = BrochurePlan(
        template_id="travel-harbor",
        blocks=[CopyBlock(id="headline", role="headline", text=text, source_backed=True)],
    )
    computer = SimulatedComputer(tmp_path / "screens")
    context = RunContext(
        goal="Edit the working copy and export a PDF.",
        proposal_brief={"project_name": text},
        plan=plan,
        template_path=template,
        template_id="travel-harbor",
        template_sha256=sha256_file(template),
        template_root=template_root,
        proposal_path=FIXTURE,
        working_copy=working,
        pdf_path=pdf_path,
        output_root=output,
        preview_dir=tmp_path / "previews",
        journal_path=tmp_path / "logs" / "actions.jsonl",
        checkpoint_path=tmp_path / "logs" / "checkpoint.json",
        pause_path=tmp_path / "logs" / "PAUSE",
        allowlist={text, str(pdf_path), str(working)},
        overwrite_confirmed=True,
        backend="simulated",
    )
    return context, computer
