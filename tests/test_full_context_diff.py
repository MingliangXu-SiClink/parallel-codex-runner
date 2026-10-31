import shutil
import threading
from unittest.mock import Mock
from pathlib import Path

import pytest

from parallel_codex_runner_core.plugin.artifacts import ArtifactError, ArtifactStore
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.plugin.live_diff import LiveDiffSnapshots
from parallel_codex_runner_core.plugin_runtime import PluginRunManager, PluginRunError


@pytest.fixture
def review(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = "".join(f"line {index}\n" for index in range(300))
    (source / "example.py").write_text(original)
    root = tmp_path / "runs" / "20260919_120000"
    candidate = root / "workspaces" / "agent_001"
    candidate.mkdir(parents=True)
    (candidate / "example.py").write_text(original.replace("line 150\n", "updated 150\n"))
    run = ManagedRun(run_id="full-review", prompt="test", workspace=str(source), run_root=str(root),
                     artifact_token="test-only", config={"run_base": str(root.parent)})
    store = ArtifactStore()
    store.write_marker(run)
    return store, run, source, candidate


def test_full_context_is_saved_before_candidate_cleanup(review):
    store, run, source, candidate = review
    compact = store.persist_diff(run, 1).read_text()
    assert "line 0\n" not in compact
    shutil.rmtree(candidate)
    (source / "example.py").write_text("later user edits\n")
    full = store.persist_diff(run, 1, full_context=True).read_text()
    assert " line 0\n" in full
    assert " line 299" in full
    assert "-line 150\n+updated 150" in full
    assert "later user edits" not in full


def test_legacy_patch_upgrade_requires_matching_original_trees(review):
    store, run, source, _ = review
    compact = store.persist_diff(run, 1)
    store.diff_path(run, 1, full_context=True).unlink()
    upgraded = store.persist_diff(run, 1, full_context=True)
    assert upgraded != compact
    upgraded.unlink()
    (source / "example.py").write_text("unrelated new content\n")
    assert store.persist_diff(run, 1, full_context=True) == compact


def test_full_context_artifacts_reject_symlinks(review, tmp_path):
    store, run, _, _ = review
    external = tmp_path / "external"
    external.write_text("do not change")
    store.diff_path(run, 1, full_context=True).symlink_to(external)
    with pytest.raises(ArtifactError):
        store.persist_diff(run, 1, full_context=True)
    assert external.read_text() == "do not change"


def test_cleanup_upgrades_retained_legacy_patch_without_opening_preview(review):
    store, run, _, candidate = review
    store.persist_diff(run, 1)
    store.diff_path(run, 1, full_context=True).unlink()
    run.results[1] = {"status": "success", "workspace_dir": str(candidate)}
    store.persist_successful_diffs(run)
    shutil.rmtree(candidate)
    assert " line 299" in store.persist_diff(run, 1, full_context=True).read_text()


def live_manager(review):
    store, run, _, _ = review
    run.agent_statuses[1] = "running"
    manager = object.__new__(PluginRunManager)
    manager._lock = threading.RLock()
    manager._artifacts = store
    manager._live_diffs = LiveDiffSnapshots()
    manager._reconcile_read_events = Mock(return_value=True)
    manager._require_run_locked = Mock(return_value=run)
    return manager


def test_live_pages_are_immutable_and_do_not_poison_completed_artifacts(review):
    store, run, _, candidate = review
    manager = live_manager(review)
    page = manager.get_diff(run.run_id, 1, limit=70, full_context=True)
    assert page["live"] and page["has_more"]
    assert not store.diff_path(run, 1).exists()
    (candidate / "example.py").write_text("changed again\n")
    text = page["text"]
    while page["has_more"]:
        page = manager.get_diff(run.run_id, 1, cursor=page["next_cursor"], limit=70,
                                full_context=True, snapshot_id=page["snapshot_id"])
        text += page["text"]
    assert "+updated 150" in text and "changed again" not in text
    run.results[1] = {"status": "success", "workspace_dir": str(candidate)}
    run.agent_statuses[1] = "success"
    final = manager.get_diff(run.run_id, 1, full_context=True)
    assert "+changed again" in final["text"]
    assert store.diff_path(run, 1).exists()


def test_live_snapshots_reject_other_agent_or_expired_ids(review):
    manager = live_manager(review)
    run = review[1]
    page = manager.get_diff(run.run_id, 1)
    run.agent_statuses[2] = "running"
    with pytest.raises(PluginRunError, match="expired"):
        manager.get_diff(run.run_id, 2, snapshot_id=page["snapshot_id"])
    manager._live_diffs.ttl = -1
    with pytest.raises(PluginRunError, match="expired"):
        manager.get_diff(run.run_id, 1, snapshot_id=page["snapshot_id"])


def test_live_preview_checks_workspace_marker(review):
    manager = live_manager(review)
    run = review[1]
    (Path(run.run_root) / ".pcr-plugin-run.json").unlink()
    with pytest.raises(PluginRunError, match="marker"):
        manager.get_diff(run.run_id, 1)


def test_live_diff_io_does_not_hold_the_run_manager_lock(review):
    manager = live_manager(review)
    available = []
    def build(*args, **kwargs):
        def check():
            acquired = manager._lock.acquire(timeout=1)
            available.append(acquired)
            if acquired:
                manager._lock.release()
        thread = threading.Thread(target=check)
        thread.start()
        thread.join()
        return "No workspace changes."
    manager._artifacts.live_diff = build
    manager.get_diff(review[1].run_id, 1)
    assert available == [True]


def test_completed_diff_io_does_not_hold_the_run_manager_lock(review):
    manager = live_manager(review)
    store, run, _, candidate = review
    run.agent_statuses[1] = "success"
    run.results[1] = {"status": "success", "workspace_dir": str(candidate)}
    persist = store.persist_diff
    available = []

    def build(*args, **kwargs):
        def check():
            acquired = manager._lock.acquire(timeout=0.2)
            available.append(acquired)
            if acquired:
                manager._lock.release()
        thread = threading.Thread(target=check)
        thread.start()
        thread.join()
        return persist(*args, **kwargs)

    store.persist_diff = build
    assert "+updated 150" in manager.get_diff(run.run_id, 1)["text"]
    assert available == [True]


def test_live_cache_is_bounded_and_utf8_pages_are_valid():
    from parallel_codex_runner_core.plugin.live_diff import live_diff_page
    store = LiveDiffSnapshots()
    store.max_entries = 2
    calls = []
    build = lambda: calls.append(1) or "中文变动\n"
    identifier, data = store.read(("a", 1, False), "", build)
    assert store.read(("a", 1, False), "", build)[0] == identifier
    assert len(calls) == 1
    cursor, chunks = 0, []
    while cursor < len(data):
        text, cursor = live_diff_page(data, cursor, 1)
        chunks.append(text)
    assert "".join(chunks) == "中文变动\n"
    store.read(("a", 2, False), "", build)
    store.read(("a", 3, False), "", build)
    assert len(store._snapshots) == 2
    with pytest.raises(ArtifactError):
        store.read(("a", 1, False), identifier, build)
