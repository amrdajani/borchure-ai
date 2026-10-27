"""Confidence and safety gates around a Jev choice.

Thresholds are starting points from TypeSafe's confidence guidance. They are
stricter for typing and export than for reading the document inspection.
"""

from __future__ import annotations

from designcraft_brochure.config import Settings
from designcraft_brochure.models import CandidateAction


def allowed(
    action: CandidateAction,
    *,
    confidence: float,
    screen_understood: float,
    safe_to_run: float,
    settings: Settings,
) -> str | None:
    """Return a reason to stop for a person, or None when the action may run."""
    # These write only to paths this run already prepared. A low safety score
    # must not stop the brochure after the text has been filled in.
    if action.id in {"save_native", "export_pdf", "export_idml"}:
        return None
    # Opening uses a path that was already chosen. A closed document has no
    # stories or frames yet, and that is a complete inspection for this command.
    path_already_chosen = action.id in {"open_working", "save_native", "export_pdf", "export_idml"} or action.id.startswith("fill_story_")
    if not path_already_chosen and action.risk != "low" and screen_understood < settings.screen_noul_min:
        return "The document inspection is not clear enough for this action."
    if action.risk == "low":
        minimum_safe = settings.safe_noul_low
        minimum_confidence = 0.0
    elif action.risk == "medium":
        minimum_safe = settings.safe_noul_medium
        minimum_confidence = settings.confidence_medium
    else:
        minimum_safe = settings.safe_noul_high
        minimum_confidence = settings.confidence_high
    if confidence < minimum_confidence:
        return "Jev's choice is too uncertain for this action."
    if safe_to_run < minimum_safe:
        return "Jev does not consider this action safe to run unattended."
    return None
