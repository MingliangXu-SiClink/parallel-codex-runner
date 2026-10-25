import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core import app
from parallel_codex_runner_core.scoped_history import copy_scoped_database, scoped_database_bytes
from . import test_prepared_history as fixtures


class ScopedHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home, self.rollout = fixtures.PreparedHistoryTests().fixture(self.root, paginated=True)

    def projection(self):
        path = self.home / "thread_history_1.sqlite"
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript("""
                CREATE TABLE _sqlx_migrations (version INTEGER PRIMARY KEY, checksum BLOB);
                INSERT INTO _sqlx_migrations VALUES (1, X'1234');
                CREATE TABLE thread_items (thread_id TEXT, item_id TEXT, item_json TEXT,
                    PRIMARY KEY(thread_id,item_id));
                CREATE INDEX item_lookup ON thread_items(thread_id);
                CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY,
                    next_rollout_byte_offset INTEGER, next_rollout_ordinal INTEGER);
                CREATE TRIGGER projection_cleanup AFTER DELETE ON thread_history_projection_state
                    BEGIN DELETE FROM thread_items WHERE thread_id=OLD.thread_id; END;
                PRAGMA user_version=17;
                PRAGMA application_id=19;
            """)
            connection.execute("INSERT INTO thread_items VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','selected',?)", ("exact 中文" * 100,))
            connection.execute("INSERT INTO thread_items VALUES ('other','unrelated',?)", ("private-other" * 100000,))
            connection.execute("INSERT INTO thread_history_projection_state VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',?,3)", (self.rollout.stat().st_size,))
            connection.commit()
        return path

    def test_export_keeps_schema_migration_identity_and_only_selected_cache(self):
        path = self.projection()
        target = self.root / "compact.sqlite"
        before = path.read_bytes()
        bound = copy_scoped_database(path, target, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        self.assertLess(target.stat().st_size, path.stat().st_size / 10)
        self.assertLessEqual(target.stat().st_size, bound)
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as copied:
            self.assertEqual(source.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall(),
                             copied.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall())
            self.assertEqual(copied.execute("SELECT * FROM _sqlx_migrations").fetchall(), [(1, b"\x12\x34")])
            self.assertEqual(copied.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertEqual(copied.execute("PRAGMA application_id").fetchone()[0], 19)
            self.assertEqual(copied.execute("SELECT DISTINCT thread_id FROM thread_items").fetchall(), [("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)])
            copied.execute("DELETE FROM thread_history_projection_state")
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM thread_items").fetchone()[0], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_estimate_does_not_scale_with_unrelated_rows_wal_or_freelist(self):
        path = self.projection()
        before = scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executemany("INSERT INTO thread_items VALUES (?,?,?)",
                                   [("other", str(i), "unrelated" * 10000) for i in range(100)])
            connection.commit()
            self.assertEqual(scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)), before)
            connection.execute("DELETE FROM thread_items WHERE thread_id='other'")
            connection.commit()
        self.assertEqual(scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)), before)

    def test_read_transaction_includes_selected_uncheckpointed_wal(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO thread_items VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','wal','committed')")
            connection.commit()
            copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
            with closing(sqlite3.connect(self.root / "copy")) as copied:
                self.assertEqual(copied.execute("SELECT item_json FROM thread_items WHERE item_id='wal'").fetchone()[0], "committed")

    def test_implicit_rowids_are_preserved_not_renumbered(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("INSERT INTO thread_items (rowid,thread_id,item_id,item_json) VALUES (100000,'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','sparse','exact')")
            connection.commit()
        copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        with closing(sqlite3.connect(self.root / "copy")) as connection:
            self.assertEqual(connection.execute("SELECT rowid FROM thread_items WHERE item_id='sparse'").fetchone()[0], 100000)

    def test_native_migration_can_leave_an_empty_sqlite_sequence(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE removed_native_table (id INTEGER PRIMARY KEY AUTOINCREMENT)")
            connection.execute("DROP TABLE removed_native_table")
        copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        with closing(sqlite3.connect(self.root / "copy")) as connection:
            self.assertEqual(connection.execute("SELECT * FROM sqlite_sequence").fetchall(), [])
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_schema WHERE name='pcr_sequence_bootstrap'").fetchone())

    def test_registry_preserves_only_reachable_rows_tools_and_foreign_keys(self):
        state = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(state)) as connection:
            connection.executescript("""
                CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT);
                CREATE TABLE project_roots (project_id TEXT REFERENCES projects(id), path TEXT);
                CREATE TABLE thread_sections (id TEXT PRIMARY KEY, name TEXT);
                ALTER TABLE threads ADD COLUMN project_id TEXT REFERENCES projects(id);
                ALTER TABLE threads ADD COLUMN thread_section_id TEXT REFERENCES thread_sections(id);
                CREATE TABLE thread_dynamic_tools (thread_id TEXT REFERENCES threads(id), name TEXT);
                CREATE TABLE thread_attachments (id TEXT PRIMARY KEY, thread_id TEXT REFERENCES threads(id),
                    attachment_type TEXT, identity_key TEXT, payload TEXT);
                CREATE TABLE remote_control_enrollments (secret TEXT);
                INSERT INTO projects VALUES ('p','keep'), ('q','other');
                INSERT INTO project_roots VALUES ('p','/keep'), ('q','/other');
                INSERT INTO thread_sections VALUES ('s','keep'), ('t','other');
                INSERT INTO thread_dynamic_tools VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','keep');
                INSERT INTO remote_control_enrollments VALUES ('must not transfer');
                UPDATE threads SET project_id='p',thread_section_id='s';
                INSERT INTO threads (id,cwd,rollout_path) VALUES ('other','/outside','/outside/history');
                INSERT INTO thread_dynamic_tools VALUES ('other','remove');
                INSERT INTO thread_attachments VALUES ('a','aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa','pull_request','keep','{"state":"open"}');
                INSERT INTO thread_attachments VALUES ('b','other','pull_request','drop','{"state":"closed"}');
            """)
        target = self.root / "state"
        copy_scoped_database(state, target, "state", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",), (str(self.rollout),))
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(connection.execute("SELECT id FROM threads").fetchall(), [("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)])
            self.assertEqual(connection.execute("SELECT name FROM thread_dynamic_tools").fetchall(), [("keep",)])
            self.assertEqual(connection.execute("SELECT * FROM thread_attachments").fetchall(),
                             [("a", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "pull_request", "keep", '{"state":"open"}')])
            self.assertEqual(connection.execute("SELECT id FROM projects").fetchall(), [("p",)])
            self.assertEqual(connection.execute("SELECT id FROM thread_sections").fetchall(), [("s",)])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM remote_control_enrollments").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unknown_dependency_in_an_existing_table_is_rejected(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("ALTER TABLE thread_items ADD COLUMN other_thread TEXT REFERENCES thread_items(item_id)")
        with self.assertRaisesRegex(ValueError, "Unsupported Codex history dependency"):
            copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))

    def test_native_contract_rejects_changed_known_table_trigger_or_migration(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            for name in ("description", "installed_on", "success", "execution_time"):
                connection.execute(f"ALTER TABLE _sqlx_migrations ADD COLUMN {name} TEXT")
            schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall()
            migrations = [(version, bytes(checksum).hex()) for version, checksum in connection.execute(
                "SELECT version,checksum FROM _sqlx_migrations ORDER BY version")]
            fingerprint = hashlib.sha256(json.dumps([schema, migrations], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with mock.patch("parallel_codex_runner_core.scoped_history.NATIVE_SCHEMAS", {"state": "unused", "projection": fingerprint}):
            copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TRIGGER additional_native_trigger AFTER INSERT ON thread_items BEGIN SELECT 1; END")
            with self.assertRaisesRegex(ValueError, "Unsupported Codex history schema or migration"):
                scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DROP TRIGGER additional_native_trigger")
                connection.execute("UPDATE _sqlx_migrations SET checksum=X'5678'")
                connection.commit()
            with self.assertRaisesRegex(ValueError, "Unsupported Codex history schema or migration"):
                copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("UPDATE _sqlx_migrations SET checksum=X'1234'")
                connection.execute("ALTER TABLE thread_items ADD COLUMN new_native_dependency TEXT")
                connection.commit()
            with self.assertRaisesRegex(ValueError, "Unsupported Codex history schema or migration"):
                copy_scoped_database(path, self.root / "copy", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))

    def test_selected_schema_failure_is_atomic_and_never_falls_back_to_whole_database(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE unknown_native_dependency (value TEXT)")
        destination = self.root / "untouched"
        destination.write_bytes(b"previous generation")
        with self.assertRaisesRegex(ValueError, "Unsupported Codex history schema"):
            copy_scoped_database(path, destination, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        self.assertEqual(destination.read_bytes(), b"previous generation")
        self.assertEqual(list(self.root.glob(".*.tmp")), [])
        with self.assertRaisesRegex(ValueError, "Unsupported Codex history schema"):
            scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))

    def test_estimated_bound_covers_compact_unicode_blob_and_index_storage(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.executemany("INSERT INTO thread_items VALUES ('aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',?,?)",
                                   [(str(i), "中文" * i) for i in range(1000)])
            connection.commit()
        bound = scoped_database_bytes(path, "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        self.assertEqual(copy_scoped_database(path, self.root / "compact", "projection", ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)), bound)
        self.assertLessEqual((self.root / "compact").stat().st_size, bound)

    def test_paged_source_never_performs_a_global_backup_or_changes_raw_history(self):
        self.projection()
        original = self.rollout.read_bytes()
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as connection:
            connection.execute("INSERT INTO threads VALUES ('unrelated','/other','/external','paginated')")
            connection.commit()
        source = app.AgentCodexSource(self.home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", self.root / "shared")
        self.addCleanup(source.close)
        homes = [self.root / f"agent_{i}/codex_home" for i in range(2)]
        with mock.patch.object(app, "copy_sqlite_database", side_effect=AssertionError("global backup")):
            for home in homes:
                app.prepare_agent_codex_home(self.home, home, self.root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
        for home in homes:
            with closing(sqlite3.connect(home / "state_5.sqlite")) as connection:
                self.assertEqual(connection.execute("SELECT id FROM threads").fetchall(), [("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)])
                isolated = Path(connection.execute("SELECT rollout_path FROM threads").fetchone()[0])
                self.assertEqual(isolated.read_bytes(), original)
        with closing(sqlite3.connect(homes[0] / "thread_history_1.sqlite")) as connection:
            connection.execute("DELETE FROM thread_items")
            connection.commit()
        with closing(sqlite3.connect(homes[1] / "thread_history_1.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_items").fetchone()[0], 1)

    def test_projection_ahead_of_frozen_rollout_is_rebuilt_only_in_private_copy(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE thread_history_projection_state SET next_rollout_byte_offset=1000000")
            connection.commit()
        source = app.AgentCodexSource(self.home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", self.root / "shared")
        self.addCleanup(source.close)
        private = self.root / "agent/codex_home"
        app.prepare_agent_codex_home(self.home, private, self.root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
        with closing(sqlite3.connect(private / path.name)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_history_projection_state").fetchone()[0], 0)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("SELECT next_rollout_byte_offset FROM thread_history_projection_state").fetchone()[0], 1000000)

    def test_items_missing_their_turn_are_rebuilt_despite_an_eof_checkpoint(self):
        path = self.projection()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("ALTER TABLE thread_items ADD COLUMN turn_id TEXT")
            connection.execute("CREATE TABLE thread_turns (thread_id TEXT,turn_id TEXT,PRIMARY KEY(thread_id,turn_id))")
            connection.execute("UPDATE thread_items SET turn_id='missing-turn' WHERE thread_id='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'")
            connection.commit()
        source = app.AgentCodexSource(self.home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", self.root / "shared")
        self.addCleanup(source.close)
        private = self.root / "agent/codex_home"
        original = self.rollout.read_bytes()
        app.prepare_agent_codex_home(self.home, private, self.root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
        self.assertEqual(source.metrics["projection_rebuild_threads"], {"aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa": "missing_turn"})
        with closing(sqlite3.connect(private / path.name)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_items").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_history_projection_state").fetchone()[0], 0)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_items WHERE thread_id='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'").fetchone()[0], 1)
        self.assertEqual(self.rollout.read_bytes(), original)

    def test_source_changes_between_database_snapshot_and_publication_are_rejected(self):
        self.projection()
        source = app.AgentCodexSource(self.home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", self.root / "shared")
        self.addCleanup(source.close)
        export = app.copy_scoped_database

        def changed_source(*args, **kwargs):
            result = export(*args, **kwargs)
            if args[2] == "projection":
                with self.rollout.open("ab") as stream:
                    stream.write(b'{"ordinal":3,"type":"event_msg","payload":{"type":"token_count"}}\n')
            return result

        private = self.root / "agent/codex_home"
        with mock.patch.object(app, "copy_scoped_database", side_effect=changed_source):
            with self.assertRaisesRegex(ValueError, "selected history changed|source history changed"):
                app.prepare_agent_codex_home(self.home, private, self.root / "workspace", "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", source)
        self.assertFalse(source._ready)
        self.assertFalse((private / "state_5.sqlite").exists())

    def test_first_projection_import_also_excludes_unrelated_threads(self):
        self.projection()
        destination = self.root / "adopted"
        app.import_history_projection(self.home, destination, ("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",))
        with closing(sqlite3.connect(destination / "thread_history_1.sqlite")) as connection:
            self.assertEqual(connection.execute("SELECT DISTINCT thread_id FROM thread_items").fetchall(), [("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",)])

    def test_private_resume_home_and_shared_baseline_are_budgeted_not_global_home(self):
        self.projection()
        global_home = self.root / "global"
        global_home.mkdir()
        (global_home / "config.toml").write_text("model='example'\n")
        (global_home / "state_5.sqlite").write_bytes(b"not the selected registry")
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "small").write_bytes(b"data")
        estimate = app.estimate_staged_run_storage(workspace, 5, 3, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", global_home, self.home)
        selected = app.estimate_resume_source_bytes(self.home, "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
        self.assertEqual(estimate.shared_source_bytes, selected)
        self.assertEqual(estimate.metadata_bytes, estimate.metadata_bytes_per_agent * 8 + selected)
        self.assertEqual(estimate.total_bytes, estimate.metadata_bytes + estimate.workspace_copies_bytes)
        self.assertLess(estimate.total_bytes, app.LARGE_RUN_STORAGE_WARNING_BYTES)


if __name__ == "__main__":
    unittest.main()
