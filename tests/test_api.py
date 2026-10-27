import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from designcraft_brochure.agent.loop import Progress
from designcraft_brochure.agent.prepare import prepare_run
from designcraft_brochure.api import BrochureAPI, serve
from designcraft_brochure.config import Settings
from designcraft_brochure.models import RunOutcome
from tests.helpers import CallbackBrain, answering_brain, write_idml


def _multipart(boundary: str, fields: dict[str, tuple[str, bytes]]) -> tuple[str, bytes]:
    chunks: list[bytes] = []
    for name, (filename, data) in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def _outcome(tmp_path: Path) -> RunOutcome:
    pdf = tmp_path / "output" / "brochure.pdf"
    native = tmp_path / "output" / "working" / "brochure.designcraft"
    idml = tmp_path / "output" / "working" / "brochure-edited.idml"
    native.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 test")
    native.write_bytes(b"native")
    idml.write_bytes(b"idml")
    return RunOutcome(
        status="completed",
        message="Brochure exported.",
        pdf_path=str(pdf),
        native_path=str(native),
        idml_path=str(idml),
        actions=4,
    )


def test_upload_returns_download_links(tmp_path):
    jobs = tmp_path / "jobs"
    created: dict[str, Path] = {}

    def runner(job, _settings, backend):
        assert backend == "live"
        assert job.proposal_path.is_file()
        assert any(job.template_root.iterdir())
        created["root"] = job.root
        return _outcome(job.root)

    api = BrochureAPI(
        jobs,
        Settings(typesafe_api_key="test-key"),
        runner=runner,
        designcraft_ready=lambda _settings: True,
    )
    content_type, body = _multipart(
        "bound",
        {"proposal": ("tour.pdf", b"proposal"), "template": ("FPSEM425.indd", b"indd")},
    )
    status, payload, media = api.handle("POST", "/brochures", {"Content-Type": content_type}, body)
    result = json.loads(payload)
    assert status == 200
    assert media == "application/json"
    assert result["status"] == "completed"
    assert result["pdf"].endswith("/pdf")
    pdf_status, pdf_body, pdf_media = api.handle("GET", result["pdf"], {}, b"")
    native_status, native_body, _native_media = api.handle("GET", result["native"], {}, b"")
    assert pdf_status == 200 and pdf_body == b"%PDF-1.4 test" and pdf_media == "application/pdf"
    idml_status, idml_body, _idml_media = api.handle("GET", result["idml"], {}, b"")
    assert native_status == 200 and native_body == b"native"
    assert idml_status == 200 and idml_body == b"idml"
    assert (created["root"] / "templates" / "FPSEM425.indd").read_bytes() == b"indd"


def test_uploaded_file_is_used_without_a_template_vote(tmp_path):
    root = tmp_path / "templates"
    write_idml(root / "brochure.idml")
    proposal = Path(__file__).parent / "fixtures" / "proposal.txt"

    def judge(state, questions):
        assert "template" not in questions
        return answering_brain(state, questions)

    prepared = prepare_run(
        proposal_path=proposal,
        template_root=root,
        output_dir=tmp_path / "output",
        preview_dir=tmp_path / "previews",
        log_dir=tmp_path / "logs",
        brain=CallbackBrain(judge),
        settings=Settings(),
        overwrite=False,
        backend="simulated",
        ocr=False,
        progress=Progress(),
        use_uploaded_template=True,
    )
    assert not isinstance(prepared, RunOutcome)
    assert prepared.working_copy.is_file()


def test_upload_rejects_a_template_designcraft_cannot_open(tmp_path):
    api = BrochureAPI(
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        runner=lambda *_args: (_ for _ in ()).throw(AssertionError("runner should not start")),
        designcraft_ready=lambda _settings: True,
    )
    content_type, body = _multipart(
        "bound",
        {"proposal": ("tour.pdf", b"proposal"), "template": ("notes.docx", b"nope")},
    )
    status, payload, _media = api.handle("POST", "/brochures", {"Content-Type": content_type}, body)
    assert status == 400
    assert "template" in json.loads(payload)["message"].lower() or ".docx" in json.loads(payload)["message"]


def test_second_upload_waits(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def runner(job, _settings, _backend):
        started.set()
        release.wait(2)
        return _outcome(job.root)

    api = BrochureAPI(
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        runner=runner,
        designcraft_ready=lambda _settings: True,
    )
    content_type, body = _multipart(
        "bound",
        {"proposal": ("tour.txt", b"hello"), "template": ("layout.idml", b"idml")},
    )
    first: dict = {}

    def run_first():
        status, payload, _media = api.handle("POST", "/brochures", {"Content-Type": content_type}, body)
        first["status"] = status
        first["body"] = payload

    thread = threading.Thread(target=run_first)
    thread.start()
    assert started.wait(2)
    status, payload, _media = api.handle("POST", "/brochures", {"Content-Type": content_type}, body)
    release.set()
    thread.join(2)
    assert status == 409
    assert json.loads(payload)["message"]
    assert first["status"] == 200


def test_http_server_health(tmp_path):
    server = serve(
        "127.0.0.1",
        0,
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        runner=lambda *_args: (_ for _ in ()).throw(AssertionError("unused")),
        designcraft_ready=lambda _settings: True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["designcraft"] is True
    finally:
        server.shutdown()
        server.server_close()


def _with_text_field(content_type: str, body: bytes, name: str, value: str) -> bytes:
    boundary = content_type.split("boundary=")[1]
    closing = f"--{boundary}--\r\n".encode()
    text = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    return body[: -len(closing)] + text + closing


def test_a_typed_tour_code_reaches_the_run_and_a_bad_one_is_refused(tmp_path):
    seen: list[str | None] = []

    def runner(job, _settings, _backend):
        seen.append(job.tour_code)
        return _outcome(job.root)

    api = BrochureAPI(
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        runner=runner,
        designcraft_ready=lambda _settings: True,
    )
    content_type, body = _multipart(
        "bound", {"proposal": ("tour.pdf", b"proposal"), "template": ("FPSEM425.indd", b"indd")}
    )
    status, _payload, _media = api.handle(
        "POST", "/brochures", {"Content-Type": content_type}, _with_text_field(content_type, body, "tour_code", " hlmrs426 ")
    )
    assert status == 200 and seen == ["HLMRS426"]

    status, _payload, _media = api.handle("POST", "/brochures", {"Content-Type": content_type}, body)
    assert status == 200 and seen[-1] is None  # no field, no code

    status, payload, _media = api.handle(
        "POST", "/brochures", {"Content-Type": content_type}, _with_text_field(content_type, body, "tour_code", "<b>bad</b>")
    )
    assert status == 400 and "tour code" in json.loads(payload)["message"].lower()
    assert len(seen) == 2  # the refused upload never started a run
