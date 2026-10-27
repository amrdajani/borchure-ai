"""Upload API in front of DesignCraft.

POST the proposal and the brochure file. Jev chooses each edit. The
headless backend sends that edit to `designcraft-cli`. No desktop app runs.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from designcraft_brochure.agent.loop import Progress, run_loop
from designcraft_brochure.agent.prepare import prepare_run
from designcraft_brochure.agent import chat as chat_edit
from designcraft_brochure.agent.revise import apply_revision
from designcraft_brochure.config import Settings
from designcraft_brochure.craft.client import ControlClient
from designcraft_brochure.craft.headless import cli_installed
from designcraft_brochure.craft.session import open_session
from designcraft_brochure.jev.brain import TypeSafeBrain
from designcraft_brochure.models import RunOutcome
from designcraft_brochure.previews import render_pages
from designcraft_brochure.content.plan import normalize_tour_code
from designcraft_brochure.safety import SafetyError, is_suspicious
from designcraft_brochure.templates.saved import LibraryError, TemplateLibrary


PROPOSAL_SUFFIXES = {".pdf", ".docx", ".txt"}
TEMPLATE_SUFFIXES = {".indd", ".idml", ".designcraft"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}
MAX_BODY_BYTES = 80 * 1024 * 1024
MAX_FIELD_BYTES = 4096
_JOB_ID = re.compile(r"[0-9a-f]{32}")

Runner = Callable[["JobPaths", Settings, str], RunOutcome]
MAX_CHAT_MESSAGE = 2000
MAX_CHAT_IMAGES = 4
ReadyCheck = Callable[[Settings], bool]


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class UploadedFile:
    field: str
    filename: str
    data: bytes


@dataclass
class FormParts:
    files: dict[str, UploadedFile]
    images: list[UploadedFile]
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class ChatJob:
    source: Path
    revision_dir: Path
    template_root: Path
    message: str
    history: list[dict]
    images: list[Path]


@dataclass
class ChatOutcome:
    reply: str
    changed: bool = False
    undo: bool = False
    pdf_path: str | None = None
    native_path: str | None = None
    idml_path: str | None = None
    notes: list[str] = field(default_factory=list)


ChatRunner = Callable[["ChatJob", Settings, str], ChatOutcome]


@dataclass
class JobPaths:
    job_id: str
    root: Path
    proposal_path: Path
    template_root: Path
    output_dir: Path
    preview_dir: Path
    log_dir: Path
    tour_code: str | None = None


class JobLog(Progress):
    def __init__(self, path: Path) -> None:
        self.path = path

    def say(self, message: str) -> None:
        print(message, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")


class BrochureAPI:
    def __init__(
        self,
        jobs_root: Path,
        settings: Settings,
        *,
        backend: str = "live",
        runner: Runner | None = None,
        chat_runner: ChatRunner | None = None,
        designcraft_ready: ReadyCheck | None = None,
        library_root: Path | None = None,
        token: str = "",
    ) -> None:
        self.library = TemplateLibrary(library_root or jobs_root.parent / "saved-templates")
        self.jobs_root = jobs_root
        self.settings = settings
        self.backend = backend
        self.token = token
        self.runner = runner or run_uploaded_job
        self.chat_runner = chat_runner or run_chat_job
        self.designcraft_ready = designcraft_ready or designcraft_is_ready
        self._lock = threading.Lock()

    def handle(self, method: str, target: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        path = urlparse(target).path
        if self.token and not _bearer_ok(headers, self.token):
            if not (method == "GET" and path == "/health"):
                return _json(401, {"status": "failed", "message": "Missing or invalid API token."})
        try:
            if method == "GET" and path == "/health":
                return self._health()
            if method == "POST" and path == "/brochures":
                return self._create(headers, body)
            if path == "/templates" and method in {"GET", "POST"}:
                return self._templates(method, headers, body)
            saved = re.fullmatch(r"/templates/([0-9a-f]{32})", path)
            if saved and method in {"POST", "DELETE"}:
                return self._template(method, saved.group(1), headers, body)
            revision = re.fullmatch(r"/brochures/([0-9a-f]{32})/revisions", path)
            if method == "POST" and revision:
                return self._revise(revision.group(1), headers, body)
            chat = re.fullmatch(r"/brochures/([0-9a-f]{32})/chat", path)
            if method == "POST" and chat:
                return self._chat(chat.group(1), headers, body)
            if method == "GET" and path.startswith("/brochures/"):
                return self._download(path)
        except ApiError as exc:
            return _json(exc.status, {"status": "failed", "message": exc.message})
        return _json(404, {"status": "failed", "message": "Unknown route."})

    def _health(self) -> tuple[int, bytes, str]:
        ready = False
        if self.backend == "headless":
            ready = cli_installed()
        elif self.backend != "live":
            ready = True
        else:
            try:
                ready = self.designcraft_ready(self.settings)
            except Exception:
                ready = False
        return _json(200, {"designcraft": ready, "backend": self.backend})

    def _create(self, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        self._require_ready()
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "Upload is larger than 80 MB.")
        content_type = _header(headers, "content-type")
        parts = parse_multipart(content_type, body)
        files = parts.files
        tour_code = _read_tour_code(parts.fields.get("tour_code"))
        proposal = _required_file(files, "proposal", PROPOSAL_SUFFIXES)
        template = self._template_for_run(files, parts.fields.get("template_id"))
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "A brochure is already running. Wait for it to finish.")
        job_id = uuid.uuid4().hex
        try:
            job = _prepare_job(self.jobs_root, job_id, proposal, template)
            job.tour_code = tour_code
            outcome = self.runner(job, self.settings, self.backend)
        except ApiError:
            raise
        except Exception as exc:
            outcome = RunOutcome(status="failed", message=str(exc))
        finally:
            self._lock.release()
        root = self.jobs_root / job_id
        root.mkdir(parents=True, exist_ok=True)
        payload = _public_result(job_id, outcome)
        pages = _first_pages(root, outcome)
        payload["pages"] = [f"/brochures/{job_id}/page/{n}" for n in range(1, len(pages) + 1)]
        payload["version"] = 0
        _write_result(root, payload, outcome, pages=pages)
        status = {"completed": 200, "needs_assistance": 422, "stopped": 409, "failed": 500}[outcome.status]
        return _json(status, payload)

    def _template_for_run(self, files: dict[str, UploadedFile], template_id: str | None) -> UploadedFile:
        """A template sent with the request wins. Otherwise use one from the library."""
        if "template" in files:
            return _required_file(files, "template", TEMPLATE_SUFFIXES)
        if not template_id or not template_id.strip():
            raise ApiError(400, "Choose a template.")
        try:
            meta, data = self.library.read(template_id.strip())
        except LibraryError as exc:
            raise ApiError(exc.status, exc.message) from None
        return UploadedFile(field="template", filename=meta["filename"], data=data)

    def _templates(self, method: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        if method == "GET":
            return _json(200, {"templates": [_public_template(item) for item in self.library.list()]})
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "Upload is larger than 80 MB.")
        parts = parse_multipart(_header(headers, "content-type"), body)
        uploaded = _required_file(parts.files, "template", TEMPLATE_SUFFIXES)
        filename = _safe_filename(uploaded.filename, TEMPLATE_SUFFIXES)
        try:
            meta, existing = self.library.add(parts.fields.get("name"), filename, uploaded.data)
        except LibraryError as exc:
            raise ApiError(exc.status, exc.message) from None
        return _json(200 if existing else 201, {"status": "completed", "existing": existing, "template": _public_template(meta)})

    def _template(self, method: str, template_id: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        try:
            if method == "DELETE":
                self.library.delete(template_id)
                return _json(200, {"status": "completed"})
            parts = parse_multipart(_header(headers, "content-type"), body)
            meta = self.library.rename(template_id, parts.fields.get("name"))
        except LibraryError as exc:
            raise ApiError(exc.status, exc.message) from None
        return _json(200, {"status": "completed", "template": _public_template(meta)})

    def _chat(self, job_id: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        root = self.jobs_root / job_id
        record_path = root / "result.json"
        if not record_path.is_file():
            raise ApiError(404, "Unknown brochure.")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("status") != "completed":
            raise ApiError(422, "Create the brochure before changing it.")
        source = _revision_source(root, record)
        if source is None:
            raise ApiError(422, "This brochure has no edited file to open.")
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "Upload is larger than 80 MB.")
        parts = parse_multipart(_header(headers, "content-type"), body)
        message = " ".join((parts.fields.get("message") or "").split())
        if not message:
            raise ApiError(400, "Say what you want to change.")
        if len(message) > MAX_CHAT_MESSAGE:
            raise ApiError(400, "That message is too long. Keep it under 2,000 characters.")
        if len(parts.images) > MAX_CHAT_IMAGES:
            raise ApiError(400, "Attach up to 4 pictures at a time.")
        for image in parts.images:
            if not image.data:
                raise ApiError(400, "A picture file is empty.")
            _safe_filename(image.filename, IMAGE_SUFFIXES)
        history = _read_history(parts.fields.get("history"))
        self._require_ready()
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "Still working on the last change. Wait for it to finish.")
        try:
            revision_dir = _next_revision_dir(root)
            images = [_store_image(revision_dir, image) for image in parts.images]
            job = ChatJob(
                source=source,
                revision_dir=revision_dir,
                template_root=root / "templates",
                message=message,
                history=history,
                images=images,
            )
            outcome = self.chat_runner(job, self.settings, self.backend)
        except ApiError:
            raise
        except Exception as exc:
            return _json(500, {"status": "failed", "message": str(exc) or "That change did not work."})
        finally:
            self._lock.release()

        version = int(record.get("version", 0))
        changed = False
        if outcome.undo:
            history_stack = list(record.get("history", []))
            if history_stack:
                record.update(history_stack.pop())
                record["history"] = history_stack
                version += 1
                changed = True
            else:
                outcome.reply = "There is nothing to undo yet."
        elif outcome.changed and outcome.pdf_path:
            current = {key: record.get(key) for key in ("pdf_path", "native_path", "idml_path", "pages")}
            record["history"] = [*record.get("history", []), current][-20:]
            pages = render_pages(Path(outcome.pdf_path), revision_dir / "pages")
            record.update(
                pdf_path=outcome.pdf_path,
                native_path=outcome.native_path,
                idml_path=outcome.idml_path,
                pages=[str(path) for path in pages],
            )
            version += 1
            changed = True
        record["version"] = version
        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return _json(
            200,
            {
                "id": job_id,
                "status": "completed",
                "reply": outcome.reply,
                "changed": changed,
                "notes": outcome.notes,
                "version": version,
                "pdf": f"/brochures/{job_id}/pdf" if record.get("pdf_path") else None,
                "native": f"/brochures/{job_id}/native" if record.get("native_path") else None,
                "idml": f"/brochures/{job_id}/idml" if record.get("idml_path") else None,
                "pages": [f"/brochures/{job_id}/page/{n}" for n in range(1, len(record.get("pages", [])) + 1)],
            },
        )

    def _revise(self, job_id: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes, str]:
        if not _JOB_ID.fullmatch(job_id):
            raise ApiError(404, "Unknown brochure.")
        root = self.jobs_root / job_id
        record_path = root / "result.json"
        if not record_path.is_file():
            raise ApiError(404, "Unknown brochure.")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("status") != "completed":
            raise ApiError(422, "Create the brochure before changing it.")
        source = _revision_source(root, record)
        if source is None:
            raise ApiError(422, "This brochure has no edited file to open.")
        self._require_ready()
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "Upload is larger than 80 MB.")
        content_type = _header(headers, "content-type")
        parts = parse_multipart(content_type, body)
        title = _read_title(parts.files.get("changes"))
        for image in parts.images:
            if not image.data:
                raise ApiError(400, "A picture file is empty.")
            _safe_filename(image.filename, IMAGE_SUFFIXES)
        if title is None and not parts.images:
            raise ApiError(400, "Say a cover title or attach a picture. Color can't be changed.")
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "A brochure is already running. Wait for it to finish.")
        try:
            revision_dir = _next_revision_dir(root)
            images = [_store_image(revision_dir, image) for image in parts.images]
            outcome = apply_revision(
                source=source,
                output_dir=revision_dir,
                template_root=root / "templates",
                title=title,
                image_paths=images,
                backend=self.backend,
                control_host=self.settings.control_host,
                control_port=self.settings.control_port,
            )
        except ApiError:
            raise
        except Exception as exc:
            outcome = RunOutcome(status="failed", message=str(exc))
        finally:
            self._lock.release()
        if outcome.status == "completed":
            record["message"] = outcome.message
            record["actions"] = outcome.actions
            record["pdf_path"] = outcome.pdf_path
            record["native_path"] = outcome.native_path
            record["idml_path"] = outcome.idml_path
            record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        payload = _public_result(job_id, outcome)
        status = {"completed": 200, "needs_assistance": 422, "stopped": 409, "failed": 500}[outcome.status]
        return _json(status, payload)

    def _require_ready(self) -> None:
        if not self.settings.typesafe_api_key:
            raise ApiError(500, "Set TYPESAFE_API_KEY before uploading a brochure.")
        if self.backend == "headless":
            if not cli_installed():
                raise ApiError(503, "designcraft-cli is not installed on this server.")
            return
        if self.backend != "live":
            return
        try:
            ready = self.designcraft_ready(self.settings)
        except Exception:
            ready = False
        if not ready:
            raise ApiError(
                503,
                f"DesignCraft is not answering on {self.settings.control_host}:{self.settings.control_port}.",
            )

    def _download(self, path: str) -> tuple[int, bytes, str]:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 4 and parts[0] == "brochures" and parts[2] == "page":
            return self._page(parts[1], parts[3])
        if len(parts) != 3 or parts[0] != "brochures" or parts[2] not in {"pdf", "native", "idml"}:
            raise ApiError(404, "Unknown route.")
        job_id, kind = parts[1], parts[2]
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ApiError(404, "Unknown brochure.")
        record_path = self.jobs_root / job_id / "result.json"
        if not record_path.is_file():
            raise ApiError(404, "Unknown brochure.")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        stored = record.get({"pdf": "pdf_path", "native": "native_path", "idml": "idml_path"}[kind])
        if not stored:
            raise ApiError(404, "That file was not produced.")
        file_path = Path(stored)
        root = (self.jobs_root / job_id).resolve()
        resolved = file_path.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise ApiError(404, "That file was not produced.")
        media = "application/pdf" if kind == "pdf" else "application/octet-stream"
        return 200, resolved.read_bytes(), media


    def _page(self, job_id: str, number: str) -> tuple[int, bytes, str]:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id) or not number.isdigit():
            raise ApiError(404, "Unknown page.")
        record_path = self.jobs_root / job_id / "result.json"
        if not record_path.is_file():
            raise ApiError(404, "Unknown brochure.")
        pages = json.loads(record_path.read_text(encoding="utf-8")).get("pages", [])
        index = int(number) - 1
        if not 0 <= index < len(pages):
            raise ApiError(404, "Unknown page.")
        resolved = Path(pages[index]).resolve()
        if (self.jobs_root / job_id).resolve() not in resolved.parents or not resolved.is_file():
            raise ApiError(404, "Unknown page.")
        return 200, resolved.read_bytes(), "image/png"


def run_chat_job(job: ChatJob, settings: Settings, backend: str) -> ChatOutcome:
    """Open the brochure, work out the change from the message, apply it, and export."""
    if backend == "simulated":
        return ChatOutcome(reply="Chat edits need the headless DesignCraft CLI. Start the API with --backend headless.")
    if backend == "headless":
        from designcraft_brochure.craft.headless import HeadlessCraft

        client = HeadlessCraft(
            job.revision_dir,
            read_roots=[path.parent for path in job.images],
        )
    else:
        client = ControlClient(settings.control_host, settings.control_port)
    try:
        working = chat_edit.save_working_copy(job.source, job.revision_dir)
        chat_edit.open_document(client, working)
        snapshot = chat_edit.take_snapshot(client)
        if not settings.copy_enabled:
            return ChatOutcome(reply="Set ANTHROPIC_API_KEY so I can read your requests, then try again.")
        try:
            reply, ops = chat_edit.plan_edits(
                settings, job.message, snapshot, job.history, [path.name for path in job.images]
            )
            ops = chat_edit.validate_ops(ops, snapshot, len(job.images))
        except chat_edit.ChatError as exc:
            return ChatOutcome(reply=str(exc))
        except Exception as exc:  # the language model could not be reached
            return ChatOutcome(reply=f"I could not reach the assistant just now ({str(exc)[:120]}). Try again.")
        if not ops:
            return ChatOutcome(reply=reply)
        if any(op["op"] == "undo" for op in ops):
            return ChatOutcome(reply=reply, undo=True)
        notes = chat_edit.apply_ops(
            client, ops, snapshot, images=job.images, work_dir=job.revision_dir
        )
        pdf = job.revision_dir / "brochure.pdf"
        native = job.revision_dir / "brochure.designcraft"
        idml = job.revision_dir / "brochure-edited.idml"
        chat_edit.export_all(client, pdf, native, idml)
        return ChatOutcome(
            reply=reply,
            changed=True,
            pdf_path=str(pdf),
            native_path=str(native),
            idml_path=str(idml),
            notes=sorted(set(notes), key=notes.index),
        )
    except chat_edit.ChatError as exc:
        return ChatOutcome(reply=str(exc))
    finally:
        client.close()


def _first_pages(root: Path, outcome: RunOutcome) -> list[Path]:
    if outcome.status != "completed" or not outcome.pdf_path:
        return []
    return render_pages(Path(outcome.pdf_path), root / "previews" / "pages")


def _read_history(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    turns = []
    for item in data[-8:]:
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and isinstance(item.get("text"), str):
            turns.append({"role": item["role"], "text": item["text"][:600]})
    return turns


def run_uploaded_job(job: JobPaths, settings: Settings, backend: str) -> RunOutcome:
    progress = JobLog(job.log_dir / "progress.log")
    with TypeSafeBrain(settings.model) as brain:
        prepared = prepare_run(
            proposal_path=job.proposal_path,
            template_root=job.template_root,
            output_dir=job.output_dir,
            preview_dir=job.preview_dir,
            log_dir=job.log_dir,
            brain=brain,
            settings=settings,
            overwrite=False,
            backend=backend,
            ocr=False,
            progress=progress,
            use_uploaded_template=True,
            tour_code=job.tour_code,
        )
        if isinstance(prepared, RunOutcome):
            return prepared
        session = open_session(
            backend,
            settings.control_host,
            settings.control_port,
            work_root=job.root,
        )
        return run_loop(prepared, brain, session, settings, progress)


def designcraft_is_ready(settings: Settings) -> bool:
    client = ControlClient(settings.control_host, settings.control_port, timeout=1.5)
    try:
        client.call("engine.commands", {})
    except Exception:
        return False
    finally:
        client.close()
    return True


def parse_multipart(content_type: str, body: bytes) -> FormParts:
    if "multipart/form-data" not in content_type.lower():
        raise ApiError(400, "Send the proposal and template as multipart form data.")
    match = re.search(r"boundary=([^;]+)", content_type, flags=re.IGNORECASE)
    if match is None:
        raise ApiError(400, "The upload is missing a multipart boundary.")
    boundary = match.group(1).strip().strip('"').encode("utf-8")
    marker = b"--" + boundary
    files: dict[str, UploadedFile] = {}
    images: list[UploadedFile] = []
    fields: dict[str, str] = {}
    for part in body.split(marker):
        if not part or part in {b"--", b"--\r\n"} or part.startswith(b"--"):
            continue
        chunk = part[2:] if part.startswith(b"\r\n") else part
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        header_blob, separator, data = chunk.partition(b"\r\n\r\n")
        if not separator:
            continue
        header_text = header_blob.decode("utf-8", errors="replace")
        disposition = ""
        for line in header_text.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1].strip()
        field = _disposition_value(disposition, "name")
        filename = _disposition_value(disposition, "filename")
        if field and not filename:
            if len(data) <= MAX_FIELD_BYTES:
                fields[field] = data.decode("utf-8", errors="replace")
            continue
        if not field:
            continue
        uploaded = UploadedFile(field=field, filename=filename, data=data)
        if field == "image":
            images.append(uploaded)
            continue
        files[field] = uploaded
    return FormParts(files=files, images=images, fields=fields)


def bind_host(host: str, token: str) -> str:
    """Localhost stays loopback. A public bind requires an API token."""
    cleaned = host.strip().lower()
    if cleaned in {"127.0.0.1", "localhost", "::1"}:
        return "127.0.0.1"
    if cleaned not in {"0.0.0.0", "::"}:
        raise SafetyError("The brochure API listens on localhost or on all interfaces.")
    if not token.strip():
        raise SafetyError("Set BROCHURE_API_TOKEN before listening on a public address.")
    return "0.0.0.0"


def _bearer_ok(headers: dict[str, str], token: str) -> bool:
    offered = _header(headers, "authorization")
    prefix = "bearer "
    if offered.lower().startswith(prefix) and offered[len(prefix):].strip() == token:
        return True
    return _header(headers, "x-brochure-token") == token


def serve(
    host: str,
    port: int,
    jobs_root: Path,
    settings: Settings,
    *,
    backend: str = "live",
    runner: Runner | None = None,
    chat_runner: ChatRunner | None = None,
    designcraft_ready: ReadyCheck | None = None,
    library_root: Path | None = None,
) -> ThreadingHTTPServer:
    token = os.environ.get("BROCHURE_API_TOKEN", "").strip()
    bound_host = bind_host(host, token)
    api = BrochureAPI(
        jobs_root,
        settings,
        backend=backend,
        runner=runner,
        chat_runner=chat_runner,
        designcraft_ready=designcraft_ready,
        library_root=library_root,
        token=token,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._reply()

        def do_POST(self) -> None:  # noqa: N802
            self._reply()

        def do_DELETE(self) -> None:  # noqa: N802
            self._reply()

        def _reply(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_BODY_BYTES:
                status, payload, media = _json(413, {"status": "failed", "message": "Upload is larger than 80 MB."})
            else:
                body = self.rfile.read(length) if length else b""
                status, payload, media = api.handle(self.command, self.path, dict(self.headers), body)
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"{self.address_string()} {format % args}", flush=True)

    return ThreadingHTTPServer((bound_host, port), Handler)


def _prepare_job(jobs_root: Path, job_id: str, proposal: UploadedFile, template: UploadedFile) -> JobPaths:
    root = jobs_root / job_id
    template_root = root / "templates"
    output_dir = root / "output"
    preview_dir = root / "previews"
    log_dir = root / "logs"
    for directory in (template_root, output_dir, preview_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)
    proposal_name = _safe_filename(proposal.filename, PROPOSAL_SUFFIXES)
    template_name = _safe_filename(template.filename, TEMPLATE_SUFFIXES)
    proposal_path = root / "input" / proposal_name
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_bytes(proposal.data)
    (template_root / template_name).write_bytes(template.data)
    return JobPaths(
        job_id=job_id,
        root=root,
        proposal_path=proposal_path,
        template_root=template_root,
        output_dir=output_dir,
        preview_dir=preview_dir,
        log_dir=log_dir,
    )


def _public_template(meta: dict) -> dict:
    return {key: meta[key] for key in ("id", "name", "filename", "suffix", "size", "added")}


def _public_result(job_id: str, outcome: RunOutcome) -> dict:
    return {
        "id": job_id,
        "status": outcome.status,
        "message": outcome.message,
        "actions": outcome.actions,
        "pdf": f"/brochures/{job_id}/pdf" if outcome.pdf_path else None,
        "native": f"/brochures/{job_id}/native" if outcome.native_path else None,
        "idml": f"/brochures/{job_id}/idml" if outcome.idml_path else None,
    }


def _write_result(root: Path, payload: dict, outcome: RunOutcome, pages: list[Path] | None = None) -> None:
    stored = dict(payload)
    stored["pages"] = [str(path) for path in pages or []]
    stored["history"] = []
    stored["pdf_path"] = outcome.pdf_path
    stored["native_path"] = outcome.native_path
    stored["idml_path"] = outcome.idml_path
    (root / "result.json").write_text(json.dumps(stored, indent=2), encoding="utf-8")


def _revision_source(root: Path, record: dict) -> Path | None:
    for key in ("idml_path", "native_path"):
        stored = record.get(key)
        if not isinstance(stored, str) or not stored:
            continue
        path = Path(stored)
        if not path.is_file():
            continue
        resolved = path.resolve()
        if root.resolve() not in resolved.parents:
            continue
        if resolved.suffix.lower() not in {".idml", ".designcraft"}:
            continue
        return resolved
    return None


def _read_tour_code(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    code = normalize_tour_code(value)
    if code is None:
        raise ApiError(400, "The tour code can only have letters, numbers and dashes, 2 to 24 characters.")
    return code


def _read_title(uploaded: UploadedFile | None) -> str | None:
    if uploaded is None or not uploaded.data:
        raise ApiError(400, "Missing the changes.")
    try:
        payload = json.loads(uploaded.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, "The changes could not be read.") from None
    if not isinstance(payload, dict):
        raise ApiError(400, "The changes could not be read.")
    if payload.get("color") or payload.get("colour"):
        raise ApiError(400, "Color can't be changed.")
    title = payload.get("title")
    if title is None or title == "":
        return None
    if not isinstance(title, str):
        raise ApiError(400, "The title has to be one line of text.")
    cleaned = " ".join(title.split())
    if not cleaned:
        return None
    if len(cleaned) > 120:
        raise ApiError(400, "The title has to fit on one line.")
    if is_suspicious(cleaned):
        raise ApiError(400, "That title can't be used.")
    return cleaned


def _store_image(revision_dir: Path, image: UploadedFile) -> Path:
    name = _safe_filename(image.filename, IMAGE_SUFFIXES)
    folder = revision_dir / "images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(image.data)
    return path


def _next_revision_dir(root: Path) -> Path:
    base = root / "revisions"
    base.mkdir(parents=True, exist_ok=True)
    numbers = [int(path.name) for path in base.iterdir() if path.is_dir() and path.name.isdigit()]
    folder = base / str((max(numbers) if numbers else 0) + 1)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _required_file(files: dict[str, UploadedFile], field: str, suffixes: set[str]) -> UploadedFile:
    uploaded = files.get(field)
    if uploaded is None or not uploaded.data:
        raise ApiError(400, f"Missing file field '{field}'.")
    _safe_filename(uploaded.filename, suffixes)
    return uploaded


def _safe_filename(filename: str, suffixes: set[str]) -> str:
    name = Path(filename.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in suffixes:
        allowed = ", ".join(sorted(suffixes))
        raise ApiError(400, f"'{name or filename}' must be one of: {allowed}.")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip(".-") or "upload"
    return stem + suffix


def _disposition_value(disposition: str, key: str) -> str:
    match = re.search(rf"{key}=(?:\"([^\"]*)\"|([^;\s]+))", disposition, flags=re.IGNORECASE)
    if match is None:
        return ""
    return match.group(1) if match.group(1) is not None else match.group(2)


def _header(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _json(status: int, payload: dict) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json"
