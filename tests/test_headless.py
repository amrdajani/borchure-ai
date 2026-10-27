import json
import os
import stat
from pathlib import Path

import pytest

from designcraft_brochure.api import BrochureAPI, bind_host
from designcraft_brochure.config import Settings
from designcraft_brochure.craft.client import ControlError
from designcraft_brochure.craft.headless import HeadlessCraft
from designcraft_brochure.safety import SafetyError


FAKE_CLI = r"""#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        body = {"protocolVersion": "2024-11-05", "capabilities": {}}
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "inspect_document":
            payload = {
                "pages": [{}],
                "stories": [{"id": 1, "text": "Hello", "length": 5}],
                "items": [{"id": 10, "kind": "text", "story": 1}],
            }
        elif name == "execute":
            payload = {"command": msg["params"]["arguments"]["command"]}
        else:
            payload = {"commands": ["file.open"]}
        body = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
    else:
        body = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": body}), flush=True)
"""


def _fake_cli(tmp_path: Path) -> Path:
    path = tmp_path / "designcraft-cli"
    path.write_text(FAKE_CLI, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_headless_client_inspects_and_edits(tmp_path):
    work = tmp_path / "job"
    client = HeadlessCraft(work, binary=str(_fake_cli(tmp_path)))
    try:
        inspected = client.call("document.inspect", {})
        assert inspected["stories"][0]["text"] == "Hello"
        edited = client.call(
            "engine.execute",
            {"command": "story.setText", "params": {"story": 1, "text": "Hello"}},
        )
        assert edited["command"] == "story.setText"
    finally:
        client.close()


def test_headless_client_reports_a_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("DESIGNCRAFT_CLI", "designcraft-cli-missing")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(ControlError, match="not installed"):
        HeadlessCraft(tmp_path / "job", binary="designcraft-cli-missing")


def test_public_bind_requires_a_token():
    assert bind_host("127.0.0.1", "") == "127.0.0.1"
    with pytest.raises(SafetyError, match="BROCHURE_API_TOKEN"):
        bind_host("0.0.0.0", "")
    assert bind_host("0.0.0.0", "secret") == "0.0.0.0"


def test_api_token_blocks_uploads_and_allows_health(tmp_path):
    api = BrochureAPI(
        tmp_path / "jobs",
        Settings(typesafe_api_key="test-key"),
        backend="headless",
        token="secret",
        designcraft_ready=lambda _settings: True,
    )
    status, payload, _media = api.handle("GET", "/health", {}, b"")
    assert status == 200
    assert json.loads(payload)["backend"] == "headless"
    status, payload, _media = api.handle("POST", "/brochures", {}, b"")
    assert status == 401
    status, _payload, _media = api.handle(
        "POST",
        "/brochures",
        {"Authorization": "Bearer secret"},
        b"",
    )
    assert status != 401
    os.environ.pop("BROCHURE_API_TOKEN", None)
