import base64
import hashlib
import shutil
from pathlib import Path

import pytest

from parallel_codex_runner_core.plugin.artifacts import ArtifactError, ArtifactStore
from parallel_codex_runner_core.plugin.state import ManagedRun


@pytest.fixture
def preview(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "runs" / "20261003_120000"
    candidate = root / "workspaces" / "agent_001"
    candidate.mkdir(parents=True)
    run = ManagedRun(run_id="preview-test", prompt="test", workspace=str(source),
                     run_root=str(root), artifact_token="test-only", config={"run_base": str(root.parent)})
    store = ArtifactStore()
    store.write_marker(run)
    before, after = b"\x89PNG\0old-image", b"\x89PNG\0new-image"
    (source / "figure.png").write_bytes(before)
    (candidate / "figure.png").write_bytes(after)
    return store, run, source, candidate, before, after


def content(store, run, metadata, side, *, chunk=4):
    version = metadata[side]
    data, offset = b"", 0
    while True:
        page = store.file_preview(run, 1, "figure.png", live=False, side=side,
                                  file_id=version["file_id"], offset=offset, max_bytes=chunk)
        value = base64.b64decode(page["content_base64"])
        assert hashlib.sha256(value).hexdigest() == page["chunk_sha256"]
        assert page["offset"] == offset and page["next_offset"] == offset + len(value)
        data += value
        offset = page["next_offset"]
        if not page["has_more"]:
            break
    assert hashlib.sha256(data).hexdigest() == version["sha256"]
    return data


def test_preserves_binary_versions_before_cleanup_and_later_source_edits(preview):
    store, run, source, candidate, before, after = preview
    store.persist_diff(run, 1)
    shutil.rmtree(candidate)
    (source / "figure.png").write_bytes(b"later edits")
    metadata = store.file_preview(run, 1, "figure.png", live=False)
    assert content(store, run, metadata, "before") == before
    assert content(store, run, metadata, "after") == after
    assert metadata["content_type"] == "image/png"


@pytest.mark.parametrize("ext", ["docx", "pdf", "svg", "gif", "webp", "heic"])
def test_retains_other_visual_document_formats(preview, ext):
    store, run, source, candidate, before, after = preview
    (source / f"report.{ext}").write_bytes(before)
    (candidate / f"report.{ext}").write_bytes(after)
    store.persist_diff(run, 1)
    metadata = store.file_preview(run, 1, f"report.{ext}", live=False)
    assert metadata["before"]["file_id"] == hashlib.sha256(before).hexdigest()
    assert metadata["after"]["file_id"] == hashlib.sha256(after).hexdigest()


def test_added_deleted_and_empty_versions_are_distinct(preview):
    store, run, source, candidate, *_ = preview
    (candidate / "added.png").write_bytes(b"")
    (source / "deleted.png").write_bytes(b"removed")
    store.persist_diff(run, 1)
    added = store.file_preview(run, 1, "added.png", live=False)
    deleted = store.file_preview(run, 1, "deleted.png", live=False)
    assert added["before"]["state"] == "absent" and added["after"]["size_bytes"] == 0
    assert deleted["after"]["state"] == "absent" and deleted["before"]["state"] == "available"


def test_live_pages_keep_one_immutable_capture_and_do_not_poison_final_versions(preview):
    store, run, _, candidate, _, after = preview
    live = store.file_preview(run, 1, "figure.png", live=True)
    assert not store.diff_path(run, 1).exists()
    (candidate / "figure.png").write_bytes(b"\0final")
    assert content(store, run, live, "after") == after
    store.persist_diff(run, 1)
    final = store.file_preview(run, 1, "figure.png", live=False)
    assert content(store, run, final, "after") == b"\0final"


def test_legacy_binary_patch_cannot_prove_current_trees_are_historical(preview):
    store, run, source, candidate, *_ = preview
    store.persist_diff(run, 1)
    store._preview_index(run, 1).unlink()
    (source / "figure.png").write_bytes(b"\0wrong-before")
    (candidate / "figure.png").write_bytes(b"\0wrong-after")
    metadata = store.file_preview(run, 1, "figure.png", live=False)
    assert metadata["before"]["state"] == metadata["after"]["state"] == "unavailable"


@pytest.mark.parametrize("path", ["../figure.png", "/figure.png", "a/../figure.png", "./figure.png", "secret.txt"])
def test_rejects_unsafe_or_unsupported_paths(preview, path):
    store, run, *_ = preview
    with pytest.raises(ArtifactError):
        store.file_preview(run, 1, path, live=True)


def test_rejects_symlinked_working_file_and_parent(preview, tmp_path):
    store, run, _, candidate, *_ = preview
    external = tmp_path / "external"
    external.mkdir()
    (external / "hidden.png").write_bytes(b"secret")
    (candidate / "linked").symlink_to(external, target_is_directory=True)
    with pytest.raises(ArtifactError):
        store.file_preview(run, 1, "linked/hidden.png", live=True)
    (candidate / "figure.png").unlink()
    (candidate / "figure.png").symlink_to(external / "hidden.png")
    with pytest.raises(ArtifactError):
        store.file_preview(run, 1, "figure.png", live=True)


def test_rejects_symlinked_retained_blob_and_cross_agent_access(preview, tmp_path):
    store, run, *_ = preview
    store.persist_diff(run, 1)
    metadata = store.file_preview(run, 1, "figure.png", live=False)
    digest = metadata["before"]["file_id"]
    with pytest.raises(ArtifactError):
        store.file_preview(run, 2, "figure.png", live=False, side="before", file_id=digest)
    target = store._preview_directory(run, 1) / digest
    target.unlink()
    external = tmp_path / "secret"
    external.write_bytes(b"secret")
    target.symlink_to(external)
    with pytest.raises(ArtifactError):
        content(store, run, metadata, "before")


def test_source_mode_uses_verified_original_baseline(preview):
    store, run, source, _, before, _ = preview
    baseline = Path(run.run_root) / "workspaces" / "baseline"
    shutil.copytree(source, baseline)
    (source / "figure.png").write_bytes(b"\0source-mode-after")
    run.config["workspace_mode"] = "source"
    store.write_marker(run)
    store.persist_diff(run, 1)
    metadata = store.file_preview(run, 1, "figure.png", live=False)
    assert content(store, run, metadata, "before") == before
    assert content(store, run, metadata, "after") == b"\0source-mode-after"
