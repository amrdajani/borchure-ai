"""Turn an InDesign .indd file into IDML with the indd-utils `indd` command.

DesignCraft cannot read .indd. The converter reads a copy and writes an
.idml file beside it. The original template is never the destination.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from designcraft_brochure.safety import SafetyError, sha256_file


def find_indd_binary() -> Path | None:
    configured = os.getenv("INDD_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        raise SafetyError(f"INDD_BIN is not an executable file: {configured}")
    for candidate in _candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    found = shutil.which("indd")
    if found:
        return Path(found).resolve()
    return None


def convert_indd_to_idml(source: Path, destination: Path) -> None:
    binary = find_indd_binary()
    if binary is None:
        raise SafetyError(
            "indd-utils is not installed, so this .indd file cannot be converted. "
            "Build https://github.com/storytold/indd-utils and set INDD_BIN to the indd executable."
        )
    if source.suffix.lower() != ".indd":
        raise SafetyError("Only an .indd file can be converted to IDML.")
    if destination.suffix.lower() != ".idml":
        raise SafetyError("The conversion destination must be an .idml file.")
    if destination.resolve() == source.resolve():
        raise SafetyError("Refusing to convert a file onto itself.")
    before = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(binary), "convert", str(source), str(destination)],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    after = sha256_file(source)
    if before != after:
        raise SafetyError("The .indd file changed during conversion. Stopping.")
    detail = (completed.stderr or completed.stdout or "").strip()
    if completed.returncode != 0 or not destination.is_file():
        message = f"Could not convert {source.name} to IDML."
        if detail:
            message = f"{message} {detail[:500]}"
        raise SafetyError(message)


def _candidates() -> list[Path]:
    project = Path(__file__).resolve().parents[3]
    return [
        project / ".tools" / "indd-utils" / "target" / "release" / "indd",
        Path.cwd() / ".tools" / "indd-utils" / "target" / "release" / "indd",
    ]
