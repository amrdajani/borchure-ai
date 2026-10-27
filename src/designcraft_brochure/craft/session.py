"""DesignCraft sessions.

Live mode speaks the control protocol to an app started with `--control`.
Simulated mode keeps an in-memory document so the decision loop can be tested
without the app. Its PDF is written with PyMuPDF, not by DesignCraft.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from designcraft_brochure.craft.client import ControlClient
from designcraft_brochure.craft.normalize import observation_from_inspect
from designcraft_brochure.models import CandidateAction, FrameView, Observation, StoryView
from designcraft_brochure.templates.library import read_idml_stories, read_snapshot, write_idml_stories


class SessionError(RuntimeError):
    pass


class SimulatedSession:
    simulated = True

    def __init__(self) -> None:
        self.opened_path: Path | None = None
        self.stories: list[StoryView] = []
        self.frames: list[FrameView] = []
        self.images: list[str] = []
        self._texts: dict[int, str] = {}

    def inspect(self) -> Observation:
        if self.opened_path is None:
            return Observation(
                document_open=False,
                limits=["No document is open in the simulated DesignCraft session."],
            )
        return Observation(
            document_open=True,
            path=str(self.opened_path),
            page_count=1,
            stories=list(self.stories),
            frames=list(self.frames),
        )

    def execute(self, action: CandidateAction) -> dict:
        command = action.command
        params = action.params
        if action.method == "document.inspect":
            return self.inspect().summary()
        if action.method == "ui.render":
            return _render_preview(Path(str(params["path"])))
        if command in {"file.open", "file.openIdml"}:
            self._open(Path(str(params["path"])))
            return {"path": str(self.opened_path), "stories": len(self.stories)}
        if command == "story.setText":
            story_id = int(params["story"])
            text = str(params["text"])
            self._texts[story_id] = text
            self.stories = [
                story.model_copy(update={"preview": text[:500], "length": len(text.encode("utf-8")), "overset": False})
                if story.id == story_id
                else story
                for story in self.stories
            ]
            return {"story": story_id, "length": len(text.encode("utf-8"))}
        if command == "file.place":
            path = str(params["path"])
            if path not in self.images:
                self.images.append(path)
            return {"frame": params.get("frame"), "path": path}
        if command == "file.saveAs":
            return self._save(Path(str(params["path"])))
        if command == "file.exportPdf":
            return self._export_pdf(Path(str(params["path"])))
        if command == "file.exportIdml":
            return self._export_idml(Path(str(params["path"])))
        raise SessionError(f"Simulated session cannot run {command or action.method}.")

    def polish(self, plan, work_dir: Path | None = None) -> list[str]:
        return []

    def save_outputs(self, native_path: Path, idml_path: Path) -> None:
        if self.opened_path is None:
            return
        self._save(native_path)
        self._export_idml(idml_path)

    def quit_app(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _open(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix == ".idml":
            texts, _pages, images = read_idml_stories(path)
        elif suffix == ".designcraft":
            payload = read_snapshot(path)
            texts = [str(item.get("text", "")) for item in payload.get("stories", [])]
            images = 0
        else:
            raise SessionError("DesignCraft cannot open this file.")
        if not texts:
            texts = [""]
        self.opened_path = path
        self._texts = {index + 1: text for index, text in enumerate(texts)}
        self.stories = [
            StoryView(id=index, preview=text[:500], length=len(text.encode("utf-8")))
            for index, text in self._texts.items()
        ]
        self.frames = [
            FrameView(id=100 + story.id, kind="text", story=story.id) for story in self.stories
        ]
        if images:
            self.frames.append(FrameView(id=1000, kind="graphic"))
        self.images = []

    def _save(self, path: Path) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "designcraft-brochure-snapshot",
            "stories": [{"id": story_id, "text": text} for story_id, text in self._texts.items()],
            "note": "Simulated snapshot. The DesignCraft app writes a real .designcraft package.",
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"path": str(path)}

    def _export_pdf(self, path: Path) -> dict:
        import fitz

        path.parent.mkdir(parents=True, exist_ok=True)
        lines = _wrap_lines("\n".join(self._texts.values()) or " ")
        height = max(792, 48 + 16 * len(lines))
        document = fitz.open()
        page = document.new_page(width=612, height=height)
        y = 36
        for line in lines:
            page.insert_text((48, y), line, fontsize=11)
            y += 16
        if self.images and height > 760:
            for image in self.images:
                if Path(image).is_file():
                    try:
                        page.insert_image(fitz.Rect(48, height - 220, 240, height - 40), filename=image)
                    except Exception:
                        continue
        document.save(path)
        document.close()
        return {"path": str(path), "pages": 1, "bytes": path.stat().st_size}

    def _export_idml(self, path: Path) -> dict:
        texts = [self._texts[story.id] for story in self.stories]
        if self.opened_path and self.opened_path.suffix.lower() == ".idml":
            write_idml_stories(self.opened_path, path, texts)
        else:
            _minimal_idml(path, texts)
        return {"path": str(path), "bytes": path.stat().st_size}


class LiveSession:
    simulated = False

    def __init__(self, client: ControlClient) -> None:
        self.client = client
        self.opened_path: str | None = None

    def inspect(self) -> Observation:
        if not self.opened_path:
            return Observation(
                document_open=False,
                limits=["The working copy is not the active DesignCraft document yet."],
            )
        try:
            result = self.client.call("document.inspect", {})
        except Exception as exc:
            return Observation(document_open=False, error=str(exc))
        observation = observation_from_inspect(result, path=self.opened_path)
        observation.path = self.opened_path
        if not observation.document_open:
            observation.error = observation.error or "DesignCraft inspect returned no pages or stories."
        return observation

    def execute(self, action: CandidateAction) -> dict:
        if action.method == "engine.execute":
            result = self.client.call(
                "engine.execute",
                {"command": action.command, "params": action.params},
            )
            if action.command in {"file.open", "file.openIdml"}:
                self.opened_path = str(action.params.get("path"))
        else:
            result = self.client.call(action.method, action.params)
        if isinstance(result, dict):
            return result
        return {"value": result}

    def polish(self, plan, work_dir: Path | None = None) -> list[str]:
        """Restore fonts, spacing, bar alignment, and pictures after the text is filled."""
        if not self.opened_path:
            return []
        from designcraft_brochure.craft.polish import polish_document

        return polish_document(self.client, plan, work_dir=work_dir)

    def save_outputs(self, native_path: Path, idml_path: Path) -> None:
        if not self.opened_path:
            return
        native_path.parent.mkdir(parents=True, exist_ok=True)
        idml_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.call(
            "engine.execute",
            {"command": "file.exportIdml", "params": {"path": str(idml_path.resolve())}},
        )
        self.client.call(
            "engine.execute",
            {"command": "file.saveAs", "params": {"path": str(native_path.resolve())}},
        )

    def quit_app(self) -> None:
        self.client.quit_app()

    def close(self) -> None:
        self.client.close()


def open_session(
    backend: str,
    host: str,
    port: int,
    *,
    work_root: Path | None = None,
    read_roots: list[Path] | None = None,
) -> SimulatedSession | LiveSession:
    if backend == "simulated":
        return SimulatedSession()
    if backend == "headless":
        from designcraft_brochure.craft.headless import HeadlessCraft

        if work_root is None:
            raise SessionError("Headless DesignCraft needs a working folder.")
        return LiveSession(HeadlessCraft(work_root, read_roots=read_roots))
    if backend == "live":
        return LiveSession(ControlClient(host, port))
    if backend == "auto":
        client = ControlClient(host, port, timeout=1.5)
        try:
            client.call("engine.commands", {})
        except Exception as exc:
            client.close()
            raise SessionError(
                f"DesignCraft is not answering on {host}:{port}. "
                f"Start it with `designcraft --control {port}`, or pass --backend simulated. ({exc})"
            ) from exc
        return LiveSession(client)
    raise SessionError(f"Unknown backend {backend!r}.")


def _wrap_lines(text: str, width: int = 80) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        remaining = paragraph
        if not remaining:
            lines.append("")
            continue
        while len(remaining) > width:
            lines.append(remaining[:width])
            remaining = remaining[width:]
        lines.append(remaining)
    return lines


def _render_preview(path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        ))
    else:
        Image.new("RGB", (32, 32), (245, 242, 234)).save(path)
    return {"path": str(path), "width": 32, "height": 32}


def _minimal_idml(path: Path, texts: list[str]) -> None:
    import zipfile
    from xml.sax.saxutils import escape

    path.parent.mkdir(parents=True, exist_ok=True)
    designmap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Document>',
        '<idPkg:Spread src="Spreads/Spread_s1.xml"/>',
    ]
    for index in range(len(texts)):
        designmap.append(f'<idPkg:Story src="Stories/Story_t{index + 1}.xml"/>')
    designmap.append("</Document>")
    spread = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Spread><Page/></Spread>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("designmap.xml", "\n".join(designmap))
        package.writestr("Spreads/Spread_s1.xml", spread)
        for index, text in enumerate(texts, start=1):
            story = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<Story><Content>{escape(text)}</Content></Story>"
            )
            package.writestr(f"Stories/Story_t{index}.xml", story)
