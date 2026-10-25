from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
import time
import logging
from contextlib import contextmanager
from typing import Any, Callable, Mapping

from .models import AgentResult
from .workspace import WorkspaceSyncPreflightError, cleanup_workspace_copies, sync_best_workspace_back


class FinalizationStageError(RuntimeError):
    """Preserves the failed phase so callers can recover sync and cleanup correctly."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(str(cause))


@dataclass(frozen=True)
class CandidateFinalizationOutcome:
    session_promotion: dict[str, Any] | None
    workspaces_deleted: bool
    codex_homes_deleted: bool
    cleanup_error: str = ""


def _promotion_payload(
    value: object,
    *,
    result: AgentResult,
    workspace: Path,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return dict(asdict(value))
    return {
        "session_id": result.codex_thread_id or "",
        "workspace": str(workspace),
    }


def finalize_candidate_workspace(
    result: AgentResult,
    *,
    candidate_workspace: Path | None,
    workspace: Path,
    workspaces_root: Path | None,
    promote_session: Callable[[AgentResult, Path], object | None],
    sync_back: bool = True,
    keep_workspaces: bool = False,
    require_resume: bool = False,
    already_synced: bool = False,
    existing_promotion: Mapping[str, Any] | None = None,
    sync_workspace: Callable[[Path, Path], None] | None = None,
    cleanup_workspaces: Callable[[Path, Path], None] | None = None,
    cleanup_codex_homes: Callable[[], bool] | None = None,
    after_sync: Callable[[], None] | None = None,
    after_promotion: Callable[[dict[str, Any] | None], None] | None = None,
    before_cleanup: Callable[[], None] | None = None,
    phase_callback: Callable[[str, float | None], None] | None = None,
) -> CandidateFinalizationOutcome:
    """Apply one Agent candidate using the same ordered flow for every frontend."""

    workspace = workspace.expanduser().resolve()
    sync_workspace = sync_workspace or sync_best_workspace_back
    cleanup_workspaces = cleanup_workspaces or cleanup_workspace_copies

    def report_phase(name: str, seconds: float | None) -> None:
        if phase_callback is not None:
            try:
                phase_callback(name, seconds)
            except Exception:
                logging.getLogger(__name__).exception("Finalization timing publication failed")

    @contextmanager
    def stage(name: str):
        report_phase(name, None)
        started = time.monotonic()
        try:
            yield
        finally:
            report_phase(name, time.monotonic() - started)

    promotion = dict(existing_promotion) if existing_promotion is not None else None
    if require_resume and not result.codex_thread_id:
        raise FinalizationStageError(
            "promotion",
            RuntimeError("the selected Agent has no resumable session"),
        )

    if not already_synced:
        if sync_back:
            if candidate_workspace is None:
                raise FinalizationStageError(
                    "sync",
                    RuntimeError("the selected Agent workspace is unavailable"),
                )
            try:
                if candidate_workspace.resolve() != workspace:
                    with stage("workspace_sync"):
                        sync_workspace(candidate_workspace, workspace)
            except WorkspaceSyncPreflightError as exc:
                raise FinalizationStageError("sync_preflight", exc) from exc
            except Exception as exc:
                raise FinalizationStageError("sync", exc) from exc
            if after_sync is not None:
                after_sync()

    # Confirming a recovered file sync does not confirm session promotion.
    promotion_ready = bool(
        str((promotion or {}).get("session_id") or "").strip()
        and not str((promotion or {}).get("error") or "").strip()
    )
    needs_promotion = sync_back and (not already_synced or not promotion_ready)
    if needs_promotion:
        try:
            with stage("session_promotion"):
                promoted = promote_session(result, workspace)
            promotion = _promotion_payload(
                promoted,
                result=result,
                workspace=workspace,
            )
            promotion_error = str((promotion or {}).get("error") or "").strip()
            if require_resume and (promotion is None or promotion_error):
                raise RuntimeError(
                    promotion_error or "the selected Agent session could not be promoted"
                )
        except Exception as exc:
            if require_resume:
                raise FinalizationStageError("promotion", exc) from exc
            promotion = {
                "session_id": result.codex_thread_id or "",
                "workspace": str(workspace),
                "error": str(exc),
            }
    if after_promotion is not None and (not already_synced or needs_promotion):
        after_promotion(promotion)

    deleted = bool(
        not keep_workspaces
        and (workspaces_root is None or not workspaces_root.exists())
    )
    codex_homes_deleted = False
    cleanup_error = ""
    if not keep_workspaces:
        if before_cleanup is not None:
            before_cleanup()
        try:
            with stage("finalization_cleanup"):
                if workspaces_root is not None and workspaces_root.exists():
                    cleanup_workspaces(workspace, workspaces_root)
                deleted = workspaces_root is None or not workspaces_root.exists()
                if cleanup_codex_homes is not None:
                    codex_homes_deleted = bool(cleanup_codex_homes())
        except Exception as exc:
            # Adoption is already committed. Housekeeping must not turn a
            # successful sync into a failed send, or cause that sync to repeat.
            deleted = workspaces_root is None or not workspaces_root.exists()
            cleanup_error = str(exc)

    return CandidateFinalizationOutcome(
        session_promotion=promotion,
        workspaces_deleted=deleted,
        codex_homes_deleted=codex_homes_deleted,
        cleanup_error=cleanup_error,
    )
