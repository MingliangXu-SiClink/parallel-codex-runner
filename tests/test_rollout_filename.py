import json
import sqlite3
from pathlib import Path

import pytest

from parallel_codex_runner_core import app
from parallel_codex_runner_core.resume_history import (
    ResumeHistory, RolloutSegment, canonical_rollout_identity, resolve_resume_history,
)


SESSION = "01a0c81b-7e07-7b31-a2de-7b18a42d5db3"
PHYSICAL = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
CANONICAL = f"rollout-2026-09-29T12-22-03-{SESSION}.jsonl"


def segment(path, **payload):
    metadata = {"ordinal": 0, "type": "session_meta", "payload": {
        "id": SESSION, "timestamp": "2026-09-29T12:22:03.125Z",
        "history_mode": "paginated", **payload,
    }}
    return RolloutSegment(path, 0, metadata, 0)


def fixture(home, mode="paginated"):
    path = home / "sessions/imported" / f"rollout-{SESSION}.jsonl"
    path.parent.mkdir(parents=True)
    header = segment(path, history_mode=mode).metadata
    path.write_text(json.dumps(header) + "\n")
    with sqlite3.connect(home / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, history_mode TEXT)")
        connection.execute("INSERT INTO threads VALUES (?, '/old', ?, ?)", (SESSION, str(path), mode))
    return path


def test_short_import_preserves_physical_identity_and_creation_time():
    item = segment(Path(f"rollout-{SESSION}.jsonl"))
    assert item.destination_name() == CANONICAL
    assert ResumeHistory((item,)).physical_ids == (SESSION,)
    assert segment(item.path, timestamp="2026-09-29T20:22:03.125+08:00").destination_name() == CANONICAL


def test_native_revert_name_is_never_collapsed_to_logical_id():
    name = f"rollout-2026-10-01T00-00-00-{SESSION}_{PHYSICAL}.jsonl"
    item = segment(Path(name))
    assert item.destination_name() == name
    assert ResumeHistory((item,)).physical_ids == (PHYSICAL,)
    assert item.destination_name(SESSION) == f"rollout-2026-10-01T00-00-00-{SESSION}.jsonl"


@pytest.mark.parametrize("name,extra", [
    (f"rollout-{SESSION}.jsonl", {"id": PHYSICAL}),
    (f"rollout-{SESSION}.jsonl", {"rollout_id": PHYSICAL}),
    (f"rollout-{SESSION}.jsonl", {"timestamp": None}),
    (f"rollout-{SESSION}.jsonl", {"timestamp": "2026-09-29T12:22:03"}),
    (f"rollout-{SESSION}_{PHYSICAL}.jsonl", {}),
    (f"rollout-2026-02-30T00-00-00-{SESSION}.jsonl", {}),
    (f"backup-{SESSION}.jsonl", {}),
])
def test_unknown_names_or_ambiguous_identity_are_not_guessed(name, extra):
    with pytest.raises(ValueError, match="Cannot safely name"):
        segment(Path(name), **extra).destination_name()


def test_short_parent_cannot_be_relabelled_using_child_reference():
    parent = segment(Path(f"rollout-{SESSION}.jsonl"))
    child = segment(Path(f"rollout-2026-10-01T00-00-00-{PHYSICAL}.jsonl"),
                    history_base={"thread_id": PHYSICAL})
    with pytest.raises(ValueError, match="different physical rollout"):
        ResumeHistory((parent, child)).physical_ids


@pytest.mark.parametrize("mode,shared", [("paginated", True), ("legacy", True), ("legacy", False)])
def test_preparation_names_are_native_ready_without_changing_source(tmp_path, mode, shared):
    home, private = tmp_path / "source", tmp_path / "private"
    original = fixture(home, mode)
    before = original.read_bytes()
    source = app.AgentCodexSource(home, SESSION, tmp_path / "baseline") if shared else None
    try:
        app.prepare_agent_codex_home(home, private, tmp_path / "workspace", SESSION, source)
        result = app.find_rollout_path_for_session(private, SESSION)
        assert result.name == CANONICAL
        assert canonical_rollout_identity(result.name) == ("2026-09-29T12-22-03", SESSION, SESSION)
        if mode == "paginated":
            assert result.read_bytes() == before
        assert original.read_bytes() == before
        with sqlite3.connect(home / "state_5.sqlite") as connection:
            assert connection.execute("SELECT rollout_path FROM threads").fetchone()[0] == str(original)
    finally:
        if source:
            source.close()


@pytest.mark.parametrize("mode", ["legacy", "paginated"])
def test_adoption_normalizes_old_import_before_publishing_thread_reference(tmp_path, mode):
    source, destination = tmp_path / "source", tmp_path / "adopted"
    original = fixture(source, mode)
    before = original.read_bytes()
    destination.mkdir()
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, history_mode TEXT)")
    result = app.import_codex_session_to_workspace(destination, source, SESSION, tmp_path / "workspace")
    assert not result.error
    path = Path(result.rollout_path)
    assert path.name == CANONICAL
    assert app.find_rollout_path_for_session(destination, SESSION) == path
    if mode == "paginated":
        assert path.read_bytes() == before
        assert resolve_resume_history(destination, path).physical_ids == (SESSION,)
    assert original.read_bytes() == before


def test_preparation_rejects_conflicting_identity_before_private_database_publish(tmp_path):
    home, private = tmp_path / "source", tmp_path / "private"
    path = fixture(home)
    header = json.loads(path.read_text())
    header["payload"]["rollout_id"] = PHYSICAL
    path.write_text(json.dumps(header) + "\n")
    source = app.AgentCodexSource(home, SESSION, tmp_path / "baseline")
    try:
        with pytest.raises(ValueError, match="ambiguous physical"):
            app.prepare_agent_codex_home(home, private, tmp_path / "workspace", SESSION, source)
        assert not (private / "state_5.sqlite").exists()
    finally:
        source.close()


def test_legacy_normalization_does_not_overwrite_existing_canonical_rollout(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "adopted"
    original = fixture(source, "legacy")
    existing = destination / "sessions/imported" / CANONICAL
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already owned by another import\n")
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, history_mode TEXT)")
        connection.execute("INSERT INTO threads VALUES (?, '/existing', ?, 'legacy')", (SESSION, str(existing)))
    before = original.read_bytes()
    with pytest.raises(ValueError, match="over an existing rollout"):
        app.import_codex_session_to_workspace(destination, source, SESSION, tmp_path / "workspace")
    assert original.read_bytes() == before
    assert existing.read_bytes() == b"already owned by another import\n"
    with sqlite3.connect(destination / "state_5.sqlite") as connection:
        assert connection.execute("SELECT cwd, rollout_path FROM threads").fetchone() == ("/existing", str(existing))
