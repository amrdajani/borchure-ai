"""The proposal's day list is what the brochure itinerary is filled from."""

import struct
from pathlib import Path

from designcraft_brochure.content.itinerary import parse_itinerary, tour_photo_paths


PROPOSAL = """
10 Day Holy Land
Day 1: Fly to Tel Aviv
Depart Newark in the evening.

Day 2
Jerusalem
Old City
Western Wall

Payment Schedule
Deposit due now
Day 9: This is after the itinerary and must be ignored
"""


def test_days_stop_at_the_next_section():
    days = parse_itinerary(PROPOSAL)
    assert [(day.number, day.title) for day in days] == [(1, "Fly to Tel Aviv"), (2, "Jerusalem")]
    assert days[0].body == "Depart Newark in the evening."
    assert days[1].body == "Old City\nWestern Wall"


def test_one_day_is_not_an_itinerary():
    assert parse_itinerary("Day 1: Only a mention\nNothing else.") == []


def test_days_are_written_as_capital_titles_and_points():
    from designcraft_brochure.content.itinerary import format_day_points, format_day_title

    assert format_day_title("Depart the USA") == "DEPART THE USA"
    assert format_day_points("Transfer to your hotel\n• See the Blue Mosque") == (
        "•\tTransfer to your hotel\n•\tSee the Blue Mosque"
    )


def test_a_long_day_is_shortened_so_it_fits_the_frame():
    from designcraft_brochure.content.itinerary import format_day_points, format_day_title

    assert format_day_title("Have the chance for an optional baptism (Mark 1:5)") == "BAPTISM (MARK 1:5)"
    assert format_day_title("6; Matt. 4). Travel to Jerusalem") == "TRAVEL TO JERUSALEM"
    assert format_day_title("Tomb (John 19:40).") == "TOMB (JOHN 19:40)"
    assert format_day_title('Cross." Visit the Church of the Holy Sepulchre') == "HOLY SEPULCHRE"
    assert len(format_day_title("Have the chance for an optional baptism (Mark 1:5)")) <= 26
    points = format_day_points(
        "You will be landing at Ben Gurion Airport in Tel Aviv. "
        "Afterwards we board our motor coach and go to our hotel on the beautiful"
    )
    assert points == "•\tBen Gurion Airport in Tel Aviv"
    assert format_day_points("• • Visit the Israel Museum") == "•\tVisit the Israel Museum"


def test_jev_asks_for_a_shorter_day_when_the_line_still_does_not_fit():
    from designcraft_brochure.config import Settings
    from designcraft_brochure.content.plan import build_plan
    from designcraft_brochure.jev.brain import scripted_judgment
    from designcraft_brochure.models import Proposal, TemplateInfo

    source = "Day 1: Travel to Jerusalem\nWalk the Old City.\nDay 2: Ephesus\nDock in Turkey."

    def judge(state, questions):
        choices = {}
        nouls = {}
        for key, question in questions.items():
            if question.get("type") == "noul":
                nouls[key] = 0.9
            elif key == "day_1":
                choices[key] = "tighten"
            else:
                choices[key] = next(iter(question["criteria"]))
        return scripted_judgment(questions, choices=choices, nouls=nouls)

    plan = build_plan(
        Proposal(source_path="proposal.pdf", raw_text=source),
        TemplateInfo(id="t", path="t.indd", category="travel", suffix=".indd"),
        type("Brain", (), {"judge": staticmethod(judge)})(),
        Settings(),
    )
    titles = {block.id: block.text for block in plan.blocks if block.role == "day_title"}
    assert titles["day_title_1"] == "JERUSALEM"
    assert titles["day_title_2"] == "EPHESUS"
    bodies = {block.id: block.text for block in plan.blocks if block.role == "day_body"}
    assert bodies["day_body_1"] == "•\tWalk the Old City."
    assert bodies["day_body_2"] == "•\tDock in Turkey."


def test_only_large_proposal_pictures_are_tour_photos(tmp_path: Path):
    large = tmp_path / "large.png"
    small = tmp_path / "small.png"
    copy = tmp_path / "copy.png"
    large.write_bytes(_png(400, 500))
    small.write_bytes(_png(32, 32))
    copy.write_bytes(large.read_bytes())
    assert tour_photo_paths([str(small), str(large), str(copy), str(tmp_path / "missing.png")]) == [str(large)]


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
