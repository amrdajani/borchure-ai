"""Action journal and resume checkpoints.

The journal records what was attempted. It does not record proposal sentences
or text that was sent to a story.
"""

from __future__ import annotations

import json
from pathlib import Path

from designcraft_brochure.models import Checkpoint


def append_journal(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def public_history_item(action_id: str, name: str, ok: bool | None, detail: str) -> dict:
    return {
        "id": action_id,
        "action": name,
        "ok": ok,
        "detail": detail[:180],
    }


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def load_checkpoint(path: Path) -> Checkpoint:
    return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
