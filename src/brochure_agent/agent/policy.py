"""Confidence and safety gates around a Jev choice.

Thresholds are starting points from TypeSafe's confidence guidance. They are
stricter for typing and export than for a screenshot.
"""

from __future__ import annotations

from brochure_agent.config import Settings
from brochure_agent.models import CandidateAction


def allowed(
    action: CandidateAction,
    *,
    confidence: float,
    screen_understood: float,
    safe_to_run: float,
    settings: Settings,
) -> str | None:
    """Return a reason to stop for a person, or None when the action may run."""
    if screen_understood < settings.screen_noul_min and action.risk != "low":
        return "The screen observation is not clear enough for this action."
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
