from pathlib import Path

from brochure_agent.cli import main
from tests.helpers import write_idml


FIXTURE = Path(__file__).parent / "fixtures" / "proposal.txt"


def test_doctor_and_offline_commands(tmp_path, capsys):
    assert main(["doctor"]) == 0
    root = tmp_path / "templates"
    write_idml(root / "travel" / "harbor.idml")
    assert main(["list-templates", "--templates", str(root)]) == 0
    listed = capsys.readouterr().out
    assert "travel-harbor" in listed
    assert main(["inspect", str(FIXTURE)]) == 0
    inspected = capsys.readouterr().out
    assert "480" in inspected
    assert "raw_text" not in inspected
