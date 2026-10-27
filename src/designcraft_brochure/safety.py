"""Path and proposal-content guards.

Proposal text is untrusted. These checks reject paths and instruction-like
lines before any DesignCraft command is sent.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


class SafetyError(ValueError):
    pass


_INJECTION = (
    re.compile(r"ignore (all |any |the )?(previous |prior |above )?instructions", re.I),
    re.compile(r"disregard (the |your )?(rules|policy|instructions)", re.I),
    re.compile(r"do not follow (the |your )?(rules|policy)", re.I),
    re.compile(r"you are now", re.I),
    re.compile(r"<\s*/?\s*system\s*>", re.I),
    re.compile(r"\bexfiltrate\b", re.I),
)

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_suspicious(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION)


def strip_suspicious_lines(text: str) -> tuple[str, int]:
    kept: list[str] = []
    removed = 0
    for line in text.splitlines():
        if line.strip() and is_suspicious(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def ensure_within(path: Path, roots: list[Path]) -> Path:
    resolved = path.expanduser().resolve()
    for root in roots:
        base = root.expanduser().resolve()
        if resolved == base or _is_relative_to(resolved, base):
            return resolved
    raise SafetyError(f"Path is outside the allowed folders: {path}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def assert_not_template_target(source: Path, destination: Path, template_root: Path) -> None:
    source_resolved = source.resolve()
    dest_resolved = destination.resolve()
    root = template_root.resolve()
    if dest_resolved == source_resolved:
        raise SafetyError("Refusing to write a template onto itself.")
    if _is_relative_to(dest_resolved, root):
        raise SafetyError("Refusing to write inside the template library.")


def unsupported_facts(text: str, source: str) -> list[str]:
    """Numbers and email addresses in `text` that never appear in `source`."""
    problems: list[str] = []
    for number in set(_NUMBER.findall(text)):
        if number not in source:
            problems.append(number)
    for email in set(match.group(0) for match in _EMAIL.finditer(text)):
        if email.lower() not in source.lower():
            problems.append(email)
    return problems


def clean_typed_text(text: str, limit: int = 1500) -> str:
    flattened = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not flattened:
        raise SafetyError("Refusing to type empty text.")
    if len(flattened) > limit:
        raise SafetyError("Refusing to type text over the length limit.")
    if is_suspicious(flattened):
        raise SafetyError("Refusing to type instruction-like text.")
    return flattened


def require_localhost(host: str) -> str:
    cleaned = host.strip().lower()
    if cleaned not in LOCAL_HOSTS:
        raise SafetyError("The DesignCraft control channel is only allowed on 127.0.0.1.")
    return "127.0.0.1"
