import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from parallel_codex_runner_core.scoped_history import (
    HistorySchemaCompatibilityError, copy_scoped_database, scoped_database_bytes,
)


def native_state(path, migration=61):
    contract = json.loads((Path(__file__).parent / "fixtures/codex-state-v61.json").read_text())
    omitted = ({"guardian_review_feedback"} if migration < 60 else set()) | (
        {"thread_read_receipts"} if migration < 61 else set())
    with closing(sqlite3.connect(path)) as db:
        for kind, name, table, sql in contract["schema"]:
            if sql and kind == "table" and name != "sqlite_sequence" and table not in omitted:
                db.execute(sql)
        # Preserve SQLite's empty sequence table left by an earlier migration.
        db.executescript("CREATE TABLE sequence_bootstrap(id INTEGER PRIMARY KEY AUTOINCREMENT); DROP TABLE sequence_bootstrap;")
        for kind, name, table, sql in contract["schema"]:
            if (sql and kind in {"index", "view", "trigger"} and table not in omitted
                    and not (migration < 59 and name == "idx_thread_attachments_identity_thread")):
                db.execute(sql)
        for version, description, success, checksum in contract["migrations"]:
            if version <= migration:
                db.execute("INSERT INTO _sqlx_migrations VALUES (?,?,'2026-10-16',?,?,0)",
                           (version, description, success, bytes.fromhex(checksum)))
        if migration >= 62:
            upgrade = json.loads((Path(__file__).parent / "fixtures/codex-state-v62-migration.json").read_text())
            for statement in upgrade["statements"]:
                db.execute(statement)
            db.execute("INSERT INTO _sqlx_migrations VALUES (?,?,'2026-10-28',1,?,0)",
                       (upgrade["version"], upgrade["description"], bytes.fromhex(upgrade["checksum"])))
        for thread in ("selected", "unrelated"):
            db.execute("""INSERT INTO threads (id,rollout_path,created_at,updated_at,source,
                model_provider,cwd,title,sandbox_policy,approval_mode) VALUES (?,?,1,1,'cli','test',?,'fixture','{}','never')""",
                (thread, f"/{thread}/history", f"/{thread}"))
            if migration >= 60:
                db.execute("INSERT INTO guardian_review_feedback VALUES (?,?,?)",
                           (f"feedback-{thread}", thread, f"{thread}-review".encode()))
            if migration >= 61:
                db.execute("INSERT INTO thread_read_receipts VALUES (?,?,?,?)",
                           (thread, f"turn-{thread}", "revision", f"last-{thread}"))
        if migration >= 62:
            db.execute("UPDATE threads SET attached_thread_parent_id='unrelated' WHERE id='selected'")
        db.commit()
    return path


@pytest.mark.parametrize("migration", [58, 59, 60, 61, 62])
def test_reviewed_native_schemas_copy_only_selected_thread_dependencies(tmp_path, migration):
    source = native_state(tmp_path / "state.sqlite", migration)
    original = source.read_bytes()
    target = tmp_path / "private.sqlite"
    bound = scoped_database_bytes(source, "state", ("selected",))
    assert copy_scoped_database(source, target, "state", ("selected",)) == bound
    assert target.stat().st_size <= bound
    with closing(sqlite3.connect(source)) as before, closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall() == before.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall()
        assert db.execute("SELECT * FROM _sqlx_migrations").fetchall() == before.execute("SELECT * FROM _sqlx_migrations").fetchall()
        assert db.execute("SELECT id FROM threads").fetchall() == [("selected",)]
        if migration >= 60:
            assert db.execute("SELECT thread_id,record FROM guardian_review_feedback").fetchall() == [("selected", b"selected-review")]
        if migration >= 61:
            assert db.execute("SELECT * FROM thread_read_receipts").fetchall() == [("selected", "turn-selected", "revision", "last-selected")]
        if migration >= 62:
            assert db.execute("SELECT attached_thread_parent_id FROM threads").fetchall() == [("unrelated",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert b"unrelated-review" not in target.read_bytes()
    assert source.read_bytes() == original


@pytest.mark.parametrize("migration", [7, 8])
def test_fresh_cli_and_desktop_projection_histories_keep_only_the_selected_thread(tmp_path, migration):
    contract = json.loads((Path(__file__).parent / "fixtures/codex-projection-v8.json").read_text())
    source = tmp_path / "projection.sqlite"
    with closing(sqlite3.connect(source)) as db:
        for kind, name, _, sql in contract["schema"]:
            if not sql:
                continue
            if kind == "table" and name == "thread_turns" and migration == 7:
                sql = sql.replace(", root_turn_id TEXT,", ",")
            db.execute(sql)
        for version, description, success, checksum in contract["migrations"]:
            if version <= migration:
                db.execute("INSERT INTO _sqlx_migrations VALUES (?,?,'2026-10-28',?,?,0)",
                           (version, description, success, bytes.fromhex(checksum)))
        for thread in ("selected", "unrelated"):
            db.execute("INSERT INTO thread_turns (thread_id,turn_id,rollout_ordinal,status) VALUES (?,?,1,'completed')",
                       (thread, f"turn-{thread}"))
        db.commit()
    original = source.read_bytes()
    target = tmp_path / "private.sqlite"
    bound = scoped_database_bytes(source, "projection", ("selected",))
    assert copy_scoped_database(source, target, "projection", ("selected",)) == bound
    with closing(sqlite3.connect(source)) as before, closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT thread_id,turn_id FROM thread_turns").fetchall() == [("selected", "turn-selected")]
        assert db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall() == before.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name").fetchall()
        assert db.execute("SELECT * FROM _sqlx_migrations").fetchall() == before.execute("SELECT * FROM _sqlx_migrations").fetchall()
    assert source.read_bytes() == original
    with closing(sqlite3.connect(source)) as db:
        db.execute("UPDATE _sqlx_migrations SET checksum=X'00' WHERE version=?", (migration,))
        db.commit()
    with pytest.raises(HistorySchemaCompatibilityError):
        copy_scoped_database(source, target, "projection", ("selected",))


@pytest.mark.parametrize("change", ["checksum", "column", "trigger", "table"])
@pytest.mark.parametrize("migration", [61, 62])
def test_future_or_changed_native_schema_stays_fail_closed_and_atomic(tmp_path, change, migration):
    source = native_state(tmp_path / "state.sqlite", migration)
    with closing(sqlite3.connect(source)) as db:
        db.execute({
            "checksum": f"UPDATE _sqlx_migrations SET checksum=X'00' WHERE version={migration}",
            "column": "ALTER TABLE thread_read_receipts ADD COLUMN new_policy TEXT",
            "trigger": "CREATE TRIGGER changed AFTER INSERT ON thread_read_receipts BEGIN SELECT 1; END",
            "table": "CREATE TABLE unknown (private TEXT)",
        }[change])
        db.commit()
    destination = tmp_path / "untouched.sqlite"
    destination.write_bytes(b"previous generation")
    with pytest.raises(HistorySchemaCompatibilityError, match="Unsupported Codex history schema"):
        copy_scoped_database(source, destination, "state", ("selected",))
    assert destination.read_bytes() == b"previous generation"
    assert not list(tmp_path.glob(".*.tmp"))


def test_parallel_exports_keep_independent_native_feedback_and_read_state(tmp_path):
    source = native_state(tmp_path / "state.sqlite")
    targets = [tmp_path / f"agent-{idx}.sqlite" for idx in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda target: copy_scoped_database(source, target, "state", ("selected",)), targets))
    with closing(sqlite3.connect(targets[0])) as db:
        db.execute("DELETE FROM guardian_review_feedback")
        db.execute("DELETE FROM thread_read_receipts")
        db.commit()
    for target in targets[1:]:
        with closing(sqlite3.connect(target)) as db:
            assert db.execute("SELECT COUNT(*) FROM guardian_review_feedback").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM thread_read_receipts").fetchone()[0] == 1
