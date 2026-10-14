from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"


def test_sources_do_not_call_indesign_scripting_or_a_shell():
    banned = (
        "extendscript",
        "doscript",
        "app.activedocument",
        ".jsx",
        "subprocess",
        "os.system",
        "powershell",
    )
    offenders = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            if word in text:
                offenders.append(f"{path.name}: {word}")
    assert offenders == []
