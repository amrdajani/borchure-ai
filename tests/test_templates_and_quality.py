from pathlib import Path

from brochure_agent.models import BrochurePlan, CopyBlock
from brochure_agent.quality.pdf import inspect_pdf
from brochure_agent.safety import SafetyError, assert_not_template_target, sha256_file, unsupported_facts
from brochure_agent.templates.library import copy_working_file, discover_templates
from tests.helpers import write_idml

from brochure_agent.models import TemplateInfo


def test_idml_discovery_and_copy_leaves_the_original(tmp_path):
    root = tmp_path / "templates"
    source = root / "travel" / "harbor.idml"
    write_idml(source, "Harbor headline placeholder")
    found = discover_templates(root)
    assert len(found) == 1
    assert found[0].id == "travel-harbor"
    assert found[0].page_count == 1
    assert found[0].image_frame_count == 1
    assert "Harbor headline" in found[0].text_preview
    before = sha256_file(source)
    working = copy_working_file(found[0], tmp_path / "output", root, overwrite=False)
    assert working.name == "harbor-working.idml"
    assert sha256_file(source) == before
    assert working.read_bytes() == source.read_bytes()
    try:
        copy_working_file(found[0], root, root, overwrite=True)
    except SafetyError:
        pass
    else:
        raise AssertionError("copy into the template folder should fail")


def test_template_target_guard(tmp_path):
    template = tmp_path / "templates" / "file.idml"
    template.parent.mkdir()
    template.write_bytes(b"template")
    try:
        assert_not_template_target(template, template, template.parent)
    except SafetyError as exc:
        assert "itself" in str(exc)
    else:
        raise AssertionError("expected a safety error")


def test_pdf_quality_checks_text_and_blank_pages(tmp_path):
    import fitz

    good = tmp_path / "good.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Harbor Day Trips")
    document.save(good)
    document.close()
    plan = BrochurePlan(
        template_id="travel-harbor",
        blocks=[CopyBlock(id="headline", role="headline", text="Harbor Day Trips", source_backed=True)],
    )
    report = inspect_pdf(good, plan, tmp_path / "previews")
    assert report.status == "ok"
    assert report.page_count == 1
    assert report.preview_paths

    blank = tmp_path / "blank.pdf"
    empty = fitz.open()
    empty.new_page()
    empty.save(blank)
    empty.close()
    blank_report = inspect_pdf(blank, plan, tmp_path / "blank-previews")
    assert blank_report.status == "issues"
    assert blank_report.blank_pages == [1]

    missing = inspect_pdf(tmp_path / "missing.pdf", plan, tmp_path / "none")
    assert missing.status == "missing"


def test_unsupported_numbers_are_rejected():
    assert "9999" in unsupported_facts("Pay $9999 today.", "The package price is $480 per group.")
    assert unsupported_facts("The package price is $480 per group.", "The package price is $480 per group.") == []


def test_template_info_round_trip():
    info = TemplateInfo(id="travel-harbor", path="x.idml", category="travel", suffix=".idml")
    assert info.category == "travel"
