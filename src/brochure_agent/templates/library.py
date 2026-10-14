"""Discover local InDesign templates without modifying them."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from brochure_agent.models import TemplateInfo
from brochure_agent.safety import SafetyError, assert_not_template_target, sha256_file

_SUFFIXES = {".indd", ".idml"}


def discover_templates(root: Path) -> list[TemplateInfo]:
    if not root.is_dir():
        raise SafetyError(f"Template folder not found: {root}")
    found: list[TemplateInfo] = []
    used_ids: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        resolved = path.resolve()
        if not _is_relative_to(resolved, root.resolve()):
            continue
        category = _category(path, root)
        info = _describe(path, category)
        info.id = _unique_id(_slug(path.relative_to(root)), used_ids)
        found.append(info)
    return found


def copy_working_file(template: TemplateInfo, output_dir: Path, template_root: Path, *, overwrite: bool) -> Path:
    source = Path(template.path)
    destination = output_dir / "working" / f"{source.stem}-working{source.suffix}"
    assert_not_template_target(source, destination, template_root)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Working copy already exists: {destination}")
    before = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    after = sha256_file(source)
    if before != after:
        raise SafetyError("Template file changed while copying. Stopping.")
    return destination


def _describe(path: Path, category: str) -> TemplateInfo:
    if path.suffix.lower() == ".idml":
        pages, preview, images, notes = _read_idml(path)
        return TemplateInfo(
            id="",
            path=str(path),
            category=category,
            suffix=path.suffix.lower(),
            page_count=pages,
            text_preview=preview,
            image_frame_count=images,
            notes=notes,
        )
    return TemplateInfo(
        id="",
        path=str(path),
        category=category,
        suffix=path.suffix.lower(),
        notes="Binary InDesign document. Its layout is judged from the live window, not from the file.",
    )


def _read_idml(path: Path) -> tuple[int | None, str, int | None, str]:
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            preview = _story_text(package, names)
            pages = _page_count(package, names)
            images = _image_count(package, names)
    except (zipfile.BadZipFile, OSError, ElementTree.ParseError) as exc:
        return None, "", None, f"IDML metadata could not be read ({exc.__class__.__name__})."
    return pages, preview[:500], images, "IDML metadata is a preview only. Editing happens in InDesign."


def _story_text(package: zipfile.ZipFile, names: list[str]) -> str:
    chunks: list[str] = []
    for name in names:
        if not (name.startswith("Stories/") and name.endswith(".xml")):
            continue
        root = ElementTree.fromstring(package.read(name))
        for element in root.iter():
            if element.tag.endswith("Content") and element.text:
                chunks.append(element.text.strip())
    return " ".join(chunk for chunk in chunks if chunk)


def _page_count(package: zipfile.ZipFile, names: list[str]) -> int | None:
    pages = 0
    if "designmap.xml" in names:
        root = ElementTree.fromstring(package.read("designmap.xml"))
        pages = sum(1 for element in root.iter() if element.tag.endswith("Page"))
    if pages:
        return pages
    spreads = [name for name in names if name.startswith("Spreads/") and name.endswith(".xml")]
    return len(spreads) or None


def _image_count(package: zipfile.ZipFile, names: list[str]) -> int | None:
    total = 0
    seen = False
    for name in names:
        if not (name.startswith("Spreads/") and name.endswith(".xml")):
            continue
        seen = True
        root = ElementTree.fromstring(package.read(name))
        total += sum(1 for element in root.iter() if element.tag.endswith("Image"))
    return total if seen else None


def _category(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if len(relative.parts) > 1:
        return relative.parts[0]
    return "uncategorized"


def _slug(relative: Path) -> str:
    stem = str(relative.with_suffix(""))
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")
    return (slug or "template")[:80]


def _unique_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    index = 2
    while f"{base}-{index}" in used:
        index += 1
    used.add(f"{base}-{index}")
    return f"{base}-{index}"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
