"""Batch-owned, immutable history with independent copy-on-write Agent files."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable


def clone_file(source: Path, destination: Path) -> bool:
    """Clone on APFS, otherwise copy; never share a writable inode."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    cloned = False
    if sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "clonefile", None)
        if function is None:
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
            return False
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        function.restype = ctypes.c_int
        cloned = function(os.fsencode(source), os.fsencode(destination), 0) == 0
    if not cloned:
        shutil.copyfile(source, destination)
    destination.chmod(0o600)
    return cloned


@dataclass
class RolloutTemplate:
    path: Path
    workspace: str = field(default_factory=lambda: "/pcr-workspace-" + uuid.uuid4().hex)
    home: str = field(default_factory=lambda: "/pcr-home-" + uuid.uuid4().hex)
    bindings: list[tuple[int, int]] = field(default_factory=list)
    active_calls: set[str] = field(default_factory=set)
    records: int = 0

    def write(self, output: BinaryIO, record: Any, text: str, *, bound: bool) -> None:
        data = text.rstrip("\r\n").encode("utf-8")
        self.records += 1
        if bound:
            # JSON whitespace reserves room for per-Agent path lengths while
            # keeping all unrelated history blocks cloneable and byte-stable.
            extra = max(1024, (data.count(self.workspace.encode()) + data.count(self.home.encode())) * 1024)
            self.bindings.append((output.tell(), len(data) + extra + 1))
            data += b" " * extra
        output.write(data + b"\n")

    def materialize(self, destination: Path, workspace: Path, home: Path,
                    rebind: Callable[..., bool]) -> dict[str, Any]:
        temporary = destination.with_name("." + destination.name + "-" + uuid.uuid4().hex)
        patched = 0
        try:
            fast = clone_file(self.path, temporary)
            with temporary.open("r+b") as output:
                for offset, length in self.bindings:
                    output.seek(offset)
                    record = json.loads(output.read(length))
                    payload = record.get("payload")
                    if record.get("type") == "session_meta" and isinstance(payload, dict):
                        payload["cwd"] = str(workspace)
                    else:
                        rebind(record, str(workspace), {self.workspace}, str(home), {self.home})
                    data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    if len(data) >= length:
                        # Unusually long paths still get correct history. Only
                        # this copy loses the fixed-offset clone optimization.
                        fast = False
                        break
                    output.seek(offset)
                    output.write(data + b" " * (length - len(data) - 1) + b"\n")
                    patched += length
                else:
                    os.replace(temporary, destination)
                    return {"cloned": fast, "rebound_bytes": patched,
                            "history_bytes": destination.stat().st_size,
                            "history_mode": "legacy"}
            # Stream unchanged ranges and parse only indexed executable context,
            # never perform another full JSON parse of the archive.
            patched = 0
            with self.path.open("rb") as source, temporary.open("wb") as output:
                for offset, length in self.bindings:
                    remaining = offset - source.tell()
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Prepared history was truncated")
                        output.write(chunk)
                        remaining -= len(chunk)
                    record = json.loads(source.read(length))
                    if record.get("type") == "session_meta":
                        record["payload"]["cwd"] = str(workspace)
                    else:
                        rebind(record, str(workspace), {self.workspace}, str(home), {self.home})
                    output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
                    patched += length
                shutil.copyfileobj(source, output, 1024 * 1024)
            os.replace(temporary, destination)
            return {"cloned": False, "rebound_bytes": patched,
                    "history_bytes": destination.stat().st_size,
                    "history_mode": "legacy"}
        finally:
            temporary.unlink(missing_ok=True)
