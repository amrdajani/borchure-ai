"""Drive DesignCraft through `designcraft-cli mcp`, with no desktop window.

The CLI speaks newline-delimited JSON-RPC on stdin and stdout. One process
owns one document. The brochure API starts it for a job and stops it when
the PDF is written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from designcraft_brochure.craft.client import ControlError
from designcraft_brochure.safety import SafetyError


def cli_binary() -> str:
    return os.environ.get("DESIGNCRAFT_CLI", "designcraft-cli").strip() or "designcraft-cli"


def cli_installed() -> bool:
    binary = cli_binary()
    if Path(binary).is_file():
        return True
    return shutil.which(binary) is not None


class HeadlessCraft:
    """Same `call` surface as the desktop control client, backed by the CLI."""

    def __init__(
        self,
        work_root: Path,
        *,
        read_roots: list[Path] | None = None,
        binary: str | None = None,
        timeout: float = 180,
    ) -> None:
        self.timeout = timeout
        self._id = 1
        self._lock = threading.Lock()
        self._stderr: list[str] = []
        work = work_root.expanduser().resolve()
        work.mkdir(parents=True, exist_ok=True)
        program = binary or cli_binary()
        if Path(program).is_file():
            executable = str(Path(program).resolve())
        else:
            found = shutil.which(program)
            if not found:
                raise ControlError(
                    "designcraft-cli is not installed. "
                    "The Linux package from the DesignCraft releases puts it on PATH."
                )
            executable = found
        args = [
            executable,
            "mcp",
            "--automation-read-root",
            str(work),
            "--automation-write-root",
            str(work),
        ]
        seen = {work}
        for root in read_roots or []:
            resolved = root.expanduser().resolve()
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            args.extend(["--automation-read-root", str(resolved)])
        assets = Path.cwd() / "assets"
        if assets.is_dir() and assets.resolve() not in seen:
            args.extend(["--automation-read-root", str(assets.resolve())])
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "brochure-api", "version": "0.1.0"},
                },
            )
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def call(self, method: str, params: dict | None = None) -> Any:
        from designcraft_brochure.craft.client import ControlClient

        body = params or {}
        ControlClient._check_request(method, body)
        if method == "document.inspect":
            return self._tool("inspect_document", {})
        if method == "engine.commands":
            return self._tool("list_commands", {})
        if method == "ui.render":
            arguments: dict[str, Any] = {"page": int(body.get("page") or 0)}
            path = body.get("path")
            if isinstance(path, str) and path:
                arguments["path"] = path
            return self._tool("render_page", arguments)
        if method == "engine.execute":
            command = body.get("command")
            if command == "app.quit":
                return {}
            inner = body.get("params") or {}
            if not isinstance(inner, dict):
                raise SafetyError("DesignCraft command params must be an object.")
            return self._tool("execute", {"command": command, "params": inner})
        raise SafetyError(f"Refusing to send control method {method!r}.")

    def quit_app(self) -> None:
        self.close()

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _tool(self, name: str, arguments: dict) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            return {"value": result}
        if result.get("isError") is True:
            raise ControlError(_content_text(result) or f"DesignCraft rejected {name}.")
        text = _content_text(result)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}

    def _request(self, method: str, params: dict) -> Any:
        message_id = self._id
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        while True:
            message = self._read()
            if message.get("id") != message_id:
                continue
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise ControlError(str(detail or "DesignCraft rejected the request."))
            return message.get("result")

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc.poll() is not None or proc.stdin is None:
            raise ControlError(self._died())
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            proc.stdin.flush()
        except OSError as exc:
            raise ControlError(self._died()) from exc

    def _read(self) -> dict:
        proc = self._proc
        if proc.stdout is None:
            raise ControlError(self._died())
        deadline = threading.Timer(self.timeout, proc.kill)
        deadline.start()
        try:
            while True:
                if proc.poll() is not None and proc.stdout.closed:
                    raise ControlError(self._died())
                line = proc.stdout.readline()
                if not line:
                    raise ControlError(self._died())
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return data
        finally:
            deadline.cancel()

    def _drain_stderr(self) -> None:
        stderr = self._proc.stderr
        if stderr is None:
            return
        for line in stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-20]

    def _died(self) -> str:
        detail = self._stderr[-1] if self._stderr else "the process closed"
        return f"designcraft-cli stopped ({detail})."


def _content_text(result: dict) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)
