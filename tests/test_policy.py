from designcraft_brochure.agent.policy import allowed
from designcraft_brochure.config import Settings
from designcraft_brochure.models import CandidateAction


def test_opening_a_known_file_does_not_require_a_screen_score():
    action = CandidateAction(
        id="open_working",
        description="Open the working copy.",
        risk="medium",
        method="engine.execute",
        command="file.openIdml",
    )
    assert (
        allowed(
            action,
            confidence=0.95,
            screen_understood=0.1,
            safe_to_run=0.9,
            settings=Settings(),
        )
        is None
    )


def test_saving_the_prepared_output_is_not_blocked_by_a_low_safety_score():
    action = CandidateAction(
        id="save_native",
        description="Save the edited document.",
        risk="medium",
        method="engine.execute",
        command="file.saveAs",
    )
    assert (
        allowed(
            action,
            confidence=0.56,
            screen_understood=0.2,
            safe_to_run=0.1,
            settings=Settings(),
        )
        is None
    )


def test_placing_an_image_still_requires_a_clear_inspection():
    action = CandidateAction(
        id="place_image_1",
        description="Place a proposal image.",
        risk="medium",
        method="engine.execute",
        command="file.place",
    )
    reason = allowed(
        action,
        confidence=0.95,
        screen_understood=0.1,
        safe_to_run=0.9,
        settings=Settings(),
    )
    assert reason == "The document inspection is not clear enough for this action."
