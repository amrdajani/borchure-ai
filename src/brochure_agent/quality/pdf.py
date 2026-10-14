"""PDF checks after InDesign export.

A closed export dialog is not success. The file has to open, and each page
is checked for blank content, clipped text, missing phrases, and page size.
"""

from __future__ import annotations

from pathlib import Path

from brochure_agent.models import BrochurePlan, QualityReport


def inspect_pdf(path: Path, plan: BrochurePlan | None, preview_dir: Path) -> QualityReport:
    report = QualityReport(path=str(path))
    if not path.is_file():
        report.issues.append("PDF file is missing.")
        return report
    report.exists = True
    try:
        import fitz
    except ImportError:
        report.issues.append("PyMuPDF is not installed, so the PDF was not opened.")
        report.status = "unverified"
        return report
    try:
        document = fitz.open(path)
    except Exception as exc:
        report.issues.append(f"PDF could not be opened ({exc.__class__.__name__}).")
        return report
    report.opened = True
    preview_dir.mkdir(parents=True, exist_ok=True)
    phrases = _phrases(plan)
    found_text = False
    found_image = False
    page_text: list[str] = []
    try:
        report.page_count = document.page_count
        for index, page in enumerate(document, start=1):
            rect = page.rect
            report.page_sizes.append((round(rect.width, 2), round(rect.height, 2)))
            text = page.get_text() or ""
            page_text.append(text)
            if text.strip():
                found_text = True
            if page.get_images():
                found_image = True
            if _blank(page, text):
                report.blank_pages.append(index)
            if _clipped(page):
                report.clipped_pages.append(index)
            preview = preview_dir / f"{path.stem}-page-{index}.png"
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
            pixmap.save(str(preview))
            report.preview_paths.append(str(preview))
        combined = "\n".join(page_text)
        report.missing_phrases = [phrase for phrase in phrases if not _contains(combined, phrase)]
    finally:
        document.close()
    if plan and plan.image_paths and not found_image:
        report.pages_missing_images = True
        report.issues.append("The plan includes images, and no PDF page contains an image.")
    if report.blank_pages:
        report.issues.append(f"Blank pages: {report.blank_pages}.")
    if report.clipped_pages:
        report.issues.append(f"Content extends outside pages: {report.clipped_pages}.")
    if report.missing_phrases:
        report.issues.append("Some approved phrases are missing from the PDF.")
    if report.page_count < 1:
        report.issues.append("The PDF has no pages.")
    if report.issues:
        report.status = "issues"
    elif not found_text:
        report.status = "unverified"
        report.issues.append("The PDF has no extractable text, so content was not confirmed.")
    else:
        report.status = "ok"
    return report


def _phrases(plan: BrochurePlan | None) -> list[str]:
    if plan is None:
        return []
    phrases = []
    for block in plan.blocks:
        if not block.source_backed:
            continue
        text = " ".join(block.text.split())
        if len(text) >= 4:
            phrases.append(text[:80])
    return phrases[:6]


def _contains(haystack: str, needle: str) -> bool:
    return " ".join(needle.split()).lower() in " ".join(haystack.split()).lower()


def _blank(page, text: str) -> bool:
    if text.strip():
        return False
    import fitz

    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), colorspace=fitz.csGRAY, alpha=False)
    samples = pixmap.samples
    if not samples:
        return True
    white = sum(1 for value in samples if value > 250)
    return white / len(samples) > 0.98


def _clipped(page) -> bool:
    payload = page.get_text("dict")
    width = page.rect.width
    height = page.rect.height
    for block in payload.get("blocks", []):
        x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
        if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1:
            return True
    return False
