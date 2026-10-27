import json
from io import BytesIO

from designcraft_brochure.config import Settings
from designcraft_brochure.content.anthropic import messages


class _Response:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        return None


def test_messages_call_uses_the_anthropic_header(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response({"content": [{"type": "thinking", "thinking": "skip"}, {"type": "text", "text": '{"headline":"Harbor"}'}]})

    monkeypatch.setattr("designcraft_brochure.content.anthropic.urllib.request.urlopen", fake_urlopen)
    text = messages(api_key="test-key", model="claude-sonnet-5-5", system="Be brief.", user="Write it.")
    assert text == '{"headline":"Harbor"}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-sonnet-5-5"
    assert "temperature" not in seen["body"]
    assert seen["body"]["messages"] == [{"role": "user", "content": "Write it."}]


def test_settings_read_anthropic_key(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test\nANTHROPIC_MODEL=claude-haiku-5-5\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    settings = Settings.from_env(env)
    assert settings.anthropic_api_key == "sk-ant-test"
    assert settings.anthropic_model == "claude-haiku-5-5"
    assert settings.copy_enabled is True
