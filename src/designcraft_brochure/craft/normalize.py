"""Turn a document.inspect payload into the fields this agent uses.

DesignCraft's inspect result lists pages, spreads, items, and stories. The
live shape can nest those lists, so the reader walks the JSON instead of
assuming one exact layout.
"""

from __future__ import annotations

from designcraft_brochure.models import FrameView, Observation, StoryView


def observation_from_inspect(payload: object, *, path: str | None = None) -> Observation:
    if not isinstance(payload, dict):
        return Observation(document_open=False, error="DesignCraft inspect did not return an object.")
    stories = [_story(item) for item in _find_lists(payload, "stories")]
    stories = [item for item in stories if item is not None]
    frames = [_frame(item) for item in _find_lists(payload, "items")]
    frames = [item for item in frames if item is not None]
    pages = _find_lists(payload, "pages")
    page_count = len(pages) if pages else _int(payload.get("pageCount") or payload.get("page_count")) or (1 if stories else 0)
    return Observation(
        document_open=bool(stories or pages or payload.get("open") is True),
        path=path,
        page_count=page_count,
        stories=stories,
        frames=frames,
    )


def _story(item: object) -> StoryView | None:
    if not isinstance(item, dict) or "id" not in item:
        return None
    story_id = _int(item.get("id"))
    if story_id is None:
        return None
    preview = item.get("preview") or item.get("text") or ""
    if not isinstance(preview, str):
        preview = str(preview)
    length = _int(item.get("length"))
    if length is None:
        length = len(preview.encode("utf-8"))
    return StoryView(
        id=story_id,
        preview=preview,
        length=length,
        overset=bool(item.get("overset")),
    )


def _frame(item: object) -> FrameView | None:
    if not isinstance(item, dict) or "id" not in item:
        return None
    frame_id = _int(item.get("id"))
    if frame_id is None:
        return None
    kind = str(item.get("kind") or item.get("type") or "item")
    story = _int(item.get("story"))
    return FrameView(id=frame_id, kind=kind, story=story)


def _find_lists(payload: object, key: str, depth: int = 0) -> list:
    if depth > 6:
        return []
    found: list = []
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            found.extend(value)
        for child_key, child in payload.items():
            if child_key == key:
                continue
            if isinstance(child, (dict, list)):
                found.extend(_find_lists(child, key, depth + 1))
    elif isinstance(payload, list):
        for child in payload:
            if isinstance(child, (dict, list)):
                found.extend(_find_lists(child, key, depth + 1))
    return found


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
