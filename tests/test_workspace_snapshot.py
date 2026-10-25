import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from parallel_codex_runner_core import workspace as snapshots


@pytest.fixture(params=[False, True], ids=["plain", "git"])
def workspace(tmp_path, request):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").write_text("source content\n")
    (source / "keep.txt").chmod(0o755)
    if request.param:
        for args in (
            ["init", "-q"],
            ["add", "keep.txt"],
            ["-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "base"],
        ):
            subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
        (source / "keep.txt").write_text("uncommitted source content\n")
    return source


def copy_and_check(source, tmp_path):
    destination = tmp_path / "runs" / "baseline"
    snapshots.copy_workspace(source, destination, tmp_path / "runs")
    assert (destination / "keep.txt").read_bytes() == (source / "keep.txt").read_bytes()
    assert stat.S_IMODE((destination / "keep.txt").stat().st_mode) == 0o755
    return destination


def test_directory_removed_between_enumeration_and_descent(workspace, tmp_path, monkeypatch):
    transient = workspace / "outputs" / ".work" / "processing"
    transient.mkdir(parents=True)
    (transient / "frame.png").write_bytes(b"frame")
    original = os.scandir
    removed = []

    def scandir(path):
        if not isinstance(path, int) and os.fspath(path) == str(transient):
            shutil.rmtree(transient)
            removed.append(True)
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    destination = copy_and_check(workspace, tmp_path)
    assert removed == [True]
    assert not (destination / transient.relative_to(workspace)).exists()


@pytest.mark.parametrize("phase", ["contents", "metadata"])
def test_file_removed_during_copy(workspace, tmp_path, monkeypatch, phase):
    transient = workspace / "working.tmp"
    transient.write_bytes(b"partial content")
    name = "copy2" if phase == "contents" else "copystat"
    original = getattr(shutil, name)
    removed = []

    def copy(source, destination, **kwargs):
        if os.fspath(source) == str(transient):
            transient.unlink()
            removed.append(True)
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, name, copy)
    destination = copy_and_check(workspace, tmp_path)
    assert removed == [True]
    assert not (destination / transient.name).exists()


def test_removed_directory_does_not_leave_partial_contents(workspace, tmp_path, monkeypatch):
    transient = workspace / "job"
    transient.mkdir()
    (transient / "frame.png").write_bytes(b"frame")
    original = shutil.copystat
    removed = []

    def copystat(source, destination, **kwargs):
        if os.fspath(source) == str(transient):
            shutil.rmtree(transient)
            removed.append(True)
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copystat", copystat)
    destination = copy_and_check(workspace, tmp_path)
    assert removed == [True]
    assert not (destination / transient.name).exists()


def test_symlink_removed_during_copy(workspace, tmp_path, monkeypatch):
    transient = workspace / "current"
    transient.symlink_to("keep.txt")
    original = os.readlink

    def readlink(path, **kwargs):
        if os.fspath(path) == str(transient):
            transient.unlink()
        return original(path, **kwargs)

    monkeypatch.setattr(os, "readlink", readlink)
    destination = copy_and_check(workspace, tmp_path)
    assert not (destination / transient.name).is_symlink()


def test_broken_symlinks_and_live_work_files_are_preserved(workspace, tmp_path):
    (workspace / "missing-target").symlink_to("not-there")
    (workspace / "folder-link").symlink_to("not-there", target_is_directory=True)
    work = workspace / "outputs" / ".work"
    work.mkdir(parents=True)
    (work / "still-present.txt").write_text("must not blanket-exclude .work")
    destination = copy_and_check(workspace, tmp_path)
    assert os.readlink(destination / "missing-target") == "not-there"
    assert os.readlink(destination / "folder-link") == "not-there"
    assert (destination / "outputs/.work/still-present.txt").read_text() == "must not blanket-exclude .work"


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.ENOSPC, errno.EIO])
def test_copy_errors_are_not_suppressed(workspace, tmp_path, monkeypatch, error_number):
    original = shutil.copy2

    def copy(source, destination, **kwargs):
        if os.fspath(source) == str(workspace / "keep.txt"):
            raise OSError(error_number, os.strerror(error_number), str(source))
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    with pytest.raises(OSError, match=os.strerror(error_number)):
        copy_and_check(workspace, tmp_path)
    assert (workspace / "keep.txt").exists()


def test_destination_disappearance_is_not_a_missing_source(workspace, tmp_path, monkeypatch):
    transient = workspace / "working.tmp"
    transient.touch()
    original = shutil.copy2

    def copy(source, destination, **kwargs):
        if os.fspath(source) == str(transient):
            transient.unlink()
            raise FileNotFoundError(errno.ENOENT, "destination disappeared", str(destination))
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    with pytest.raises(OSError, match="destination disappeared"):
        copy_and_check(workspace, tmp_path)


def test_recreated_source_is_not_silently_skipped(workspace, tmp_path, monkeypatch):
    transient = workspace / "working.tmp"
    transient.touch()
    original = shutil.copy2

    def copy(source, destination, **kwargs):
        if os.fspath(source) == str(transient):
            transient.unlink()
            transient.write_text("replacement")
            raise FileNotFoundError(errno.ENOENT, "source replaced", str(source))
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    with pytest.raises(OSError, match="source replaced"):
        copy_and_check(workspace, tmp_path)
    assert transient.read_text() == "replacement"


def test_missing_workspace_root_still_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshots.copy_workspace(tmp_path / "missing", tmp_path / "copy", tmp_path / "runs")


def test_workspace_removed_during_copy_still_fails(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").touch()
    original = shutil.copy2

    def copy(src, destination, **kwargs):
        shutil.rmtree(source)
        return original(src, destination, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    with pytest.raises(FileNotFoundError):
        snapshots.copy_workspace(source, tmp_path / "copy", tmp_path / "runs")


def test_run_directory_exclusion_and_existing_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").touch()
    run_base = source / "custom-runs"
    destination = run_base / "one" / "baseline"
    snapshots.copy_workspace(source, destination, run_base)
    assert (destination / "keep.txt").exists()
    assert not (destination / "custom-runs").exists()
    with pytest.raises(FileExistsError):
        snapshots.copy_workspace(source, destination, run_base)


def test_sync_back_does_not_silently_skip_a_missing_source(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    destination = tmp_path / "original"
    source.mkdir()
    destination.mkdir()
    transient = source / "changed.txt"
    transient.write_text("new")
    original = shutil.copy2

    def copy(src, dst, **kwargs):
        if os.fspath(src) == str(transient):
            transient.unlink()
        return original(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)
    with pytest.raises(FileNotFoundError):
        snapshots.sync_back_with_python(source, destination)
