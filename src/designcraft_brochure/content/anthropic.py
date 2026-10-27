"""Anthropic Messages API for optional brochure copy.

Jev does not write the sentences. When ANTHROPIC_API_KEY is set, this call
drafts them. Claude Sonnet 5.5 rejects a non-default temperature, so the
request leaves that field out.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from designcraft_brochure.content.openai_compat import ProviderError


_API_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"


def messages(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2048,
    timeout: float = 90,
) -> str:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": _VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"Anthropic request failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Anthropic request could not connect: {exc.reason}") from exc
    return _text(body)


def _text(body: object) -> str:
    if not isinstance(body, dict):
        raise ProviderError("Anthropic response had an unexpected shape.")
    content = body.get("content")
    if not isinstance(content, list):
        raise ProviderError("Anthropic response did not include message content.")
    parts = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise ProviderError("Anthropic response did not include text.")
    return text
