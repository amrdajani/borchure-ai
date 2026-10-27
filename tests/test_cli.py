from pathlib import Path

from designcraft_brochure.cli import main
from designcraft_brochure.proposal.extract import extract_proposal
from designcraft_brochure.proposal.reader import read_source
from tests.helpers import write_idml


FIXTURE = Path(__file__).parent / "fixtures" / "proposal.txt"


def test_doctor_lists_and_inspects_without_jev(tmp_path, capsys):
    assert main(["doctor"]) == 0
    root = tmp_path / "templates"
    write_idml(root / "travel" / "harbor.idml")
    indd = root / "corporate" / "old.indd"
    indd.parent.mkdir(parents=True)
    indd.write_bytes(b"binary")
    assert main(["list-templates", "--templates", str(root)]) == 0
    listed = capsys.readouterr().out
    assert "travel-harbor" in listed
    assert "corporate-old" in listed
    assert main(["inspect", str(FIXTURE)]) == 0
    inspected = capsys.readouterr().out
    assert "480" in inspected
    assert "raw_text" not in inspected


def test_injection_line_is_removed_before_extraction(tmp_path):
    source = read_source(FIXTURE, tmp_path / "images")
    proposal = extract_proposal(source, brain=None)
    assert proposal.injection_flagged
    assert "Ignore previous" not in proposal.raw_text
    assert "$480" in proposal.raw_text or any("480" in price for price in proposal.prices)
