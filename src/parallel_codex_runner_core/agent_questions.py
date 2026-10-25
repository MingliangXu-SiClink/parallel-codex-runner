"""Durable answers addressed to a live native request, never to a new prompt."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Existing non-POSIX exec clients remain non-interactive.
    fcntl = None


class QuestionError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any, name: str, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise QuestionError(f"Invalid {name}")
    return value


def validate_questions(params: dict[str, Any]) -> dict[str, Any]:
    for key in ("threadId", "turnId", "itemId"):
        _text(params.get(key), key, 256)
    if not isinstance(params.get("isBlocking", True), bool):
        raise QuestionError("Invalid isBlocking")
    questions = params.get("questions")
    if not isinstance(questions, list) or not questions or params.get("kind") != "async" and len(questions) > 3:
        raise QuestionError("Expected one to three questions")
    ids = set()
    for question in questions:
        if not isinstance(question, dict):
            raise QuestionError("Invalid question")
        key = _text(question.get("id"), "question ID", 128)
        if key in ids:
            raise QuestionError("Duplicate question ID")
        ids.add(key)
        _text(question.get("header"), "question header", 256)
        _text(question.get("question"), "question text")
        for flag in ("isOther", "isSecret"):
            if not isinstance(question.get(flag, False), bool):
                raise QuestionError(f"Invalid {flag}")
        options = question.get("options")
        if options is not None:
            if not isinstance(options, list) or not 0 <= len(options) <= 20:
                raise QuestionError("Invalid question options")
            labels = set()
            for option in options:
                if not isinstance(option, dict):
                    raise QuestionError("Invalid option")
                label = _text(option.get("label"), "option label", 1024)
                if label in labels or not isinstance(option.get("description", ""), str):
                    raise QuestionError("Invalid or duplicate option")
                labels.add(label)
    if len(_json(params).encode()) > 48 * 1024:
        raise QuestionError("Question request is too large")
    return {**params, "isBlocking": params.get("isBlocking", True)}


def async_questions(thread_id: str, turn_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("type") not in {"agentMessage", "agent_message"} or item.get("delivery") != "async" or not item.get("questions"):
        return None
    item_id = _text(item.get("id"), "async question item", 256)
    questions = []
    for index, question in enumerate(item["questions"]):
        options = question.get("options")
        questions.append({"id": json.dumps(["request_user_input_async", item_id, index], separators=(",", ":")),
            "header": f"Question {index + 1}", "question": question.get("title"), "isOther": True,
            "options": [{"label": option, "description": ""} for option in options] if options else None})
    return validate_questions({"threadId": thread_id, "turnId": turn_id, "itemId": item_id,
        "kind": "async", "isBlocking": False, "questions": questions})


def async_reply(record: dict[str, Any], answers: dict[str, Any]) -> str:
    values = [{"questionItemId": question["id"], "question": question["question"],
               "answer": answers[question["id"]]["answers"][0]} for question in record["questions"]]
    encoded = _json(values).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return "<send_user_message_question_reply>\n" + encoded + "\n</send_user_message_question_reply>"


class QuestionStore:
    def __init__(self, directory: Path, *, async_only: bool = False):
        self.directory = Path(directory)
        self.async_only = async_only
        self.async_path = self.directory / "agent_async_questions.sqlite3"
        self.path = self.async_path if async_only else self.directory / "agent_questions.sqlite3"
        self.lease = self.directory / "agent_questions.owner"

    def _async(self) -> QuestionStore:
        return self if self.async_only else QuestionStore(self.directory, async_only=True)

    @contextlib.contextmanager
    def connection(self, *, create: bool = False):
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        if self.path.is_symlink():
            raise QuestionError("Question store must not be a symbolic link")
        db = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                if create:
                    db.execute("""CREATE TABLE IF NOT EXISTS requests (
                        id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
                        native_id TEXT NOT NULL, payload TEXT NOT NULL,
                        state TEXT NOT NULL, delivery TEXT NOT NULL DEFAULT 'none',
                        answer_id TEXT, answer TEXT, created REAL NOT NULL,
                        updated REAL NOT NULL, UNIQUE(connection_id, native_id))""")
                yield db
        finally:
            db.close()

    def owner_alive(self) -> bool:
        if fcntl is None:
            return False
        try:
            fd = os.open(self.lease, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(fd)

    def _record(self, row: sqlite3.Row, alive: bool) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        state, delivery = row["state"], row["delivery"]
        asynchronous = payload.get("kind") == "async"
        if asynchronous and delivery == "sending" and not alive:
            delivery = "unknown"
        if state == "open" and not alive and not asynchronous:
            state = "invalidated"
            if delivery in {"sending", "sent"}:
                delivery = "unknown"
        return {"id": row["id"], "connection_id": row["connection_id"],
                "thread_id": payload["threadId"], "turn_id": payload["turnId"],
                "item_id": payload["itemId"], "is_blocking": payload["isBlocking"],
                "questions": payload["questions"], "state": state, "delivery": delivery,
                "answer_id": row["answer_id"],
                "answers": json.loads(row["answer"]) if row["answer"] else None,
                "created_at": row["created"], "updated_at": row["updated"], "kind": "async" if asynchronous else "rpc"}

    def open_async(self, params: dict[str, Any]) -> str:
        if not self.async_only:
            return self._async().open_async(params)
        params = validate_questions(params)
        if params.get("kind") != "async":
            raise QuestionError("Expected a native async question")
        identity = [params[key] for key in ("threadId", "turnId", "itemId")]
        key = hashlib.sha256(_json(["async", *identity]).encode()).hexdigest()
        encoded, now = _json(params), time.time()
        with self.connection(create=True) as db:
            old = db.execute("SELECT payload FROM requests WHERE id=?", (key,)).fetchone()
            if old is not None and old["payload"] != encoded:
                raise QuestionError("Async question identity was reused with different questions")
            db.execute("INSERT OR IGNORE INTO requests(id,connection_id,native_id,payload,state,created,updated) "
                       "VALUES(?,?,?,?, 'open',?,?)", (key, "async:" + params["threadId"], _json(params["itemId"]), encoded, now, now))
        return key

    def queued_async(self) -> list[dict[str, Any]]:
        if not self.async_only:
            return self._async().queued_async()
        if not self.path.exists():
            return []
        with self.connection() as db:
            rows = db.execute("SELECT * FROM requests WHERE state='open' AND delivery='queued' "
                              "AND connection_id LIKE 'async:%' ORDER BY created LIMIT 20").fetchall()
            return [self._record(row, True) for row in rows]

    def async_delivery(self, request_id: str, before: str, after: str) -> bool:
        if not self.async_only:
            return self._async().async_delivery(request_id, before, after)
        with self.connection() as db:
            changed = db.execute("UPDATE requests SET delivery=?,state=?,updated=? WHERE id=? AND delivery=? "
                       "AND connection_id LIKE 'async:%'", (after, "resolved" if after == "sent" else "open",
                       time.time(), request_id, before))
            return changed.rowcount == 1

    def import_async_output(self, path: Path) -> None:
        if not self.async_only:
            return self._async().import_async_output(path)
        if not path.is_file() or path.is_symlink():
            return
        with self.connection(create=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS async_projection (id INTEGER PRIMARY KEY, cursor INTEGER, thread TEXT, turn TEXT, skipping INTEGER, inode INTEGER)")
            row = db.execute("SELECT * FROM async_projection WHERE id=1").fetchone()
            cursor, thread, turn, skipping, inode = ((row["cursor"], row["thread"], row["turn"], row["skipping"], row["inode"])
                if row else (0, "", "", False, 0))
        # Older live owners already emit the structured fields. Import only
        # their original log, incrementally; never infer choices from Markdown.
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            stat = os.fstat(source.fileno())
            if stat.st_size == cursor and stat.st_ino == inode:
                return
            if stat.st_size < cursor or stat.st_ino != inode:
                cursor, thread, turn, skipping = 0, "", "", False
            inode = stat.st_ino
            source.seek(cursor)
            beginning = cursor
            for _ in range(2048):
                if source.tell() - beginning >= 1024 * 1024:
                    break
                raw = source.readline(256 * 1024 + 1)
                if not raw:
                    break
                if skipping or len(raw) > 256 * 1024:
                    cursor = source.tell()
                    skipping = not raw.endswith(b"\n")
                    continue
                if not raw.endswith(b"\n"):
                    break
                cursor = source.tell()
                try:
                    event = json.loads(raw)
                    if event.get("type") == "thread.started":
                        thread = event.get("thread_id", "")
                    elif event.get("type") == "turn.started":
                        turn = event.get("turn_id", "")
                    elif event.get("type") == "item.completed" and thread and turn:
                        params = async_questions(thread, turn, event.get("item", {}))
                        if params:
                            self.open_async(params)
                except (ValueError, TypeError, KeyError):
                    continue
        with self.connection() as db:
            db.execute("INSERT INTO async_projection VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "cursor=excluded.cursor,thread=excluded.thread,turn=excluded.turn,skipping=excluded.skipping,inode=excluded.inode "
                "WHERE async_projection.inode != excluded.inode OR async_projection.cursor <= excluded.cursor",
                (cursor, thread, turn, skipping, inode))

    def snapshot(self, *, limit: int = 20) -> list[dict[str, Any]]:
        asynchronous = self._async().snapshot(limit=limit) if not self.async_only else []
        if not self.path.exists():
            return asynchronous
        alive = self.owner_alive()
        with self.connection() as db:
            rows = db.execute("SELECT * FROM requests ORDER BY (state='open' AND delivery='none') DESC, (state='open') DESC, created DESC LIMIT ?",
                              (min(max(limit, 1), 100),)).fetchall()
        records = asynchronous
        for row in rows:
            record = self._record(row, alive)
            records.append(record)
        records.sort(key=lambda record: (record["state"] != "open", record["delivery"] != "none", -record["created_at"]))
        bounded, size = [], 0
        for record in records[:limit]:
            size += len(_json(record).encode())
            if size > 192 * 1024:
                break
            bounded.append(record)
        return bounded

    def pending_count(self) -> int:
        asynchronous = self._async().pending_count() if not self.async_only else 0
        if not self.path.exists():
            return asynchronous
        alive = self.owner_alive()
        with self.connection() as db:
            return asynchronous + db.execute("SELECT count(*) FROM requests WHERE state='open' AND delivery='none' "
                              "AND (? OR connection_id LIKE 'async:%')", (alive,)).fetchone()[0]

    def blocking_count(self) -> int:
        if not self.path.exists() or not self.owner_alive():
            return 0
        with self.connection() as db:
            return sum(json.loads(r[0])["isBlocking"] for r in db.execute(
                "SELECT payload FROM requests WHERE state='open'"))

    def get(self, request_id: str) -> dict[str, Any]:
        if self.path.exists():
            with self.connection() as db:
                row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
                if row is not None:
                    return self._record(row, self.owner_alive())
        if not self.async_only:
            return self._async().get(request_id)
        raise QuestionError("The original question is no longer available")

    def answer(self, request_id: str, connection_id: str, answer_id: str,
               answers: dict[str, Any]) -> dict[str, Any]:
        if not self.async_only and self.get(request_id).get("kind") == "async":
            return self._async().answer(request_id, connection_id, answer_id, answers)
        _text(answer_id, "answer identity", 128)
        if not self.path.exists():
            raise QuestionError("The original question is no longer available")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row["connection_id"] != connection_id:
                raise QuestionError("This question belongs to a different execution")
            payload = json.loads(row["payload"])
            if not isinstance(answers, dict) or set(answers) != {q["id"] for q in payload["questions"]}:
                raise QuestionError("Answer every question in the original request")
            normalized = {}
            for question in payload["questions"]:
                answer = answers[question["id"]]
                values = answer.get("answers") if isinstance(answer, dict) else None
                if not isinstance(values, list) or len(values) != 1:
                    raise QuestionError("Select one answer for each question")
                text = _text(values[0], "answer")
                options = question.get("options")
                if options and not question.get("isOther") and text not in {o["label"] for o in options}:
                    raise QuestionError("Answer is not one of the offered options")
                normalized[question["id"]] = {"answers": [text]}
            encoded = _json(normalized)
            if row["answer"] is not None:
                if row["answer"] != encoded:
                    raise QuestionError("A different answer has already been committed")
                return self._record(row, self.owner_alive())
            if row["state"] != "open" or payload.get("kind") != "async" and not self.owner_alive():
                raise QuestionError("The original question is no longer active; your answer was not sent")
            db.execute("UPDATE requests SET answer_id=?,answer=?,delivery='queued',updated=? WHERE id=?",
                       (answer_id, encoded, time.time(), request_id))
            return self._record(db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone(), True)


class QuestionOwner:
    """Only the adapter holding this lease may write native RPC responses."""

    def __init__(self, directory: Path):
        if fcntl is None:
            raise QuestionError("Native Agent questions require a POSIX execution owner")
        self.store = QuestionStore(directory)
        self.store.directory.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.store.lease, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.id = uuid.uuid4().hex
            with self.store.connection(create=True) as db:
                db.execute("UPDATE requests SET state='invalidated', delivery=CASE WHEN delivery IN ('sending','sent') "
                           "THEN 'unknown' ELSE delivery END,updated=? WHERE state='open' AND connection_id NOT LIKE 'async:%'", (time.time(),))
        except BaseException:
            os.close(self.fd)
            raise

    def open(self, native_id: Any, params: dict[str, Any]) -> str:
        if isinstance(native_id, bool) or not isinstance(native_id, (str, int)):
            raise QuestionError("Invalid native request identity")
        params = validate_questions(params)
        native = _json(native_id)
        key = hashlib.sha256(_json([self.id, native]).encode()).hexdigest()
        encoded, now = _json(params), time.time()
        with self.store.connection() as db:
            row = db.execute("SELECT payload FROM requests WHERE id=?", (key,)).fetchone()
            if row is not None and row["payload"] != encoded:
                raise QuestionError("Native request identity was reused with different questions")
            db.execute("INSERT OR IGNORE INTO requests(id,connection_id,native_id,payload,state,created,updated) "
                       "VALUES(?,?,?,?, 'open',?,?)", (key, self.id, native, encoded, now, now))
        return key

    def take_answers(self) -> list[tuple[str, Any, dict[str, Any]]]:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM requests WHERE connection_id=? AND state='open' AND delivery='queued'",
                              (self.id,)).fetchall()
            for row in rows:
                db.execute("UPDATE requests SET delivery='sending',updated=? WHERE id=?", (time.time(), row["id"]))
            return [(r["id"], json.loads(r["native_id"]), {"answers": json.loads(r["answer"])}) for r in rows]

    def sent(self, request_id: str) -> None:
        with self.store.connection() as db:
            db.execute("UPDATE requests SET delivery='sent',updated=? WHERE id=? AND connection_id=? AND delivery='sending'",
                       (time.time(), request_id, self.id))

    def resolve(self, native_id: Any = None, *, turn_id: str | None = None) -> None:
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM requests WHERE connection_id=? AND state='open'", (self.id,)).fetchall()
            for row in rows:
                if (turn_id is not None and json.loads(row["payload"])["turnId"] == turn_id
                    or turn_id is None and row["native_id"] == _json(native_id)):
                    db.execute("UPDATE requests SET state=?,updated=? WHERE id=?",
                               ("resolved" if row["delivery"] == "sent" and turn_id is None else "invalidated",
                                time.time(), row["id"]))

    def close(self) -> None:
        if self.fd is None:
            return
        try:
            with self.store.connection() as db:
                db.execute("UPDATE requests SET state='invalidated',delivery=CASE WHEN delivery IN ('sending','sent') "
                           "THEN 'unknown' ELSE delivery END,updated=? WHERE connection_id=? AND state='open'",
                           (time.time(), self.id))
        finally:
            os.close(self.fd)
            self.fd = None
