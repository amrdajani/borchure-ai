from pathlib import Path

import fitz

from designcraft_brochure.agent.loop import Progress, run_loop
from designcraft_brochure.agent.prepare import prepare_run
from designcraft_brochure.config import Settings
from designcraft_brochure.craft.session import SimulatedSession
from designcraft_brochure.agent.candidates import _is_range, build_candidates
from designcraft_brochure.content.plan import airfare_line, format_trip_date, tour_code_line
from designcraft_brochure.models import BrochurePlan, CopyBlock, Observation, RunOutcome, StoryView
from designcraft_brochure.safety import sha256_file
from tests.helpers import CallbackBrain, answering_brain, write_idml


FIXTURE = Path(__file__).parent / "fixtures" / "proposal.txt"


def test_simulated_run_exports_a_pdf_without_touching_the_template(tmp_path):
    root = tmp_path / "templates"
    template = root / "travel" / "harbor.idml"
    write_idml(template)
    before = sha256_file(template)
    proposal = tmp_path / "proposal.txt"
    proposal.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    brain = CallbackBrain(answering_brain)
    prepared = prepare_run(
        proposal_path=proposal,
        template_root=root,
        output_dir=tmp_path / "output",
        preview_dir=tmp_path / "previews",
        log_dir=tmp_path / "logs",
        brain=brain,
        settings=Settings(),
        overwrite=False,
        backend="simulated",
        ocr=False,
        progress=Progress(),
    )
    assert not isinstance(prepared, RunOutcome)
    outcome = run_loop(prepared, brain, SimulatedSession(), Settings(), Progress())
    assert outcome.status == "completed", outcome.message
    assert outcome.simulated is True
    assert sha256_file(template) == before
    assert "PyMuPDF" in outcome.message
    assert outcome.pdf_path
    assert outcome.native_path
    assert outcome.idml_path
    assert Path(outcome.idml_path).is_file()
    with fitz.open(outcome.pdf_path) as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Harbor Day Trips" in text
    assert "$480" in text
    assert "Ignore previous" not in text
    journal = (tmp_path / "logs" / "actions.jsonl").read_text(encoding="utf-8")
    assert "480" not in journal
    assert "Ignore previous" not in journal
    assert "open_working" in journal
    assert "export_pdf" in journal


def test_price_date_and_contact_stories_use_proposal_facts(tmp_path):
    observation = Observation(
        document_open=True,
        stories=[
            StoryView(id=1, preview="$4,325", length=6),
            StoryView(id=2, preview="$500", length=4),
            StoryView(id=3, preview="APRIL 06 -16, 2025", length=18),
            StoryView(id=4, preview="01/31/2025", length=10),
            StoryView(id=5, preview="friends@travelwithfriends.com 501-379-9226", length=42),
            StoryView(id=6, preview="DAY 1", length=5),
            StoryView(
                id=7,
                preview="HOST CONTACT INFO NAME: ROBBY MITCHELL EMAIL: robby@robbymitchell.com FIRST PAYMENT: 08/02/2027",
                length=90,
            ),
            StoryView(id=8, preview="HLMTC1127", length=9),
            StoryView(id=10, preview="INTERNATIONAL AIRFARE", length=21),
            StoryView(id=11, preview="AIRFARE & TRANSFERS", length=19),
        ],
    )
    plan = BrochurePlan(
        template_id="fpsem",
        blocks=[
            CopyBlock(id="price_1", role="price", text="$4900", source_backed=True),
            CopyBlock(id="price_2", role="price", text="$500", source_backed=True),
            CopyBlock(id="date_1", role="date", text="April 8th - 17th, 2026", source_backed=True),
            CopyBlock(id="date_2", role="date", text="Dec 9, 2025", source_backed=True),
            CopyBlock(id="date_3", role="date", text="Jan 8, 2026", source_backed=True),
            CopyBlock(id="name", role="name", text="Pastor Sherry Morris", source_backed=True),
            CopyBlock(id="email", role="contact", text="rami@travelwithfriends.com", source_backed=True),
            CopyBlock(id="phone", role="contact", text="+1 (501) 813-4974", source_backed=True),
            CopyBlock(id="host_0", role="host_panel", text="CONTACT INFO", source_backed=True),
            CopyBlock(id="host_1", role="host_panel", text="HOST: PASTOR SHERRY MORRIS", source_backed=True),
            CopyBlock(id="host_2", role="host_panel", text="EMAIL: crownsministries17@yahoo.com", source_backed=True),
            CopyBlock(id="host_3", role="host_panel", text="FIRST PAYMENT: Dec 9, 2025", source_backed=True),
            CopyBlock(id="host_4", role="host_panel", text="FINAL PAYMENT: Jan 8, 2026", source_backed=True),
            CopyBlock(id="trip_date_0", role="trip_date", text="APR 8-17, 2026", source_backed=True),
            CopyBlock(id="tour_code", role="tour_code", text="Tour Code:", source_backed=True),
            CopyBlock(id="airfare", role="airfare", text="AIRFARE FROM SALT LAKE CITY", source_backed=True),
        ],
    )
    actions = build_candidates(
        observation,
        plan,
        working_copy=tmp_path / "working.idml",
        native_path=tmp_path / "native.designcraft",
        pdf_path=tmp_path / "out.pdf",
        idml_export_path=tmp_path / "edited.idml",
        preview_path=tmp_path / "page.png",
        overwrite=False,
    )
    fills = {action.id: action.params["text"] for action in actions if action.id.startswith("fill_story_")}
    assert fills["fill_story_1"] == "$4900"
    assert "fill_story_2" not in fills
    assert fills["fill_story_3"] == "APR 8-17, 2026"
    assert fills["fill_story_8"] == "Tour Code:"
    assert fills["fill_story_10"] == "AIRFARE FROM SALT LAKE CITY"
    assert "fill_story_11" not in fills
    assert fills["fill_story_4"] == "Jan 8, 2026"
    assert "fill_story_5" not in fills
    assert "fill_story_6" not in fills
    assert fills["fill_story_7"] == (
        "CONTACT INFO\nHOST: PASTOR SHERRY MORRIS\nEMAIL: crownsministries17@yahoo.com\n"
        "FIRST PAYMENT: Dec 9, 2025\nFINAL PAYMENT: Jan 8, 2026"
    )
    assert "Follow our ministry" not in fills["fill_story_7"]
    assert "FOLLOW OUR MINISTRY" not in fills["fill_story_7"]
    assert all(action.id != "request_human" for action in actions)


def test_a_truncated_host_panel_is_not_offered_again(tmp_path):
    written = (
        "CONTACT INFO\nHOST: PASTOR DANE WEAD\nEMAIL: dane@newlifelogan.com\n"
        "FIRST PAYMENT: July 13, 2027\nFINAL PAYMENT: Aug 12, 2027"
    )
    preview = "CONTACT INFO HOST: PASTOR DANE WEAD EMAIL: dane@newlifelogan.com"
    observation = Observation(
        document_open=True,
        stories=[
            StoryView(id=99, preview=preview, length=len(written)),
            *[StoryView(id=index, preview="DAY", length=3) for index in range(1, 9)],
        ],
    )
    plan = BrochurePlan(
        template_id="hlmtc",
        blocks=[
            CopyBlock(id="host_0", role="host_panel", text="CONTACT INFO", source_backed=True),
            CopyBlock(id="host_1", role="host_panel", text="HOST: PASTOR DANE WEAD", source_backed=True),
            CopyBlock(id="host_2", role="host_panel", text="EMAIL: dane@newlifelogan.com", source_backed=True),
            CopyBlock(id="host_3", role="host_panel", text="FIRST PAYMENT: July 13, 2027", source_backed=True),
            CopyBlock(id="host_4", role="host_panel", text="FINAL PAYMENT: Aug 12, 2027", source_backed=True),
        ],
    )
    actions = build_candidates(
        observation,
        plan,
        working_copy=tmp_path / "working.idml",
        native_path=tmp_path / "native.designcraft",
        pdf_path=tmp_path / "out.pdf",
        idml_export_path=tmp_path / "edited.idml",
        preview_path=tmp_path / "page.png",
        overwrite=False,
    )
    assert "fill_story_99" not in {action.id for action in actions}


def test_trip_dates_use_the_bar_format_and_a_missing_tour_code_stays_blank():
    assert format_trip_date("Nov 10 -19, 2027") == "NOV 10-19, 2027"
    assert format_trip_date("October 5-14, 2026") == "OCT 5-14, 2026"
    assert format_trip_date("April 8th - 17th, 2026") == "APR 8-17, 2026"
    assert tour_code_line("Prepared for Dane Wead") == "Tour Code:"
    assert airfare_line("Airfare to and from Salt Lake City") == "AIRFARE FROM SALT LAKE CITY"
    assert airfare_line("Airfare from Newark") == "AIRFARE FROM NEWARK"
    assert tour_code_line("Tour code: hlmrs426") == "Tour Code: HLMRS426"
    assert _is_range("November 30 to December 9, 2027")


def test_indd_templates_are_not_offered(tmp_path):
    root = tmp_path / "templates"
    indd = root / "travel" / "old.indd"
    indd.parent.mkdir(parents=True)
    indd.write_bytes(b"not a real indd")
    outcome = prepare_run(
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
    assert isinstance(outcome, RunOutcome)
    assert outcome.status == "failed"
    assert ".indd" in outcome.message


def test_an_open_template_is_exported_when_no_story_matches(tmp_path):
    root = tmp_path / "templates"
    template = root / "travel" / "many.idml"
    _write_many_stories(template, [f"Section {index}" for index in range(1, 10)])
    proposal = tmp_path / "proposal.txt"
    proposal.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    prepared = prepare_run(
        proposal_path=proposal,
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
    assert not isinstance(prepared, RunOutcome)
    outcome = run_loop(prepared, CallbackBrain(_stare_or_open), SimulatedSession(), Settings(), Progress())
    assert outcome.status == "completed", outcome.message
    assert outcome.pdf_path
    assert Path(outcome.pdf_path).is_file()
    journal = (tmp_path / "logs" / "actions.jsonl").read_text(encoding="utf-8")
    assert journal.count("inspect_document") < 4


def _stare_or_open(state, questions):
    from designcraft_brochure.jev.brain import scripted_judgment

    choices: dict[str, str] = {}
    nouls: dict[str, float] = {}
    if "next_action" in questions:
        criteria = questions["next_action"]["criteria"]
        choices["next_action"] = "open_working" if "open_working" in criteria else "inspect_document"
        if choices["next_action"] not in criteria:
            choices["next_action"] = next(iter(criteria))
        nouls["screen_understood"] = 0.95
        nouls["task_complete"] = 0.05
    if "safe_to_run" in questions:
        nouls["safe_to_run"] = 0.96
    if "effect_happened" in questions:
        nouls["effect_happened"] = 0.96
    return scripted_judgment(questions, choices=choices, nouls=nouls)


def _write_many_stories(path: Path, stories: list[str]) -> None:
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    story_tags = "\n".join(
        f'  <idPkg:Story src="Stories/Story_t{index}.xml"/>' for index, _text in enumerate(stories, start=1)
    )
    designmap = f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <idPkg:Spread src="Spreads/Spread_s1.xml"/>
{story_tags}
</Document>
"""
    spread = """<?xml version="1.0" encoding="UTF-8"?>
<idPkg:Spread xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <Spread Self="s1"><Page Self="p1"/></Spread>
</idPkg:Spread>
"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("designmap.xml", designmap)
        package.writestr("Spreads/Spread_s1.xml", spread)
        for index, text in enumerate(stories, start=1):
            package.writestr(
                f"Stories/Story_t{index}.xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<idPkg:Story xmlns:idPkg="http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging" DOMVersion="8.0">
  <Story Self="t{index}"><Content>{text}</Content></Story>
</idPkg:Story>
""",
            )


def test_a_typed_tour_code_replaces_the_blank_bar_and_wins_over_the_proposal():
    assert tour_code_line("no code here") == "Tour Code:"
    assert tour_code_line("no code here", " hlmrs426 ") == "Tour Code: HLMRS426"
    assert tour_code_line("Tour Code: OLD123", "new456") == "Tour Code: NEW456"
    assert tour_code_line("Tour Code: OLD123", "x;drop") == "Tour Code: OLD123"
