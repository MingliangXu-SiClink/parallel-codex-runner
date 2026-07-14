from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from ..diffing import build_workspace_diff_text
from ..paths import is_relative_to
from .state import ManagedRun


RUN_ROOT_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}(?:_[0-9]{3})?$")
RUN_MARKER_NAME = ".pcr-plugin-run.json"


class ArtifactError(RuntimeError):
    pass


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class ArtifactStore:
    """Validate PCR-owned paths and preserve review artifacts before cleanup."""

    @staticmethod
    def _run_root_location(run: ManagedRun, workspace: Path) -> Path:
        raw_root = Path(run.run_root).expanduser()
        if raw_root.is_symlink() or not raw_root.exists() or not raw_root.is_dir():
            raise ArtifactError(
                f"The recorded run root is no longer a real directory: {raw_root}"
            )
        root = raw_root.resolve()
        configured_base = str(run.config.get("run_base") or "").strip()
        if not configured_base:
            raise ArtifactError("The run does not contain a verified run base")
        base = Path(configured_base).expanduser().resolve()
        if is_relative_to(base, workspace) or root.parent != base:
            raise ArtifactError(f"The run root is outside its recorded run base: {root}")
        if RUN_ROOT_PATTERN.fullmatch(root.name) is None:
            raise ArtifactError(f"Unexpected PCR run directory name: {root.name}")
        return root

    @staticmethod
    def _validate_marker(run: ManagedRun, root: Path) -> None:
        marker = root / RUN_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            raise ArtifactError(f"PCR plugin marker is missing: {marker}")
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"Cannot validate PCR plugin marker: {marker}") from exc
        expected = {
            "run_id": run.run_id,
            "workspace": run.workspace,
            "artifact_token": run.artifact_token,
        }
        if not run.artifact_token or not isinstance(marker_data, dict):
            raise ArtifactError(f"PCR plugin marker is invalid: {marker}")
        if any(marker_data.get(key) != value for key, value in expected.items()):
            raise ArtifactError(f"PCR plugin marker does not match this run: {marker}")

    def workspace(self, run: ManagedRun) -> Path:
        raw = Path(run.workspace).expanduser()
        if raw.is_symlink() or not raw.exists() or not raw.is_dir():
            raise ArtifactError(
                f"The recorded workspace is no longer a real directory: {raw}"
            )
        workspace = raw.resolve()
        if str(workspace) != run.workspace:
            raise ArtifactError(
                f"The recorded workspace now resolves somewhere else: {raw}"
            )
        return workspace

    def run_root(
        self,
        run: ManagedRun,
        *,
        require_marker: bool = True,
    ) -> Path:
        if not run.run_root:
            raise ArtifactError("Run artifacts are not available")
        workspace = self.workspace(run)
        root = self._run_root_location(run, workspace)
        if not require_marker:
            return root
        self._validate_marker(run, root)
        return root

    def write_marker(self, run: ManagedRun) -> None:
        root = self.run_root(run, require_marker=False)
        marker = root / RUN_MARKER_NAME
        if marker.is_symlink():
            raise ArtifactError(f"Refusing to replace a symlinked run marker: {marker}")
        write_json_atomic(
            marker,
            {
                "version": 2,
                "run_id": run.run_id,
                "workspace": run.workspace,
                "artifact_token": run.artifact_token,
            },
        )

    def workspaces_root(self, run: ManagedRun) -> Path:
        workspace = self.workspace(run)
        run_root = self.run_root(run)
        raw_root = run_root / "workspaces"
        if raw_root.is_symlink():
            raise ArtifactError(
                f"Candidate workspace root was replaced by a symlink: {raw_root}"
            )
        root = raw_root.resolve()
        if root.parent != run_root or is_relative_to(root, workspace):
            raise ArtifactError(f"Refusing to use unverified candidate workspaces: {root}")
        return root

    def agent_workspace(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: str | None = None,
    ) -> Path:
        workspaces_root = self.workspaces_root(run)
        raw_candidate = workspaces_root / f"agent_{agent:03d}"
        if raw_candidate.is_symlink():
            raise ArtifactError(
                f"Agent workspace was replaced by a symlink: {raw_candidate}"
            )
        candidate = raw_candidate.resolve()
        if recorded_path:
            recorded = Path(recorded_path).expanduser().resolve()
            if recorded != candidate:
                raise ArtifactError(
                    f"Recorded AGENT-{agent:03d} workspace does not match its run"
                )
        return candidate

    def agent_meta_file(self, run: ManagedRun, agent: int, name: str) -> Path:
        root = self.run_root(run)
        path = root / "meta" / f"agent_{agent:03d}" / name
        if path.is_symlink():
            raise ArtifactError(f"Agent artifact was replaced by a symlink: {path}")
        return path

    def diff_path(self, run: ManagedRun, agent: int) -> Path:
        root = self.run_root(run)
        directory = root / "plugin" / "diffs"
        if directory.is_symlink():
            raise ArtifactError(f"Diff directory was replaced by a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"agent_{agent:03d}.patch"

    def persist_diff(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: str | None = None,
        *,
        refresh: bool = False,
    ) -> Path:
        destination = self.diff_path(run, agent)
        if destination.is_symlink():
            raise ArtifactError(f"Diff file was replaced by a symlink: {destination}")
        if destination.is_file() and not refresh:
            return destination
        baseline = self.workspace(run)
        candidate = self.agent_workspace(run, agent, recorded_path)
        if not candidate.is_dir():
            raise ArtifactError(f"AGENT-{agent:03d} workspace is not available yet")
        try:
            diff = build_workspace_diff_text(baseline, candidate)
        except Exception as exc:
            raise ArtifactError(
                f"Cannot build the AGENT-{agent:03d} diff: {exc}"
            ) from exc
        try:
            write_text_atomic(destination, diff)
        except OSError as exc:
            raise ArtifactError(
                f"Cannot persist the AGENT-{agent:03d} diff: {exc}"
            ) from exc
        return destination

    def persist_successful_diffs(self, run: ManagedRun) -> Dict[int, Path]:
        paths: Dict[int, Path] = {}
        for agent, result in sorted(run.results.items()):
            if result.get("status") != "success":
                continue
            paths[agent] = self.persist_diff(
                run,
                agent,
                result.get("workspace_dir"),
            )
        return paths

    def remove_codex_homes(self, run: ManagedRun) -> bool:
        root = self.run_root(run)
        meta = root / "meta"
        if meta.is_symlink():
            raise ArtifactError(f"Metadata directory was replaced by a symlink: {meta}")
        if not meta.exists():
            return True
        for codex_home in meta.glob("agent_*/codex_home"):
            agent_dir = codex_home.parent
            if (
                re.fullmatch(r"agent_[0-9]+", agent_dir.name) is None
                or agent_dir.is_symlink()
                or agent_dir.parent.resolve() != meta.resolve()
            ):
                raise ArtifactError(f"Unsafe Agent metadata path: {agent_dir}")
            if codex_home.is_symlink():
                raise ArtifactError(f"Codex home was replaced by a symlink: {codex_home}")
            if codex_home.exists():
                shutil.rmtree(codex_home)
        return not any(meta.glob("agent_*/codex_home"))

    def remove_run_root(self, run: ManagedRun) -> bool:
        if not run.run_root:
            return True
        raw_root = Path(run.run_root).expanduser()
        if not raw_root.exists() and not raw_root.is_symlink():
            return True
        recorded_workspace = Path(run.workspace).expanduser().resolve()
        root = self._run_root_location(run, recorded_workspace)
        self._validate_marker(run, root)
        shutil.rmtree(root)
        return not root.exists()
