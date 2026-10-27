"""JSON-line client for a running DesignCraft app.

The request shape is the one in DesignCraft's control protocol: one JSON
object per line, with a string method, optional id, and optional params.
The server listens on 127.0.0.1 only. This client refuses any other host
and refuses any method or command outside the brochure allowlist.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from designcraft_brochure.safety import SafetyError, require_localhost


class ControlError(RuntimeError):
    pass


# Methods this agent is allowed to send. app.quit, ui.click, and ui.key are
# intentionally absent: brochure edits go through document commands.
ALLOWED_METHODS = frozenset(
    {
        "document.inspect",
        "engine.commands",
        "engine.execute",
        "ui.render",
    }
)

ALLOWED_COMMANDS = frozenset(
    {
        "file.open",
        "file.openIdml",
        "story.get",
        "story.setText",
        "file.place",
        "file.saveAs",
        "file.exportPdf",
        "file.exportIdml",
        # Formatting only. The finishing pass in polish.py sends these; a choice
        # from Jev is still checked against validate.py, which does not allow them.
        "text.select",
        "type.char",
        "type.para",
        "type.selectionAttrs",
        "transform.resize",
        "object.textFrameOptions",
        "object.fit",
        # Chat edits: colours, nudging, and find-and-change inside one story.
        "swatch.create",
        "object.fill",
        "transform.move",
        "find.change",
    }
)


class ControlClient:
    def __init__(self, host: str, port: int, timeout: float = 90) -> None:
        self.host = require_localhost(host)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise SafetyError("DesignCraft control port must be between 1 and 65535.")
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._reader = None
        self._writer = None
        self._next_id = 1

    def call(self, method: str, params: dict | None = None) -> Any:
        self._check_request(method, params or {})
        payload = {"id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        if len(line.encode("utf-8")) > 4_000_000:
            raise SafetyError("Control request is over DesignCraft's 4 MiB line limit.")
        try:
            self._ensure()
            assert self._writer is not None and self._reader is not None
            self._writer.write(line + "\n")
            self._writer.flush()
            reply = self._reader.readline()
        except OSError as exc:
            self.close()
            raise ControlError(
                f"DesignCraft at {self.host}:{self.port} is not reachable ({exc.__class__.__name__}). Nothing further was sent."
            ) from exc
        if not reply:
            self.close()
            raise ControlError(
                "DesignCraft closed the connection. The command may have completed; inspect the document before trying again."
            )
        try:
            data = json.loads(reply)
        except json.JSONDecodeError as exc:
            self.close()
            raise ControlError("DesignCraft returned a line that was not JSON.") from exc
        if data.get("ok") is True:
            return data.get("result")
        self.close()
        message = data.get("error") if isinstance(data, dict) else None
        raise ControlError(str(message or "DesignCraft rejected the request."))

    def quit_app(self) -> None:
        """Close DesignCraft. This is not offered to Jev; a run calls it once at the end."""
        payload = {
            "id": self._next_id,
            "method": "engine.execute",
            "params": {"command": "app.quit", "params": {}},
        }
        self._next_id += 1
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        try:
            self._ensure()
            assert self._writer is not None and self._reader is not None and self._sock is not None
            self._sock.settimeout(5)
            self._writer.write(line + "\n")
            self._writer.flush()
            reply = self._reader.readline()
        except OSError:
            self.close()
            return
        self.close()
        if not reply:
            return
        try:
            data = json.loads(reply)
        except json.JSONDecodeError as exc:
            raise ControlError("DesignCraft returned a line that was not JSON.") from exc
        if data.get("ok") is not True:
            message = data.get("error") if isinstance(data, dict) else None
            raise ControlError(str(message or "DesignCraft did not close."))

    def close(self) -> None:
        reader, writer, sock = self._reader, self._writer, self._sock
        self._reader = None
        self._writer = None
        self._sock = None
        for handle in (writer, reader):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _ensure(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise ControlError(
                f"DesignCraft at {self.host}:{self.port} is not reachable. "
                "Start it with `designcraft --control "
                f"{self.port}`."
            ) from exc
        sock.settimeout(self.timeout)
        self._sock = sock
        self._reader = sock.makefile("r", encoding="utf-8", newline="\n")
        self._writer = sock.makefile("w", encoding="utf-8", newline="\n")

    @staticmethod
    def _check_request(method: str, params: dict) -> None:
        if method not in ALLOWED_METHODS:
            raise SafetyError(f"Refusing to send control method {method!r}.")
        if method != "engine.execute":
            return
        command = params.get("command")
        if command not in ALLOWED_COMMANDS:
            raise SafetyError(f"Refusing to run DesignCraft command {command!r}.")
        inner = params.get("params", {})
        if inner is None:
            return
        if not isinstance(inner, dict):
            raise SafetyError("Command params must be an object.")
