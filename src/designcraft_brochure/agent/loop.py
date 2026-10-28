"""Observe, ask Jev, run one DesignCraft command, then check the result."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from designcraft_brochure.agent.candidates import build_candidates
from designcraft_brochure.agent.policy import allowed
from designcraft_brochure.config import Settings
from designcraft_brochure.craft.session import LiveSession, SimulatedSession
from designcraft_brochure.craft.validate import CommandContext, validate_action
from designcraft_brochure.jev.brain import Brain, BrainError, Judgment
from designcraft_brochure.jev.questions import action_questions, safety_questions, verify_questions
from designcraft_brochure.journal import append_journal, public_history_item, save_checkpoint
from designcraft_brochure.models import (
    BrochurePlan,
    CandidateAction,
    Checkpoint,
    Observation,
    QualityReport,
    RunOutcome,
)
from designcraft_brochure.quality.pdf import inspect_pdf
from designcraft_brochure.safety import SafetyError, sha256_file


_OBSERVE_ONLY = {"inspect_document", "render_page"}


@dataclass
class Progress:
    def say(self, message: str) -> None:
        print(message, flush=True)


@dataclass
class RunContext:
    goal: str
    proposal_brief: dict
    plan: BrochurePlan
    template_path: Path
    template_id: str
    template_sha256: str
    template_root: Path
    proposal_path: Path
    working_copy: Path
    native_path: Path
    pdf_path: Path
    idml_export_path: Path
    output_root: Path
    preview_dir: Path
    journal_path: Path
    checkpoint_path: Path
    pause_path: Path
    allowlist: set[str]
    image_paths: list[str]
    overwrite_confirmed: bool
    backend: str
    control_host: str = "127.0.0.1"
    control_port: int = 7979
    action_count: int = 0
    history: list[dict] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    polished: bool = False


def run_loop(
    context: RunContext,
    brain: Brain,
    session: SimulatedSession | LiveSession,
    settings: Settings,
    progress: Progress,
) -> RunOutcome:
    stop_rejections = 0
    outcome: RunOutcome | None = None
    try:
        for _ in range(settings.max_actions):
            if context.pause_path.is_file():
                _checkpoint(context)
                outcome = _outcome(context, session, "needs_assistance", "Paused because logs/PAUSE exists.")
                return outcome
            observation = session.inspect()
            quality = _quality(context)
            if _finished(context, quality):
                _checkpoint(context)
                outcome = _outcome(context, session, "completed", _completed_message(context, session))
                return outcome
            candidates = build_candidates(
                observation,
                context.plan,
                working_copy=context.working_copy,
                native_path=context.native_path,
                pdf_path=context.pdf_path,
                idml_export_path=context.idml_export_path,
                preview_path=context.preview_dir / "page-render.png",
                overwrite=context.overwrite_confirmed,
            )
            forced = _export_without_asking(candidates, observation)
            if forced is not None:
                action = forced
                confidence = 1.0
                judgment = Judgment("brochure-api", {}, {})
                progress.say(f"Nothing left to edit. Continuing with {action.id}.")
            else:
                chosen = _choose_action(
                    brain, context, session, observation, quality, candidates, settings, progress
                )
                if isinstance(chosen, RunOutcome):
                    outcome = chosen
                    return outcome
                action, confidence, judgment = chosen
                if action.id == "stop_agent" or judgment.noul("task_complete") >= settings.task_complete_noul:
                    if _finished(context, quality):
                        _checkpoint(context)
                        outcome = _outcome(context, session, "completed", _completed_message(context, session))
                        return outcome
                    stop_rejections += 1
                    if stop_rejections >= 2:
                        _checkpoint(context)
                        outcome = _outcome(
                            context,
                            session,
                            "needs_assistance",
                            "Jev tried to stop before the PDF passed inspection and the native file was saved.",
                        )
                        return outcome
                    continue
            if context.failures.get(action.id, 0) >= settings.retry_limit:
                _checkpoint(context)
                outcome = _outcome(context, session, "needs_assistance", f"{action.id} failed the retry limit.")
                return outcome
            if not _template_intact(context):
                outcome = _outcome(context, session, "failed", "The original template changed. Stopping.")
                return outcome
            try:
                reason = None
                if forced is None:
                    reason = _safety_reason(
                        brain,
                        action,
                        observation,
                        confidence,
                        judgment.noul("screen_understood"),
                        settings,
                    )
                validate_action(action, _command_context(context, observation))
            except (SafetyError, BrainError) as exc:
                _reject(context, action, exc.__class__.__name__)
                if context.failures.get(action.id, 0) >= settings.retry_limit:
                    outcome = _outcome(context, session, "needs_assistance", str(exc))
                    return outcome
                continue
            if reason:
                progress.say(f"Held {action.id}: {reason}")
                _reject(context, action, reason)
                if context.failures.get(action.id, 0) >= settings.retry_limit:
                    outcome = _outcome(context, session, "needs_assistance", reason)
                    return outcome
                continue
            if action.id in _FINISH_ACTIONS and not context.polished:
                _polish(context, session, progress)
            try:
                session.execute(action)
            except KeyboardInterrupt:
                _checkpoint(context)
                outcome = _outcome(context, session, "stopped", "Stopped by the operator.")
                return outcome
            except Exception as exc:
                _reject(context, action, exc.__class__.__name__)
                if context.failures.get(action.id, 0) >= settings.retry_limit:
                    outcome = _outcome(context, session, "needs_assistance", str(exc))
                    return outcome
                continue
            context.action_count += 1
            after = session.inspect()
            local_ok, detail = _local_effect(action, observation, after, context)
            verify_noul = 1.0 if forced is not None else 0.0
            if forced is None:
                try:
                    verified = brain.judge(
                        {
                            "before": observation.summary(),
                            "after": after.summary(),
                            "intended_effect": action.description,
                            "local_evidence": local_ok,
                        },
                        verify_questions(action.description),
                    )
                    verify_noul = verified.noul("effect_happened")
                except BrainError as exc:
                    _checkpoint(context)
                    outcome = _outcome(context, session, "failed", str(exc))
                    return outcome
            if local_ok is False:
                ok = False
            elif local_ok is True and (action.risk == "low" or action.id in _FINISH_ACTIONS):
                ok = True
            else:
                ok = verify_noul >= settings.verify_noul_min
            if not ok:
                context.failures[action.id] = context.failures.get(action.id, 0) + 1
            _record(context, action, ok, detail if ok else "not verified")
            _journal(context, action, ok, judgment.model)
            _checkpoint(context)
            if len(context.history) >= 4 and all(item["action"] in _OBSERVE_ONLY for item in context.history[-4:]):
                outcome = _outcome(
                    context,
                    session,
                    "needs_assistance",
                    "The last actions did not change the document.",
                )
                return outcome
        _checkpoint(context)
        outcome = _outcome(context, session, "needs_assistance", "Stopped at the maximum action count.")
        return outcome
    finally:
        note = _publish_edited_copy(context, session, progress)
        if outcome is not None and note:
            outcome.message = f"{outcome.message} {note}"
            outcome.native_path = str(context.native_path) if context.native_path.is_file() else outcome.native_path
            outcome.idml_path = str(context.idml_export_path) if context.idml_export_path.is_file() else None
        session.close()


def _export_without_asking(candidates: list[CandidateAction], observation: Observation) -> CandidateAction | None:
    """Save and export once no story still has a replacement waiting."""
    if not observation.document_open:
        return None
    if any(item.id.startswith("fill_story_") for item in candidates):
        return None
    by_id = {item.id: item for item in candidates}
    for action_id in _FINISH_ACTIONS:
        action = by_id.get(action_id)
        if action is not None:
            return action
    return None


def _choose_action(
    brain: Brain,
    context: RunContext,
    session: SimulatedSession | LiveSession,
    observation: Observation,
    quality: QualityReport | None,
    candidates: list[CandidateAction],
    settings: Settings,
    progress: Progress,
) -> tuple[CandidateAction, float, Judgment] | RunOutcome:
    try:
        judgment = brain.judge(
            _decision_state(context, observation, quality),
            action_questions(candidates),
        )
    except BrainError as exc:
        _checkpoint(context)
        return _outcome(context, session, "failed", str(exc))
    except KeyboardInterrupt:
        _checkpoint(context)
        return _outcome(context, session, "stopped", "Stopped by the operator.")
    selected = judgment.choice("next_action").choice
    action = next((item for item in candidates if item.id == selected), None)
    if action is None:
        _checkpoint(context)
        return _outcome(context, session, "needs_assistance", "Jev selected an action that was not offered.")
    confidence = judgment.choice("next_action").confidence
    progress.say(f"Jev chose {action.id} (confidence {confidence:.2f}).")
    if action.id != "request_human":
        return action, confidence, judgment
    finish = _pending_finish(candidates, context, settings)
    if finish is None:
        _checkpoint(context)
        if _finished(context, quality):
            return _outcome(context, session, "completed", _completed_message(context, session))
        return _outcome(context, session, "needs_assistance", "Jev asked for a person.")
    progress.say(f"Jev asked for a person. Continuing with {finish.id}.")
    return finish, confidence, judgment


def _polish(context: RunContext, session: SimulatedSession | LiveSession, progress: Progress) -> None:
    """Restore the template's look once, after every fill and before the first save."""
    context.polished = True
    try:
        notes = session.polish(context.plan, context.output_root)
    except Exception as exc:
        progress.say(f"Finishing pass failed: {exc}")
        return
    for note in notes:
        progress.say(f"Finishing: {note}")


def _finished(context: RunContext, quality: QualityReport | None) -> bool:
    if quality is None or not context.native_path.is_file() or not _template_intact(context):
        return False
    if quality.status == "ok":
        return True
    # The template may have no story for every price. A PDF that opens and has
    # text is still the brochure.
    blocking = [issue for issue in quality.issues if not issue.startswith("Some approved phrases")]
    return quality.opened and quality.page_count >= 1 and not quality.blank_pages and not blocking


def _quality(context: RunContext) -> QualityReport | None:
    if not context.pdf_path.is_file():
        return None
    return inspect_pdf(context.pdf_path, context.plan, context.preview_dir)


def _decision_state(context: RunContext, observation: Observation, quality: QualityReport | None) -> dict:
    report = None
    if quality is not None:
        report = {
            "status": quality.status,
            "page_count": quality.page_count,
            "issues": quality.issues[:6],
            "missing_phrases": len(quality.missing_phrases),
        }
    return {
        "goal": context.goal,
        "document": observation.summary(),
        "native_saved": context.native_path.is_file(),
        "pdf_written": context.pdf_path.is_file(),
        "quality": report,
        "plan_blocks": len(context.plan.blocks),
        "history": context.history[-8:],
    }


_FINISH_ACTIONS = ("save_native", "export_pdf", "export_idml")


def _pending_finish(
    candidates: list[CandidateAction],
    context: RunContext,
    settings: Settings,
) -> CandidateAction | None:
    by_id = {item.id: item for item in candidates}
    for action_id in _FINISH_ACTIONS:
        action = by_id.get(action_id)
        if action is None:
            continue
        if context.failures.get(action_id, 0) >= settings.retry_limit:
            continue
        return action
    return None


def _safety_reason(brain, action, observation, confidence, screen_understood, settings: Settings) -> str | None:
    judgment = brain.judge(
        {"document": observation.summary(), "action_id": action.id, "risk": action.risk},
        safety_questions(action),
    )
    return allowed(
        action,
        confidence=confidence,
        screen_understood=screen_understood,
        safe_to_run=judgment.noul("safe_to_run"),
        settings=settings,
    )


def _command_context(context: RunContext, observation: Observation) -> CommandContext:
    return CommandContext(
        working_copy=context.working_copy,
        native_path=context.native_path,
        pdf_path=context.pdf_path,
        idml_export_path=context.idml_export_path,
        preview_dir=context.preview_dir,
        template_root=context.template_root,
        allowlist=set(context.allowlist),
        image_paths=list(context.image_paths),
        overwrite=context.overwrite_confirmed,
        story_ids={story.id for story in observation.stories},
        frame_ids={frame.id for frame in observation.frames},
    )


def _local_effect(
    action: CandidateAction,
    before: Observation,
    after: Observation,
    context: RunContext,
) -> tuple[bool, str]:
    if action.id == "open_working":
        return after.document_open, "document open" if after.document_open else "document stayed closed"
    if action.id.startswith("fill_story_"):
        story_id = action.params.get("story")
        expected = str(action.params.get("text", ""))
        preview = next((story.preview for story in after.stories if story.id == story_id), "")
        changed = preview != next((story.preview for story in before.stories if story.id == story_id), None)
        head = expected.split("\n", 1)[0][:80]
        present = bool(head) and head.lower() in preview.lower()
        if present and not changed:
            return True, "story already held that text"
        return bool(changed and present), "story updated" if changed and present else "story text did not match"
    if action.id.startswith("place_image_"):
        return True, "image place command returned"
    if action.id == "save_native":
        return context.native_path.is_file(), "native file saved" if context.native_path.is_file() else "native file missing"
    if action.id == "export_pdf":
        return context.pdf_path.is_file(), "pdf written" if context.pdf_path.is_file() else "pdf missing"
    if action.id == "export_idml":
        return context.idml_export_path.is_file(), "idml written" if context.idml_export_path.is_file() else "idml missing"
    if action.id in _OBSERVE_ONLY:
        return True, "observation updated"
    return False, "no local evidence"


def _reject(context: RunContext, action: CandidateAction, detail: str) -> None:
    context.failures[action.id] = context.failures.get(action.id, 0) + 1
    _record(context, action, False, detail)
    _checkpoint(context)


def _record(context: RunContext, action: CandidateAction, ok: bool, detail: str) -> None:
    context.history.append(public_history_item(action.id, action.id, ok, detail))


def _journal(context: RunContext, action: CandidateAction, ok: bool, model: str) -> None:
    append_journal(
        context.journal_path,
        {
            "action": action.id,
            "method": action.method,
            "command": action.command,
            "ok": ok,
            "model": model,
        },
    )


def _template_intact(context: RunContext) -> bool:
    return sha256_file(context.template_path) == context.template_sha256


def _publish_edited_copy(
    context: RunContext,
    session: SimulatedSession | LiveSession,
    progress: Progress,
) -> str:
    """Write the edited IDML, then close DesignCraft. The original .indd is not changed."""
    notes: list[str] = []
    if session.opened_path:
        try:
            session.save_outputs(context.native_path, context.idml_export_path)
            progress.say(f"Saved edited copy: {context.idml_export_path}")
            notes.append(
                "DesignCraft cannot write a binary .indd. "
                f"The edited file InDesign opens is {context.idml_export_path}."
            )
        except Exception as exc:
            progress.say(f"Could not save the edited copy: {exc}")
            notes.append(f"The edited copy was not saved ({exc}).")
    if not session.simulated:
        try:
            session.quit_app()
            progress.say("DesignCraft closed.")
            notes.append("DesignCraft closed.")
        except Exception as exc:
            progress.say(f"DesignCraft did not close: {exc}")
            notes.append(f"DesignCraft did not close ({exc}).")
    return " ".join(notes)


def _completed_message(context: RunContext, session: SimulatedSession | LiveSession) -> str:
    if session.simulated:
        return (
            "Simulated mode wrote the PDF with PyMuPDF and a JSON snapshot of the stories. "
            "DesignCraft itself did not export this file. "
            f"PDF: {context.pdf_path}. Snapshot: {context.native_path}."
        )
    return (
        "DesignCraft saved the native file and exported the PDF. "
        f"PDF: {context.pdf_path}. Native file: {context.native_path}."
    )


def _outcome(
    context: RunContext,
    session: SimulatedSession | LiveSession,
    status: str,
    message: str,
) -> RunOutcome:
    quality = _quality(context)
    return RunOutcome(
        status=status,  # type: ignore[arg-type]
        message=message,
        pdf_path=str(context.pdf_path) if context.pdf_path.is_file() else None,
        working_copy=str(context.working_copy),
        native_path=str(context.native_path) if context.native_path.is_file() else None,
        idml_path=str(context.idml_export_path) if context.idml_export_path.is_file() else None,
        actions=context.action_count,
        quality_status=quality.status if quality else None,
        simulated=session.simulated,
    )


def _checkpoint(context: RunContext) -> None:
    save_checkpoint(
        context.checkpoint_path,
        Checkpoint(
            proposal_path=str(context.proposal_path),
            template_path=str(context.template_path),
            template_root=str(context.template_root),
            template_id=context.template_id,
            template_sha256=context.template_sha256,
            working_copy=str(context.working_copy),
            native_path=str(context.native_path),
            pdf_path=str(context.pdf_path),
            idml_export_path=str(context.idml_export_path),
            plan=context.plan,
            proposal_brief=context.proposal_brief,
            allowlist=sorted(context.allowlist),
            image_paths=list(context.image_paths),
            action_count=context.action_count,
            history=list(context.history),
            failures=dict(context.failures),
            overwrite_confirmed=context.overwrite_confirmed,
            backend=context.backend,
            control_host=context.control_host,
            control_port=context.control_port,
        ),
    )
