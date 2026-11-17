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
