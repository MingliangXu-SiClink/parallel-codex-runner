import subprocess
import time

from parallel_codex_runner_core.diffing import workspace_changes


def test_large_repeated_text_diff_keeps_all_context_without_blocking_reads(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    original = "".join(f"record-{index % 100}\n" for index in range(80_000))
    updated = "changed first\n" + original[original.index("\n") + 1:]
    (before / "records.txt").write_text(original)
    (after / "records.txt").write_text(updated)
    started = time.monotonic()
    change, = workspace_changes(before, after, include_full_context=True)
    elapsed = time.monotonic() - started
    assert elapsed < 1, f"Repeated-row diff took {elapsed:.3f}s"
    for patch in (change.patch, change.full_patch):
        (before / "records.txt").write_text(original)
        result = subprocess.run(["git", "apply", "--unsafe-paths", "-"], cwd=before,
                                input=patch.encode(), capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
        assert (before / "records.txt").read_text() == updated
    assert len(change.patch) < 1000
    assert change.full_patch.count("\n record-") == 79_999


def test_large_diff_preserves_crlf_and_missing_final_newline(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    original = b"row\r\n" * 12_000 + b"before"
    updated = b"row\r\n" * 12_000 + b"after"
    (before / "records.txt").write_bytes(original)
    (after / "records.txt").write_bytes(updated)
    change, = workspace_changes(before, after, include_full_context=True)
    for patch in (change.patch, change.full_patch):
        (before / "records.txt").write_bytes(original)
        result = subprocess.run(["git", "apply", "-"], cwd=before,
                                input=patch.encode(), capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
        assert (before / "records.txt").read_bytes() == updated
        assert "-before\n\\ No newline at end of file" in patch
        assert "+after\n\\ No newline at end of file" in patch
