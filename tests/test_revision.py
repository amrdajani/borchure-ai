import json
from pathlib import Path

from designcraft_brochure.api import BrochureAPI
from designcraft_brochure.config import Settings
from designcraft_brochure.templates.library import read_idml_stories
from tests.helpers import write_idml
from tests.test_api import _multipart


def _job(tmp_path: Path) -> tuple[BrochureAPI, str]:
    job_id = "a" * 32
    root = tmp_path / "jobs" / job_id
    output = root / "output"
    output.mkdir(parents=True)
    idml = output / "brochure-edited.idml"
    write_idml(idml, "Old harbor title")
    pdf = output / "brochure.pdf"
    native = output / "brochure.designcraft"
    pdf.write_bytes(b"%PDF-1.4 old")
    native.write_bytes(b"{}")
    record = {
        "id": job_id,
        "status": "completed",
        "message": "Created.",
        "actions": 3,
        "pdf": f"/brochures/{job_id}/pdf",
        "native": f"/brochures/{job_id}/native",
        "idml": f"/brochures/{job_id}/idml",
        "pdf_path": str(pdf),
        "native_path": str(native),
        "idml_path": str(idml),
    }
    (root / "result.json").write_text(json.dumps(record), encoding="utf-8")
    api = BrochureAPI(
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        backend="simulated",
    )
    return api, job_id


def _changes(title: str | None, images: dict[str, tuple[str, bytes]] | None = None) -> tuple[str, bytes]:
    payload = {} if title is None else {"title": title}
    fields = {"changes": ("changes.json", json.dumps(payload).encode())}
    if images:
        fields.update(images)
    return _multipart("bound", fields)


def test_revision_replaces_the_cover_title(tmp_path):
    api, job_id = _job(tmp_path)
    content_type, body = _changes("Tuscany by Bicycle")
    status, payload, _media = api.handle(
        "POST",
        f"/brochures/{job_id}/revisions",
        {"Content-Type": content_type},
        body,
    )
    result = json.loads(payload)
    assert status == 200
    assert result["status"] == "completed"
    assert "Tuscany by Bicycle" in result["message"]
    _idml_status, idml_body, _idml_media = api.handle("GET", result["idml"], {}, b"")
    edited = tmp_path / "edited.idml"
    edited.write_bytes(idml_body)
    stories, _pages, _images = read_idml_stories(edited)
    assert stories == ["Tuscany by Bicycle"]
    pdf_status, pdf_body, pdf_media = api.handle("GET", result["pdf"], {}, b"")
    assert pdf_status == 200 and pdf_media == "application/pdf" and pdf_body.startswith(b"%PDF")


def test_revision_places_a_picture(tmp_path):
    api, job_id = _job(tmp_path)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0P"
        b"\x0f\x00\x04\x85\x01\x80\x84\xa9\x8c!\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    content_type, body = _changes(None, {"image": ("cover.png", png)})
    status, payload, _media = api.handle(
        "POST",
        f"/brochures/{job_id}/revisions",
        {"Content-Type": content_type},
        body,
    )
    result = json.loads(payload)
    assert status == 200
    assert "Placed 1 picture" in result["message"]


def test_revision_rejects_a_color_request(tmp_path):
    api, job_id = _job(tmp_path)
    content_type, body = _multipart(
        "bound",
        {"changes": ("changes.json", json.dumps({"color": "#112233"}).encode())},
    )
    status, payload, _media = api.handle(
        "POST",
        f"/brochures/{job_id}/revisions",
        {"Content-Type": content_type},
        body,
    )
    assert status == 400
    assert "Color" in json.loads(payload)["message"]


def test_revision_requires_a_change(tmp_path):
    api, job_id = _job(tmp_path)
    content_type, body = _changes(None)
    status, payload, _media = api.handle(
        "POST",
        f"/brochures/{job_id}/revisions",
        {"Content-Type": content_type},
        body,
    )
    assert status == 400
    assert json.loads(payload)["message"]


def test_revision_unknown_brochure(tmp_path):
    api, _job_id = _job(tmp_path)
    content_type, body = _changes("Hello")
    status, _payload, _media = api.handle(
        "POST",
        "/brochures/" + ("b" * 32) + "/revisions",
        {"Content-Type": content_type},
        body,
    )
    assert status == 404
