"""Discover brochure templates without modifying them.

DesignCraft opens .idml and its own .designcraft files. An .indd file is
offered only when indd-utils can convert a copy to IDML first.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from designcraft_brochure.models import TemplateInfo
from designcraft_brochure.safety import SafetyError, assert_not_template_target, sha256_file
from designcraft_brochure.templates.indd import find_indd_binary

_OPENABLE = {".idml", ".designcraft"}
_LISTED = _OPENABLE | {".indd"}


def discover_templates(root: Path) -> list[TemplateInfo]:
    if not root.is_dir():
        raise SafetyError(f"Template folder not found: {root}")
    found: list[TemplateInfo] = []
    used_ids: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _LISTED:
            continue
        resolved = path.resolve()
        if not _is_relative_to(resolved, root.resolve()):
            continue
        category = _category(path, root)
        info = _describe(path, category)
        info.id = _unique_id(_slug(path.relative_to(root)), used_ids)
        found.append(info)
    return found


def openable_templates(templates: list[TemplateInfo]) -> list[TemplateInfo]:
    return [item for item in templates if item.openable]


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


def read_idml_stories(path: Path) -> tuple[list[str], int, int]:
    """Return story texts, page count, and image count from an IDML package."""
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        stories = []
        for name in sorted(names):
            if not (name.startswith("Stories/") and name.endswith(".xml")):
                continue
            root = ElementTree.fromstring(package.read(name))
            chunks = [
                element.text.strip()
                for element in root.iter()
                if element.tag.endswith("Content") and element.text and element.text.strip()
            ]
            stories.append("".join(chunks))
        pages = _page_count(package, names)
        images = _image_count(package, names)
    return stories, pages, images


def write_idml_stories(source: Path, destination: Path, stories: list[str]) -> None:
    """Write an IDML copy whose story Content nodes carry the edited text."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as package, zipfile.ZipFile(destination, "w") as out:
        story_files = [
            name
            for name in sorted(package.namelist())
            if name.startswith("Stories/") and name.endswith(".xml")
        ]
        for name in package.namelist():
            payload = package.read(name)
            if name in story_files:
                index = story_files.index(name)
                text = stories[index] if index < len(stories) else ""
                payload = _replace_story_text(payload, text)
            out.writestr(name, payload)


def read_snapshot(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != "designcraft-brochure-snapshot":
        raise SafetyError("This .designcraft file is not a snapshot this simulated session can open.")
    return payload


def _replace_story_text(payload: bytes, text: str) -> bytes:
    root = ElementTree.fromstring(payload)
    contents = [element for element in root.iter() if element.tag.endswith("Content")]
    if contents:
        contents[0].text = text
        for extra in contents[1:]:
            extra.text = ""
    else:
        ElementTree.SubElement(root, "Content").text = text
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _describe(path: Path, category: str) -> TemplateInfo:
    suffix = path.suffix.lower()
    if suffix == ".indd":
        converter = find_indd_binary()
        if converter is None:
            notes = "indd-utils is not installed, so this .indd file cannot be converted."
        else:
            notes = "A copy is converted to IDML with indd-utils. The original .indd file is not modified."
        return TemplateInfo(
            id="",
            path=str(path),
            category=category,
            suffix=suffix,
            notes=notes,
            openable=converter is not None,
        )
    if suffix == ".idml":
        try:
            stories, pages, images = read_idml_stories(path)
            preview = " ".join(part for part in stories if part)[:500]
            notes = "IDML preview only. DesignCraft opens a copy of this file."
        except (zipfile.BadZipFile, OSError, ElementTree.ParseError) as exc:
            return TemplateInfo(
                id="",
                path=str(path),
                category=category,
                suffix=suffix,
                notes=f"IDML metadata could not be read ({exc.__class__.__name__}).",
                openable=True,
            )
        return TemplateInfo(
            id="",
            path=str(path),
            category=category,
            suffix=suffix,
            page_count=pages,
            text_preview=preview,
            image_frame_count=images,
            notes=notes,
            openable=True,
        )
    return TemplateInfo(
        id="",
        path=str(path),
        category=category,
        suffix=suffix,
        notes="DesignCraft native file. A copy is opened; the template itself is not written.",
        openable=True,
    )


def _page_count(package: zipfile.ZipFile, names: list[str]) -> int:
    pages = 0
    if "designmap.xml" in names:
        root = ElementTree.fromstring(package.read("designmap.xml"))
        pages = sum(1 for element in root.iter() if element.tag.endswith("Page"))
    if pages:
        return pages
    spreads = [name for name in names if name.startswith("Spreads/") and name.endswith(".xml")]
    return len(spreads) or 1


def _image_count(package: zipfile.ZipFile, names: list[str]) -> int:
    total = 0
    for name in names:
        if not (name.startswith("Spreads/") and name.endswith(".xml")):
            continue
        root = ElementTree.fromstring(package.read(name))
        total += sum(1 for element in root.iter() if element.tag.endswith("Image"))
    return total


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
