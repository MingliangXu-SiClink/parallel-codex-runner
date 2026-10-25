"""Compact native SQLite snapshots containing only a resume's dependencies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Iterable


STATE_TABLES = {
    "_sqlx_migrations", "threads", "thread_dynamic_tools", "thread_attachments",
    "thread_spawn_edges", "thread_sections", "projects", "project_roots",
    "backfill_state", "remote_control_enrollments", "external_agent_config_imports",
    "rollout_migration_state", "rollout_migration_skipped_rollouts",
    "project_idempotency_keys", "sqlite_sequence",
    "guardian_review_feedback", "thread_read_receipts",
}
PROJECTION_TABLES = {
    "_sqlx_migrations", "thread_turns", "thread_items", "thread_realtime_items",
    "thread_history_projection_state", "sqlite_sequence",
}
# Native schemas and migration checksums exercised by the resume/fork probes.
# A CLI migration needs a reviewed adapter, not a silent whole-library fallback.
NATIVE_SCHEMAS = {
    "state": "3514187effc4a57f23f225f4ddf9af6d6b6c2d916bd6fee5d57e46b9ca718d54",
    "projection": "d96d7011e4abb80c78e9a5b2fdfa02a2f2132242039b67c119e38b0e6045488e",
}
# Migrations 60/61 add thread-scoped feedback and read state. The preceding
# schema and all earlier migration checksums are unchanged.
STATE_SCHEMA_UPGRADES = {
    "c9926f802f373d492219b83f560072fdec8ad299bfff58118b5d7bbc014f462a",
    "775759d3b4bf22114c958578473d0e273af0b5d8eefc564b5a0685700d404be8",
}


class HistorySchemaCompatibilityError(ValueError):
    """Native history needs a reviewed adapter, not a different user message."""


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _in(column: str, values: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    parameters = tuple(dict.fromkeys(values))
    return (_quote(column) + " IN (" + ",".join("?" for _ in parameters) + ")", parameters)


def _schema(connection: sqlite3.Connection, kind: str) -> list[tuple[str, str, str, str | None]]:
    if kind not in NATIVE_SCHEMAS:
        raise ValueError(f"Unknown Codex history database kind: {kind}")
    rows = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY rowid").fetchall()
    allowed = STATE_TABLES if kind == "state" else PROJECTION_TABLES
    unsupported = [name for typ, name, _, sql in rows
                   if typ == "table" and (name not in allowed or (sql and "CREATE VIRTUAL TABLE" in sql.upper()))]
    if unsupported:
        raise HistorySchemaCompatibilityError("Unsupported Codex history schema; no global database was copied: " + ", ".join(unsupported))
    if {"version", "description", "installed_on", "success", "checksum", "execution_time"} <= set(_columns(connection, "_sqlx_migrations")):
        migrations = [(version, bytes(checksum).hex()) for version, checksum in connection.execute(
            "SELECT version,checksum FROM _sqlx_migrations ORDER BY version")]
        fingerprint = hashlib.sha256(json.dumps([sorted(rows, key=lambda row: (row[0], row[1])), migrations],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if fingerprint != NATIVE_SCHEMAS[kind] and not (kind == "state" and fingerprint in STATE_SCHEMA_UPGRADES):
            raise HistorySchemaCompatibilityError("Unsupported Codex history schema or migration; original history was not changed")
    supported_foreign_keys = {
        "threads": {("projects", "project_id", "id"), ("thread_sections", "thread_section_id", "id")},
        "thread_dynamic_tools": {("threads", "thread_id", "id")},
        "thread_attachments": {("threads", "thread_id", "id")},
        "guardian_review_feedback": {("threads", "thread_id", "id")},
        "project_roots": {("projects", "project_id", "id")},
    }
    for typ, name, _, _ in rows:
        if typ == "table" and any((row[2], row[3], row[4]) not in supported_foreign_keys.get(name, set())
                                  for row in connection.execute(f"PRAGMA foreign_key_list({_quote(name)})")):
            raise HistorySchemaCompatibilityError(f"Unsupported Codex history dependency: {name}")
    return rows


def _columns(connection: sqlite3.Connection, table: str, *, generated: bool = False) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_xinfo({_quote(table)})") if row[6] == 0 or generated]


def _selectors(connection: sqlite3.Connection, kind: str, identifiers: tuple[str, ...],
               rollout_paths: tuple[str, ...]) -> dict[str, tuple[str, tuple[str, ...]]]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    scopes = {table: ("0", ()) for table in tables}
    if "_sqlx_migrations" in tables:
        scopes["_sqlx_migrations"] = ("1", ())
    if kind == "projection":
        for table in tables - {"_sqlx_migrations", "sqlite_sequence"}:
            if "thread_id" not in _columns(connection, table):
                raise HistorySchemaCompatibilityError(f"Codex projection table has no thread identity: {table}")
            scopes[table] = _in("thread_id", identifiers)
        return scopes
    if "threads" not in tables:
        raise HistorySchemaCompatibilityError("Codex state database has no threads table")
    thread_columns = _columns(connection, "threads")
    where, parameters = _in("id", identifiers)
    if "rollout_path" in thread_columns:
        paths_where, paths_parameters = _in("rollout_path", rollout_paths)
        where, parameters = f"({where} OR {paths_where})", parameters + paths_parameters
    scopes["threads"] = (where, parameters)
    selected = tuple(row[0] for row in connection.execute(f"SELECT id FROM threads WHERE {where}", parameters))
    for table in ("thread_dynamic_tools", "thread_attachments", "guardian_review_feedback", "thread_read_receipts"):
        if table in tables:
            scopes[table] = _in("thread_id", selected)
    if "thread_spawn_edges" in tables:
        parent, args = _in("parent_thread_id", selected)
        child, child_args = _in("child_thread_id", selected)
        scopes["thread_spawn_edges"] = (f"{parent} AND {child}", args + child_args)
    for column, table in (("thread_section_id", "thread_sections"), ("project_id", "projects")):
        if column in thread_columns and table in tables:
            values = tuple(row[0] for row in connection.execute(
                f"SELECT DISTINCT {_quote(column)} FROM threads WHERE {where} AND {_quote(column)} IS NOT NULL", parameters))
            scopes[table] = _in("id", values)
            if table == "projects" and "project_roots" in tables:
                scopes["project_roots"] = _in("project_id", values)
    return scopes


def _budget(connection: sqlite3.Connection, schema: list, scopes: dict) -> int:
    page = connection.execute("PRAGMA page_size").fetchone()[0]
    # Budget freshly packed rows and every index, not source freelists or WAL.
    # Include record headers, sparse B-tree pages and overflow-page tails.
    total = page * 2
    for typ, name, table, _ in schema:
        if typ not in ("table", "index") or name == "sqlite_sequence":
            continue
        columns = (_columns(connection, table, generated=True) if typ == "table" else
                   [row[2] for row in connection.execute(f"PRAGMA index_xinfo({_quote(name)})") if row[1] >= 0])
        if typ == "index" and any(row[1] == -2 for row in connection.execute(f"PRAGMA index_xinfo({_quote(name)})")):
            raise HistorySchemaCompatibilityError("Cannot budget an unsupported Codex expression index")
        size = " + ".join(f"COALESCE(length(CAST({_quote(column)} AS BLOB)),0)" for column in columns) or "0"
        size = f"({size} + {128 + 9 * len(columns)})"
        where, args = scopes[table]
        amount = connection.execute(
            f"SELECT COALESCE(SUM(2 * {size} + CASE WHEN {size} > ? THEN ? ELSE 0 END),0) "
            f"FROM {_quote(table)} WHERE {where}", (page - 128, page, *args)).fetchone()[0]
        total += page * 2 + int(amount)
    return total


def scoped_database_bytes(source: Path, kind: str, identifiers: tuple[str, ...],
                          rollout_paths: tuple[str, ...] = ()) -> int:
    if not source.exists():
        return 0
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        schema = _schema(connection, kind)
        return _budget(connection, schema, _selectors(connection, kind, identifiers, rollout_paths))


def copy_scoped_database(source: Path, destination: Path, kind: str, identifiers: tuple[str, ...],
                         rollout_paths: tuple[str, ...] = ()) -> int:
    """Export from one read transaction, without backing up unrelated pages."""
    if not source.exists():
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader, \
                closing(sqlite3.connect(temporary)) as writer:
            reader.execute("BEGIN")
            schema = _schema(reader, kind)
            scopes = _selectors(reader, kind, identifiers, rollout_paths)
            bound = _budget(reader, schema, scopes)
            for pragma in ("page_size", "encoding", "user_version", "application_id"):
                value = reader.execute("PRAGMA " + pragma).fetchone()[0]
                writer.execute("PRAGMA " + pragma + " = " + (str(value) if isinstance(value, int) else "'" + value + "'"))
            for typ, name, _, sql in schema:
                if typ == "table" and name != "sqlite_sequence":
                    writer.execute(sql)
            for table, (where, args) in scopes.items():
                if table == "sqlite_sequence":
                    continue
                names = _columns(reader, table)
                definition = next(sql for typ, name, _, sql in schema if typ == "table" and name == table)
                if not re.search(r"\bWITHOUT\s+ROWID\b", definition, re.IGNORECASE) and "rowid" not in names:
                    names = ["rowid", *names]
                columns = ",".join(map(_quote, names))
                rows = reader.execute(f"SELECT {columns} FROM {_quote(table)} WHERE {where}", args)
                placeholders = ",".join("?" for _ in names)
                writer.executemany(f"INSERT INTO {_quote(table)} ({columns}) VALUES ({placeholders})", rows)
            if "sqlite_sequence" in scopes:
                if not writer.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_sequence'").fetchone():
                    # SQLite can retain this internal table after its last
                    # AUTOINCREMENT table was removed by a native migration.
                    writer.execute("CREATE TABLE pcr_sequence_bootstrap (id INTEGER PRIMARY KEY AUTOINCREMENT)")
                    writer.execute("DROP TABLE pcr_sequence_bootstrap")
                writer.execute("DELETE FROM sqlite_sequence")
                writer.executemany("INSERT INTO sqlite_sequence VALUES (?,?)", reader.execute("SELECT name,seq FROM sqlite_sequence"))
            # Install triggers last: exporting existing rows is not a native
            # insert event, and must not change timestamps or projection state.
            for typ, _, _, sql in schema:
                if typ in ("index", "view", "trigger") and sql:
                    writer.execute(sql)
            writer.commit()
            if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or writer.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Selected Codex history failed SQLite integrity validation")
        if temporary.stat().st_size > bound:
            raise ValueError("Selected Codex database exceeded its storage budget")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        return bound
    finally:
        temporary.unlink(missing_ok=True)
