"""Turn proposal text into structured fields.

Regex collects emails, phones, prices, dates, and percentages. Jev assigns
company, project, category, and sentence roles by choosing among those
source spans. Nothing is invented to fill a gap.
"""

from __future__ import annotations

import re

from designcraft_brochure.jev.brain import Brain
from designcraft_brochure.jev.questions import extraction_questions, role_questions
from designcraft_brochure.models import Proposal, SourceDocument
from designcraft_brochure.safety import is_suspicious, strip_suspicious_lines

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
_PRICE = re.compile(
    r"(?:USD\s*)?\$\s?\d+(?:,\d{3})*(?:\.\d{2})?|\b\d+(?:\.\d+)?\s?(?:USD|dollars)\b",
    re.I,
)
_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b"
)
_STAT = re.compile(r"\b\d+(?:\.\d+)?\s?%")

_ROLE_FIELDS = {
    "service": "services",
    "benefit": "benefits",
    "specification": "specifications",
    "statistic": "statistics",
    "cta": "calls_to_action",
    "description": "description_parts",
}


def extract_proposal(source: SourceDocument, brain: Brain | None) -> Proposal:
    cleaned, removed = strip_suspicious_lines(source.text)
    proposal = Proposal(
        source_path=source.path,
        raw_text=cleaned,
        images=list(source.images),
        injection_flagged=removed > 0,
        missing=list(source.warnings),
    )
    if removed:
        proposal.missing.append(
            f"Ignored {removed} proposal line(s) that tried to instruct the agent."
        )
    if not cleaned.strip():
        proposal.missing.append("No usable proposal text.")
        return proposal

    proposal.contacts = _unique(_EMAIL.findall(cleaned) + _PHONE.findall(cleaned))
    proposal.prices = _unique(_PRICE.findall(cleaned))
    proposal.dates = _unique(_DATE.findall(cleaned))
    proposal.statistics = _unique(_STAT.findall(cleaned))
    sentences = split_sentences(cleaned)
    lines = meaningful_lines(cleaned)

    if brain is None:
        proposal.unclassified = sentences
        proposal.description = sentences[0] if sentences else None
        proposal.missing.append("Sentence roles were not confirmed by Jev.")
        _note_gaps(proposal)
        return proposal

    judgment = brain.judge(
        {"proposal_text": cleaned[:12000], "lines": lines},
        extraction_questions(lines),
    )
    proposal.confirmed_by_jev = True
    if judgment.noul("proposal_instructs_agent") >= 0.7 and not proposal.injection_flagged:
        proposal.injection_flagged = True
        proposal.missing.append("Jev flagged instruction-like content in the proposal.")
    proposal.company_name = _line_choice(judgment, "company_line", lines)
    proposal.project_name = _line_choice(judgment, "project_line", lines)
    proposal.category = judgment.choice("category").choice
    if sentences:
        roles = brain.judge(
            {"proposal_text": cleaned[:12000]},
            role_questions(sentences),
        )
        description_parts: list[str] = []
        for index, sentence in enumerate(sentences):
            if is_suspicious(sentence):
                continue
            role = roles.choice(f"role_{index}").choice
            field = _ROLE_FIELDS.get(role)
            if field == "description_parts":
                description_parts.append(sentence)
            elif field == "statistics":
                proposal.statistics = _unique(proposal.statistics + [sentence])
            elif field:
                getattr(proposal, field).append(sentence)
            else:
                proposal.unclassified.append(sentence)
        if description_parts:
            proposal.description = description_parts[0]
    if proposal.company_name is None:
        proposal.missing.append("Company name was not stated.")
    if proposal.project_name is None:
        proposal.missing.append("Project name was not stated.")
    _note_gaps(proposal)
    return proposal


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = []
    for part in parts:
        line = " ".join(part.split())
        if len(line) >= 20 and not is_suspicious(line):
            sentences.append(line)
    return sentences[:20]


def meaningful_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if len(line) >= 2 and not is_suspicious(line):
            lines.append(line[:240])
    return lines[:12] or ["unknown document"]


def _line_choice(judgment, key: str, lines: list[str]) -> str | None:
    selected = judgment.choice(key).choice
    if selected == "unknown":
        return None
    prefix = "line_"
    if not selected.startswith(prefix):
        return None
    index = int(selected[len(prefix) :])
    if index < 0 or index >= len(lines):
        return None
    return lines[index]


def _note_gaps(proposal: Proposal) -> None:
    if not proposal.prices:
        proposal.missing.append("No price was stated.")
    if not proposal.contacts:
        proposal.missing.append("No contact email or phone was stated.")
    if not proposal.dates:
        proposal.missing.append("No deadline or date was stated.")
    if not proposal.images:
        proposal.missing.append("No image or logo file was found in the proposal.")


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        item = " ".join(value.split())
        if item and item not in seen:
            seen.append(item)
    return seen
