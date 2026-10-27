"""Page pictures of a finished brochure, for the chat to show next to the messages."""

from __future__ import annotations

from pathlib import Path


def render_pages(pdf_path: Path, out_dir: Path, *, dpi: int = 110) -> list[Path]:
    """One PNG per page. A PDF that cannot be rendered gives no previews, never an error."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore[no-redef]
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return []
    pages: list[Path] = []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(document, start=1):
            path = out_dir / f"page-{index}.png"
            page.get_pixmap(dpi=dpi).save(str(path))
            pages.append(path)
    except Exception:
        return []
    finally:
        document.close()
    return pages
