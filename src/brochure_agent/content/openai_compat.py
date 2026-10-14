"""OpenAI-compatible chat calls for copy and vision.

Jev does not generate text or accept images. These calls are optional and
use the documented chat-completions request:
POST {base}/chat/completions with a Bearer key and a messages array.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    pass


def chat_completion(
    *,
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: float = 90,
) -> str:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"Model request failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Model request could not connect: {exc.reason}") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Model response did not include message content.") from exc
    return _content_text(content)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    raise ProviderError("Model content had an unexpected shape.")
