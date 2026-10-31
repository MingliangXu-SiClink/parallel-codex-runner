from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import BinaryIO


CONVERSATION_KINDS = frozenset({"output", "warning", "error"})
_RULE_VERSION = 1
_SCAN_BYTES = 8 * 1024 * 1024
_SCAN_RECORDS = 1000
_SCAN_SECONDS = 0.025


def _boundary(source: BinaryIO, offset: int) -> bytes:
    length = min(offset, 256)
    return hashlib.blake2b(os.pread(source.fileno(), length, offset - length), digest_size=16).digest()


class ConversationIndex:
    """Derived positions only; the JSONL and its original ordinals stay authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def locate(self, source: BinaryIO, *, total: int, cursor: int, agent: int | None,
               kinds: set[str], limit: int) -> tuple[list[int], int] | None:
        try:
            return self._locate(source, total=total, cursor=cursor, agent=agent, kinds=kinds, limit=limit)
        except sqlite3.DatabaseError as error:
            # This is a disposable access index, never an execution receipt.
            # A busy/full index falls back to the existing bounded raw reader.
            if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
                try:
                    self.delete()
                except OSError:
                    pass
            return None
        except OSError:
            return None

    def _locate(self, source: BinaryIO, *, total: int, cursor: int, agent: int | None,
                kinds: set[str], limit: int) -> tuple[list[int], int] | None:
        from ..remote.transcript import transcript_item

        stat = os.fstat(source.fileno())
        identity = (stat.st_dev, stat.st_ino, _RULE_VERSION)
        with closing(sqlite3.connect(self.path, timeout=0.05)) as connection:
            if self.path.stat().st_mode & 0o077:
                self.path.chmod(0o600)
            connection.execute("CREATE TABLE IF NOT EXISTS coverage (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "device INTEGER, inode INTEGER, rule INTEGER, cursor INTEGER, offset INTEGER, "
                "source_size INTEGER, source_mtime INTEGER, boundary BLOB)")
            connection.execute("CREATE TABLE IF NOT EXISTS hits (ordinal INTEGER PRIMARY KEY, agent INTEGER, kind TEXT)")
            connection.execute("CREATE INDEX IF NOT EXISTS agent_hits ON hits(agent, ordinal)")
            state = connection.execute("SELECT device,inode,rule,cursor,offset,source_size,source_mtime,boundary "
                                       "FROM coverage WHERE singleton=1").fetchone()
            valid = (state is not None and state[:3] == identity and state[4] <= stat.st_size
                and not (state[5] == stat.st_size and state[6] != stat.st_mtime_ns)
                and _boundary(source, state[4]) == state[7])
            if not valid:
                with connection:
                    connection.execute("DELETE FROM hits")
                    connection.execute("DELETE FROM coverage")
                    connection.execute("INSERT INTO coverage VALUES (1,?,?,?,?,?,?,?,?)",
                        (*identity, 0, 0, stat.st_size, stat.st_mtime_ns, _boundary(source, 0)))
                state = (*identity, 0, 0, stat.st_size, stat.st_mtime_ns, _boundary(source, 0))
            covered, offset = state[3:5]
            if covered < cursor:
                # A recent-tail request must not wait for an unrelated old prefix.
                return None
            if covered == cursor and covered < total:
                source.seek(offset)
                began = time.monotonic()
                position = covered
                end = offset
                hits = []
                while position < total:
                    raw = source.readline()
                    if not raw or not raw.endswith(b"\n"):
                        return None
                    try:
                        event = json.loads(raw)
                        item = transcript_item(event)
                        if item and item["kind"] in CONVERSATION_KINDS:
                            try:
                                event_agent = int(event.get("idx") or 0)
                            except (TypeError, ValueError):
                                event_agent = 0
                            hits.append((position, event_agent, item["kind"]))
                    except (TypeError, ValueError):
                        pass
                    position += 1
                    end = source.tell()
                    if (position - covered >= _SCAN_RECORDS or source.tell() - offset >= _SCAN_BYTES
                        or time.monotonic() - began >= _SCAN_SECONDS):
                        break
                if position == covered:
                    return None
                # Parse outside the transaction. Commit the candidates and their
                # contiguous coverage together, or replay this bounded segment.
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = connection.execute("SELECT device,inode,rule,cursor,offset,source_size,source_mtime,boundary "
                        "FROM coverage WHERE singleton=1").fetchone()
                    if current != state:
                        return None
                    connection.executemany("INSERT INTO hits VALUES (?,?,?)", hits)
                    connection.execute("UPDATE coverage SET cursor=?,offset=?,source_size=?,source_mtime=?,boundary=? "
                        "WHERE singleton=1", (position, end, stat.st_size, stat.st_mtime_ns, _boundary(source, end)))
                covered = position
            upper = min(covered, total)
            predicate = "ordinal>=? AND ordinal<?"
            parameters: list[object] = [cursor, upper]
            if agent is not None:
                predicate += " AND agent=?"
                parameters.append(int(agent))
            predicate += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters.extend(sorted(kinds))
            rows = connection.execute(f"SELECT ordinal FROM hits WHERE {predicate} ORDER BY ordinal LIMIT ?",
                                      (*parameters, limit + 1)).fetchall()
            ordinals = [row[0] for row in rows]
            end_cursor = ordinals[limit] if len(ordinals) > limit else upper
            return ordinals[:limit], end_cursor

    def delete(self) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                self.path.with_name(self.path.name + suffix).unlink()
            except FileNotFoundError:
                pass
