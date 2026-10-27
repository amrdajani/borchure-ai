"""The finishing pass puts the template's look back after story.setText."""

from __future__ import annotations

import json
from pathlib import Path

from designcraft_brochure.craft.assets import load_slots, match_frames, picture_frames, place_brand_images
from designcraft_brochure.craft.client import ALLOWED_COMMANDS
from designcraft_brochure.craft.polish import polish_document
from designcraft_brochure.models import BrochurePlan, CopyBlock

YELLOW = "C=3 M=20 Y=99 K=0"


class FakeDesignCraft:
    """Answers the few calls the finishing pass makes and records every command."""

    def __init__(self, stories: dict[int, str], frames: list[dict], overset_until_size: float | None = None):
        self.texts = dict(stories)
        self.frames = frames
        self.sent: list[tuple[str, dict]] = []
        self.overset_until_size = overset_until_size
        self.size: dict[int, float] = {}
        self.selected: tuple[int, int, int] | None = None

    def call(self, method, params=None):
        params = params or {}
        if method == "document.inspect":
            return {
                "spreads": [{"pages": [{"bounds": [0, 0, 792, 612]}], "items": self.frames}],
                "stories": [
                    {
                        "id": story_id,
                        "preview": text.replace("\n", " ")[:80],
                        "frames": [frame["id"] for frame in self.frames if frame.get("story") == story_id],
                        "lines": 1,
                        "overset": False,
                    }
                    for story_id, text in self.texts.items()
                ],
            }
        command, inner = params["command"], params["params"]
        self.sent.append((command, inner))
        if command == "story.get":
            story = inner["story"]
            overset = bool(self.overset_until_size and self.size.get(story, 11) > self.overset_until_size)
            return {"text": self.texts[story], "overset": overset}
        if command == "story.setText":
            self.texts[inner["story"]] = inner["text"]
        if command == "text.select":
            self.selected = (inner["story"], inner["anchor"], inner["focus"])
        if command == "type.char" and "size" in inner["attrs"] and self.selected:
            self.size[self.selected[0]] = inner["attrs"]["size"]
        if command == "type.selectionAttrs":
            return {"chars": {"fontFamily": "Montserrat", "size": 13.1, "fill": "[Paper]", "tracking": -35}}
        return {}

    def commands(self, name):
        return [inner for command, inner in self.sent if command == name]


def _text_frame(frame_id, story, bounds):
    return {"id": frame_id, "kind": "<text frame>", "bounds": bounds, "story": story, "fill": "[None]"}


def _bar(bounds):
    return {"id": 900 + int(bounds[1]), "kind": "<rectangle>", "bounds": bounds, "fill": YELLOW}


def _plan(*blocks):
    return BrochurePlan(template_id="t", blocks=[CopyBlock(id=f"b{i}", source_backed=True, **b) for i, b in enumerate(blocks)])


HOST = [
    {"role": "host_panel", "text": "CONTACT INFO"},
    {"role": "host_panel", "text": "HOST: PASTOR DANE WEAD"},
    {"role": "host_panel", "text": "EMAIL: dane@newlifelogan.com"},
    {"role": "host_panel", "text": "FIRST PAYMENT: July 13, 2027"},
    {"role": "host_panel", "text": "FINAL PAYMENT: Aug 12, 2027"},
]


def test_flattened_host_panel_gets_a_title_bold_lines_and_yellow_payments(tmp_path):
    flat = "CONTACT INFO\n\nHOST: PASTOR DANE WEAD\n\nEMAIL: dane@newlifelogan.com\n\nFIRST PAYMENT: July 13, 2027\n\nFINAL PAYMENT: Aug 12, 2027"
    client = FakeDesignCraft({99: flat}, [_text_frame(178, 99, [282, 165, 510, 313]), _bar([614, 515, 800, 543])])
    notes = polish_document(client, _plan(*HOST), assets_dir=tmp_path)
    assert "host panel restyled" in notes
    assert client.texts[99] == "\n".join(line["text"] for line in HOST)
    styled = [(inner["attrs"]["fontStyle"], inner["attrs"]["size"], inner["attrs"]["fill"]) for inner in client.commands("type.char")]
    assert styled[:5] == [
        ("Regular", 18, "[Paper]"),
        ("Bold", 11, "[Paper]"),
        ("Bold", 11, "[Paper]"),
        ("Bold", 11, YELLOW),
        ("Bold", 11, YELLOW),
    ]
    spacing = [inner["attrs"]["spaceAfter"] for inner in client.commands("type.para")]
    assert spacing[:5] == [12, 12, 12, 0, 0]


def test_bar_text_is_caps_left_aligned_and_centred_inside_the_bar(tmp_path):
    bar = [613.8, 515.4, 800.4, 543.0]
    client = FakeDesignCraft(
        {104: "AIRFARE FROM SALT LAKE CITY"},
        [_text_frame(126, 104, [619.4, 528.2, 790.0, 539.5]), _bar(bar)],
        overset_until_size=10,
    )
    notes = polish_document(client, _plan({"role": "airfare", "text": "AIRFARE FROM SALT LAKE CITY"}), assets_dir=tmp_path)
    assert "airfare bar aligned" in notes
    resize = client.commands("transform.resize")[0]
    assert resize["to"][0] == 622.0 and resize["to"][3] == 539.0
    assert resize["to"][2] <= 780  # inside the trimmed page
    assert client.commands("object.textFrameOptions")[0]["verticalJustification"] == "center"
    last = client.commands("type.char")[-1]["attrs"]
    assert last["capitalization"] == "allCaps" and last["tracking"] == -35
    assert last["size"] <= 10  # shrunk until the story stopped overflowing


def test_cover_line_names_the_host_from_the_approved_host_line(tmp_path):
    client = FakeDesignCraft(
        {100: "JOIN \nEvangelists \nROBBY & TRACEY MITCHELL IN"},
        [_text_frame(179, 100, [543.8, 74.4, 769.2, 144.0])],
    )
    notes = polish_document(client, _plan(*HOST), assets_dir=tmp_path)
    assert client.texts[100] == "JOIN PASTOR DANE WEAD IN"
    assert any(note.startswith("cover line") for note in notes)


def test_only_commands_that_format_text_were_added_to_the_allowlist():
    added = {"text.select", "type.char", "type.para", "type.selectionAttrs", "transform.resize", "object.textFrameOptions", "object.fit"}
    assert added <= ALLOWED_COMMANDS
    assert "app.quit" not in ALLOWED_COMMANDS and "file.delete" not in ALLOWED_COMMANDS


def _assets(tmp_path):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "logo.png").write_bytes(b"png")
    (tmp_path / "brand" / "sun.png").write_bytes(b"png")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "slots": [
                    {"name": "logo", "bounds": [282, 26, 510, 146], "file": "brand/logo.png", "fit": "fitProportionally"},
                    {"name": "sun", "bounds": [540, 15, 623, 84], "file": "brand/sun.png", "fit": "fitProportionally"},
                    {"name": "gone", "bounds": [0, 0, 10, 10], "file": "brand/missing.png", "fit": "fitProportionally"},
                    {"name": "escape", "bounds": [0, 0, 10, 10], "file": "../outside.png", "fit": "fitProportionally"},
                ]
            }
        )
    )
    return tmp_path


def _picture(frame_id, bounds):
    return {"id": frame_id, "kind": "<image>", "bounds": bounds, "asset": frame_id + 1000}


def test_pictures_go_into_the_frames_that_line_up_with_a_slot(tmp_path):
    root = _assets(tmp_path)
    assert {slot["name"] for slot in load_slots(root)} == {"logo", "sun"}  # missing and out-of-folder files are ignored
    info = {"spreads": [{"items": [_picture(7, [281.9, 26, 510.1, 146.5]), _picture(8, [540, 16, 622, 83]), _picture(9, [36, 515, 105, 582])]}]}
    pairs = match_frames(load_slots(root), picture_frames(info))
    assert {(slot["name"], frame["id"]) for slot, frame in pairs} == {("logo", 7), ("sun", 8)}  # the QR frame is left alone

    client = FakeDesignCraft({}, [])
    notes = place_brand_images(client, info, root)
    assert sorted(notes) == ["placed logo", "placed sun"]
    assert {inner["frame"] for inner in client.commands("file.place")} == {7, 8}
    assert {inner["mode"] for inner in client.commands("object.fit")} == {"fitProportionally"}


def test_a_step_that_fails_does_not_stop_the_rest(tmp_path):
    class Broken(FakeDesignCraft):
        def call(self, method, params=None):
            if params and params.get("command") == "type.char":
                raise RuntimeError("DesignCraft said no")
            return super().call(method, params)

    flat = "CONTACT INFO\nHOST: PASTOR DANE WEAD"
    client = Broken({99: flat, 100: "JOIN \nEvangelists"}, [_text_frame(178, 99, [282, 165, 510, 313]), _text_frame(179, 100, [543, 74, 769, 144])])
    notes = polish_document(client, _plan(*HOST), assets_dir=tmp_path)
    assert any(note.startswith("host panel: skipped") for note in notes)


def test_the_run_polishes_once_before_the_first_save_or_export(tmp_path):
    from pathlib import Path

    from designcraft_brochure.agent.loop import Progress, run_loop
    from designcraft_brochure.agent.prepare import prepare_run
    from designcraft_brochure.config import Settings
    from designcraft_brochure.craft.session import SimulatedSession
    from designcraft_brochure.models import RunOutcome
    from tests.helpers import CallbackBrain, answering_brain, write_idml

    root = tmp_path / "templates"
    write_idml(root / "travel" / "harbor.idml")
    proposal = tmp_path / "proposal.txt"
    proposal.write_text((Path(__file__).parent / "fixtures" / "proposal.txt").read_text(encoding="utf-8"), encoding="utf-8")
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

    order: list[str] = []

    class Recording(SimulatedSession):
        def polish(self, plan, work_dir=None):
            order.append("polish")
            return []

        def execute(self, action):
            if action.command in {"file.saveAs", "file.exportPdf", "file.exportIdml"}:
                order.append(action.command)
            return super().execute(action)

    outcome = run_loop(prepared, brain, Recording(), Settings(), Progress())
    assert outcome.status == "completed", outcome.message
    assert order.count("polish") == 1
    assert order[0] == "polish"


def test_register_url_uses_the_lower_case_tour_code():
    from designcraft_brochure.craft.qr import register_url

    assert register_url("HLMDW1127") == "https://www.travelwithfriends.com/host-tour/hlmdw1127"
    assert register_url(" hlmrs426 ") == "https://www.travelwithfriends.com/host-tour/hlmrs426"
    assert register_url("x") is None and register_url("a/../b") is None


def test_the_cover_register_code_is_replaced_when_there_is_a_tour_code(tmp_path):
    frames = [_picture(150, [36.0, 514.8, 104.9, 581.6]), _picture(300, [640, 520, 720, 600])]
    client = FakeDesignCraft({}, frames)
    plan = _plan({"role": "tour_code", "text": "Tour Code: HLMDW1127"})
    notes = polish_document(client, plan, assets_dir=tmp_path, work_dir=tmp_path / "out")
    assert "register code points at https://www.travelwithfriends.com/host-tour/hlmdw1127" in notes
    placed = client.commands("file.place")
    assert [inner["frame"] for inner in placed] == [150]  # the terms code on page 2 is left alone
    png = Path(placed[0]["path"])
    assert png.is_file() and png.read_bytes()[:4] == b"\x89PNG"
    assert client.commands("object.fit")[0]["mode"] == "fitProportionally"


def test_without_a_tour_code_the_template_register_code_stays(tmp_path):
    client = FakeDesignCraft({}, [_picture(150, [36.0, 514.8, 104.9, 581.6])])
    polish_document(client, _plan({"role": "tour_code", "text": "Tour Code:"}), assets_dir=tmp_path, work_dir=tmp_path / "out")
    assert client.commands("file.place") == []
