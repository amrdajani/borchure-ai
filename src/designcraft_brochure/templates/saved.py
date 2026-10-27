"""Templates people add through the API, kept on disk to use again.

Each template is a folder: the file as uploaded, and a small meta.json with the
name people see. Brochure runs read a copy, so a saved template is never edited.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from designcraft_brochure.safety import is_suspicious

TEMPLATE_ID = re.compile(r"[0-9a-f]{32}")
MAX_TEMPLATES = 50
MAX_NAME = 80


class LibraryError(Exception):
    """The request cannot be done. The message is safe to show to the person."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class TemplateLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list(self) -> list[dict]:
        found = []
        if self.root.is_dir():
            for folder in self.root.iterdir():
                meta = self._meta(folder)
                if meta is not None:
                    found.append(meta)
        return sorted(found, key=lambda item: item["added"], reverse=True)

    def add(self, name: str | None, filename: str, data: bytes) -> tuple[dict, bool]:
        """Save a template. The second value is True when the same file was already saved."""
        digest = hashlib.sha256(data).hexdigest()
        current = self.list()
        for item in current:
            if item["sha256"] == digest:
                return item, True
        if len(current) >= MAX_TEMPLATES:
            raise LibraryError(409, f"The library is full ({MAX_TEMPLATES} templates). Delete one first.")
        suffix = Path(filename).suffix.lower()
        meta = {
            "id": uuid.uuid4().hex,
            "name": clean_name(name) or clean_name(Path(filename).stem) or "Template",
            "filename": filename,
            "suffix": suffix,
            "size": len(data),
            "sha256": digest,
            "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        folder = self.root / meta["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(data)
        (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta, False

    def rename(self, template_id: str, name: str | None) -> dict:
        folder = self._folder(template_id)
        meta = self._meta(folder)
        cleaned = clean_name(name)
        if meta is None:
            raise LibraryError(404, "That template is not in the library.")
        if not cleaned:
            raise LibraryError(400, "Give the template a name.")
        meta["name"] = cleaned
        (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def delete(self, template_id: str) -> None:
        folder = self._folder(template_id)
        if self._meta(folder) is None:
            raise LibraryError(404, "That template is not in the library.")
        shutil.rmtree(folder)

    def read(self, template_id: str) -> tuple[dict, bytes]:
        folder = self._folder(template_id)
        meta = self._meta(folder)
        if meta is None:
            raise LibraryError(404, "That template is not in the library any more. Pick another or add it again.")
        path = folder / meta["filename"]
        if not path.is_file():
            raise LibraryError(404, "That template's file is missing. Add it again.")
        return meta, path.read_bytes()

    def _folder(self, template_id: str) -> Path:
        if not TEMPLATE_ID.fullmatch(template_id or ""):
            raise LibraryError(404, "That template is not in the library.")
        return self.root / template_id

    def _meta(self, folder: Path) -> dict | None:
        path = folder / "meta.json"
        if not path.is_file():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        needed = ("id", "name", "filename", "suffix", "size", "sha256", "added")
        if not isinstance(meta, dict) or any(key not in meta for key in needed):
            return None
        if meta["id"] != folder.name or Path(str(meta["filename"])).name != meta["filename"]:
            return None
        return meta


def clean_name(value: str | None) -> str:
    """One line of plain text, short, with nothing that reads like an instruction."""
    text = " ".join((value or "").split())
    if len(text) > MAX_NAME:
        text = text[:MAX_NAME].rstrip()
    if text and is_suspicious(text):
        raise LibraryError(400, "That name can't be used.")
    return text
