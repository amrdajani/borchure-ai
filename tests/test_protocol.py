import json
import socket
import threading

import pytest

from designcraft_brochure.craft.client import ControlClient, ControlError
from designcraft_brochure.safety import SafetyError


def test_refuses_remote_host():
    with pytest.raises(SafetyError):
        ControlClient("8.8.8.8", 7979)


def test_refuses_commands_outside_the_brochure_allowlist():
    client = ControlClient("127.0.0.1", 7979)
    with pytest.raises(SafetyError):
        client.call("app.quit", {})
    with pytest.raises(SafetyError):
        client.call("engine.execute", {"command": "file.delete", "params": {}})
    with pytest.raises(SafetyError):
        client.call("ui.click", {"x": 1, "y": 1})


def test_roundtrip_matches_the_control_protocol():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)
    seen = {}

    def serve():
        conn, _ = server.accept()
        data = b""
        while b"\n" not in data:
            data += conn.recv(4096)
        request = json.loads(data.decode("utf-8"))
        seen["method"] = request["method"]
        seen["params"] = request["params"]
        reply = {"id": request["id"], "ok": True, "result": {"stories": [{"id": 1, "preview": "Hello"}]}}
        conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    client = ControlClient("127.0.0.1", port, timeout=2)
    try:
        result = client.call("document.inspect", {})
    finally:
        client.close()
        thread.join(timeout=2)
        server.close()
    assert seen["method"] == "document.inspect"
    assert seen["params"] == {}
    assert result["stories"][0]["preview"] == "Hello"


def test_error_reply_is_not_retried():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)
    calls = {"n": 0}

    def serve():
        conn, _ = server.accept()
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        calls["n"] += data.count(b"\n")
        request = json.loads(data.decode("utf-8").splitlines()[0])
        reply = {"id": request["id"], "ok": False, "error": "no document"}
        conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    client = ControlClient("127.0.0.1", port, timeout=2)
    with pytest.raises(ControlError, match="no document"):
        client.call("document.inspect", {})
    thread.join(timeout=2)
    server.close()
    assert calls["n"] == 1
