"""Apply a confirmed batch of edits to a brochure that was already created.

The operator named the title and the pictures. This does not ask Jev to
invent them, and it does not start a second fill of the proposal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from designcraft_brochure.craft.session import open_session
from designcraft_brochure.craft.validate import CommandContext, validate_action
from designcraft_brochure.models import CandidateAction, Observation, RunOutcome, StoryView
from designcraft_brochure.safety import SafetyError


def apply_revision(
    *,
    source: Path,
    output_dir: Path,
    template_root: Path,
    title: str | None,
    image_paths: list[Path],
    backend: str,
    control_host: str,
    control_port: int,
) -> RunOutcome:
    source = source.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    working = output_dir / f"working{suffix}"
    native_path = output_dir / "brochure.designcraft"
    pdf_path = output_dir / "brochure.pdf"
    idml_path = output_dir / "brochure-edited.idml"
    shutil.copyfile(source, working)
    images = [path.expanduser().resolve() for path in image_paths]
    session = open_session(backend, control_host, control_port, work_root=output_dir)
    notes: list[str] = []
    actions = 0
    try:
        _run(
            session.execute,
            _open_action(working),
            _context(working, native_path, pdf_path, idml_path, template_root, title, images, story_ids=set(), frame_ids=set()),
        )
        actions += 1
        observation = session.inspect()
        if not observation.document_open:
            return _failed(working, "The brochure did not open.")
        if title:
            story = _cover_story(observation)
            if story is None:
                return _failed(working, "The brochure has no text frame for a cover title.")
            _run(
                session.execute,
                _title_action(story, title),
                _context(
                    working, native_path, pdf_path, idml_path, template_root, title, images,
                    story_ids={item.id for item in observation.stories},
                    frame_ids={item.id for item in observation.frames},
                ),
            )
            actions += 1
            notes.append(f"Updated the cover title to {title}.")
            observation = session.inspect()
        for index, image in enumerate(images):
            _run(
                session.execute,
                _place_action(observation, image, index),
                _context(
                    working, native_path, pdf_path, idml_path, template_root, title, images,
                    story_ids={item.id for item in observation.stories},
                    frame_ids={item.id for item in observation.frames},
                ),
            )
            actions += 1
        if images:
            notes.append(f"Placed {len(images)} picture{'s' if len(images) != 1 else ''}.")
        for action, path in (
            (_export("export_pdf", "file.exportPdf", pdf_path), pdf_path),
            (_export("save_native", "file.saveAs", native_path), native_path),
            (_export("export_idml", "file.exportIdml", idml_path), idml_path),
        ):
            _run(
                session.execute,
                action,
                _context(
                    working, native_path, pdf_path, idml_path, template_root, title, images,
                    story_ids={item.id for item in observation.stories},
                    frame_ids={item.id for item in observation.frames},
                ),
            )
            actions += 1
            if not path.is_file():
                return _failed(working, f"{path.name} was not written.")
        if not session.simulated:
            notes.append("DesignCraft closed.")
        return RunOutcome(
            status="completed",
            message=" ".join(notes) or "Applied the changes.",
            pdf_path=str(pdf_path),
            working_copy=str(working),
            native_path=str(native_path),
            idml_path=str(idml_path),
            actions=actions,
            simulated=session.simulated,
        )
    except SafetyError as exc:
        return _failed(working, str(exc))
    except Exception as exc:
        return _failed(working, str(exc))
    finally:
        if not session.simulated:
            try:
                session.quit_app()
            except Exception:
                pass
        session.close()


def _run(execute, action: CandidateAction, context: CommandContext) -> None:
    validate_action(action, context)
    execute(action)


def _context(
    working: Path,
    native_path: Path,
    pdf_path: Path,
    idml_path: Path,
    template_root: Path,
    title: str | None,
    images: list[Path],
    *,
    story_ids: set[int],
    frame_ids: set[int],
) -> CommandContext:
    return CommandContext(
        working_copy=working,
        native_path=native_path,
        pdf_path=pdf_path,
        idml_export_path=idml_path,
        preview_dir=working.parent,
        template_root=template_root,
        allowlist={title} if title else set(),
        image_paths=[str(path) for path in images],
        overwrite=True,
        story_ids=story_ids,
        frame_ids=frame_ids,
    )


def _open_action(working: Path) -> CandidateAction:
    command = "file.openIdml" if working.suffix.lower() == ".idml" else "file.open"
    return CandidateAction(
        id="open_working",
        description="Open the brochure that was just created.",
        risk="low",
        method="engine.execute",
        command=command,
        params={"path": str(working)},
    )


def _title_action(story: StoryView, title: str) -> CandidateAction:
    return CandidateAction(
        id=f"fill_story_{story.id}",
        description=f"Replace the cover title with {title}.",
        risk="low",
        method="engine.execute",
        command="story.setText",
        params={"story": story.id, "text": title},
    )


def _place_action(observation: Observation, image: Path, index: int) -> CandidateAction:
    graphics = [
        frame
        for frame in observation.frames
        if frame.kind.lower() in {"graphic", "image", "rectangleframe"}
    ]
    params: dict = {"path": str(image)}
    if index < len(graphics):
        params["frame"] = graphics[index].id
    else:
        params["x"] = 36
        params["y"] = (36, 220, 400)[index % 3]
        params["width"] = 180
    return CandidateAction(
        id=f"place_image_{index + 1}",
        description=f"Place {image.name}.",
        risk="medium",
        method="engine.execute",
        command="file.place",
        params=params,
    )


def _export(action_id: str, command: str, path: Path) -> CandidateAction:
    return CandidateAction(
        id=action_id,
        description=f"Write {path.name}.",
        risk="medium",
        method="engine.execute",
        command=command,
        params={"path": str(path)},
    )


def _cover_story(observation: Observation) -> StoryView | None:
    if not observation.stories:
        return None
    filled = [story for story in observation.stories if story.preview.strip()]
    pool = filled or list(observation.stories)
    return min(pool, key=lambda story: (story.length or len(story.preview), story.id))


def _failed(working: Path, message: str) -> RunOutcome:
    return RunOutcome(status="failed", message=message, working_copy=str(working))
