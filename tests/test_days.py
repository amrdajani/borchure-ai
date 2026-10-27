"""Swapping itinerary days: finding them and checking the requested order."""

from __future__ import annotations

import pytest

from designcraft_brochure.agent import chat, days


def _story(story, page, text, x, y, w=60, h=14):
    return {"story": story, "frame": story, "page": page, "where": "", "bounds": [x, y, x + w, y + h], "text": text, "style": ""}


def _page():
    stories = []
    for n in range(1, 5):
        top = 100 + (n - 1) * 60
        stories.append(_story(n * 10, 2, f"DAY {n}", 400, top))
        stories.append(_story(n * 10 + 1, 2, f"TITLE {n}", 470, top, 120))
        stories.append(_story(n * 10 + 2, 2, f"Bullet {n}", 470, top + 16, 120, 30))
    stories.append(_story(99, 1, "DAY 1", 10, 10))
    return stories


def test_days_are_found_with_their_title_and_bullets():
    blocks = days.find_days(_page())
    assert [b["day"] for b in blocks] == [1, 2, 3, 4]
    assert blocks[2]["title"]["text"] == "TITLE 3"
    assert blocks[2]["body"]["text"] == "Bullet 3"


def test_a_page_without_days_has_none():
    assert days.find_days([_story(1, 1, "Hello", 0, 0)]) == []


def test_the_model_sees_each_day():
    described = days.describe_days(_page())
    assert [d["day"] for d in described] == [1, 2, 3, 4]
    assert described[0]["title"] == "TITLE 1"


SNAPSHOT = {"stories": [], "shapes": [], "pictures": [], "swatches": [], "days": [{"day": n, "title": f"T{n}", "bullets": []} for n in range(1, 5)]}


def test_a_swap_is_accepted_and_a_wrong_list_is_not():
    assert chat.validate_ops([{"op": "reorder_days", "order": [1, 3, 2, 4]}], SNAPSHOT, image_count=0)[0]["order"] == [1, 3, 2, 4]
    for order in ([1, 2, 3], [1, 2, 3, 3], [1, 2, 3, 9], "1,2,3,4", [True, 2, 3, 4]):
        with pytest.raises(chat.ChatError):
            chat.validate_ops([{"op": "reorder_days", "order": order}], SNAPSHOT, image_count=0)


def test_no_days_means_a_clear_refusal():
    with pytest.raises(chat.ChatError, match="list of days"):
        chat.validate_ops([{"op": "reorder_days", "order": [1, 2]}], {"stories": [], "shapes": [], "pictures": [], "swatches": [], "days": []}, image_count=0)
