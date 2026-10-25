"""Read model menu metadata without creating a Codex thread or internal cache."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class ModelCatalogReadError(RuntimeError):
    pass


def _menu_models(records: list[Any]) -> list[dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ModelCatalogReadError("Invalid model record")
        model = record.get("model")
        efforts = record.get("supportedReasoningEfforts")
        hidden = record.get("hidden", False)
        default = record.get("defaultReasoningEffort")
        if (not isinstance(model, str) or not model.strip() or not isinstance(efforts, list)
                or not isinstance(hidden, bool) or (default is not None and not isinstance(default, str))):
            raise ModelCatalogReadError("Invalid model metadata")
        levels = []
        for level in efforts:
            effort = level.get("reasoningEffort") if isinstance(level, dict) else None
            if not isinstance(effort, str) or not effort.strip():
                raise ModelCatalogReadError("Invalid reasoning effort")
            entry = {"effort": effort.strip().lower()}
            if entry not in levels:
                levels.append(entry)
        normalized = {"slug": model.strip(), "visibility": "hide" if hidden else "list",
            "default_reasoning_level": default.strip().lower() if default else None,
            "supported_reasoning_levels": levels}
        previous = models.get(model.strip())
        if previous is not None and previous != normalized:
            raise ModelCatalogReadError("Conflicting model records")
        models[model.strip()] = normalized
    return list(models.values())


def read_model_catalog(
    executable: str, private_home: Path, environment: dict[str, str], timeout: float,
) -> list[dict[str, Any]]:
    process = subprocess.Popen(
        [executable, "app-server", "--listen", "stdio://"], cwd=str(private_home),
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=(os.name == "posix"),
    )
    assert process.stdin is not None and process.stdout is not None
    messages: queue.Queue[bytes | None | Exception] = queue.Queue()
    deadline = time.monotonic() + timeout
    identifier = 0

    def read_stream() -> None:
        try:
            while line := process.stdout.readline(1024 * 1024 + 2):
                if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
                    raise ModelCatalogReadError("Oversized catalog response")
                messages.put(line)
        except Exception as error:
            messages.put(error)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()

    def send(message: dict[str, Any]) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal identifier
        identifier += 1
        send({"id": identifier, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelCatalogReadError("Catalog request timed out")
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as error:
                raise ModelCatalogReadError("Catalog request timed out") from error
            if line is None or isinstance(line, Exception):
                raise ModelCatalogReadError("Catalog connection closed")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ModelCatalogReadError("Invalid catalog response")
            if "method" in message:
                if "id" in message:
                    send({"id": message["id"], "error": {
                        "code": -32601, "message": "Read-only model catalog client"}})
                continue
            if message.get("id") != identifier:
                continue
            if "error" in message or not isinstance(message.get("result"), dict):
                raise ModelCatalogReadError("Catalog request failed")
            return message["result"]

    try:
        initialized = request("initialize", {"clientInfo": {
            "name": "parallel_codex_model_catalog", "version": "1"}, "capabilities": {}})
        if not isinstance(initialized.get("userAgent"), str):
            raise ModelCatalogReadError("Unrecognized catalog initialization")
        send({"method": "initialized", "params": {}})
        records: list[Any] = []
        cursors: set[str] = set()
        cursor = None
        for _ in range(128):
            params: dict[str, Any] = {"limit": 100, "includeHidden": True}
            if cursor is not None:
                params["cursor"] = cursor
            result = request("model/list", params)
            page = result.get("data")
            if not isinstance(page, list) or len(records) + len(page) > 4096:
                raise ModelCatalogReadError("Invalid catalog page")
            records.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return _menu_models(records)
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ModelCatalogReadError("Invalid catalog cursor")
            cursors.add(cursor)
        raise ModelCatalogReadError("Incomplete model catalog")
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        else:
            process.wait()
        reader.join(timeout=3)
        process.stdout.close()
