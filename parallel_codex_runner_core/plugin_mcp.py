from __future__ import annotations

import atexit
import threading
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .plugin_runtime import PluginRunManager


mcp = FastMCP(
    "parallel-codex-runner",
    instructions=(
        "Run multiple isolated Codex candidates for one task, expose their live "
        "events and diffs, and finalize only the candidate the user explicitly accepts."
    ),
)

_manager: Optional[PluginRunManager] = None
_manager_lock = threading.Lock()

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
RUN_CODE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def get_manager() -> PluginRunManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PluginRunManager()
        return _manager


def close_manager() -> None:
    global _manager
    with _manager_lock:
        manager = _manager
        _manager = None
    if manager is not None:
        manager.close()


atexit.register(close_manager)


@mcp.tool(annotations=READ_ONLY)
def pcr_health() -> Dict[str, Any]:
    """Check whether the local PCR plugin runtime is available and show its active run."""
    return get_manager().health()


@mcp.tool(annotations=READ_ONLY)
def pcr_estimate_run(
    workspace: str,
    num_agents: int = 5,
    runs_dir: Optional[str] = None,
    resume_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Estimate candidate workspace and metadata storage before starting a PCR run."""
    return get_manager().estimate(
        workspace=workspace,
        num_agents=num_agents,
        runs_dir=runs_dir,
        resume_session_id=resume_session_id,
    )


@mcp.tool(annotations=RUN_CODE)
def pcr_start_run(
    prompt: str,
    workspace: str,
    num_agents: int = 5,
    max_parallel: Optional[int] = None,
    serial: bool = False,
    recommend_by: str = "reasoning_tokens",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    runs_dir: Optional[str] = None,
    codex_bin: str = "codex",
    sync_back: bool = True,
    keep_workspaces: bool = False,
    confirm_large_run: bool = False,
) -> Dict[str, Any]:
    """Start isolated Codex candidates without changing the original workspace yet.

    Candidate workspaces remain available for review until accept, discard, or
    expiry. keep_workspaces controls whether they remain after finalization.
    A result with status confirmation_required is not a started run. Present its
    storage estimate to the user and call again with confirm_large_run only after
    explicit approval.
    """
    return get_manager().start_run(
        prompt=prompt,
        workspace=workspace,
        num_agents=num_agents,
        max_parallel=max_parallel,
        serial=serial,
        recommend_by=recommend_by,
        model=model,
        effort=effort,
        resume_session_id=resume_session_id,
        runs_dir=runs_dir,
        codex_bin=codex_bin,
        sync_back=sync_back,
        keep_workspaces=keep_workspaces,
        confirm_large_run=confirm_large_run,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_get_run(
    run_id: str,
    include_events: bool = True,
    cursor: int = 0,
    event_limit: int = 50,
) -> Dict[str, Any]:
    """Get run configuration, candidate states, recommendation, and paged new events."""
    return get_manager().get_run(
        run_id=run_id,
        include_events=include_events,
        cursor=cursor,
        event_limit=event_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_wait_for_run(
    run_id: str,
    timeout_seconds: float = 30.0,
    cursor: int = 0,
    event_limit: int = 50,
) -> Dict[str, Any]:
    """Wait up to 60 seconds for progress, then return status and events after cursor."""
    return get_manager().wait_for_run(
        run_id=run_id,
        timeout_seconds=timeout_seconds,
        cursor=cursor,
        event_limit=event_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_get_events(
    run_id: str,
    cursor: int = 0,
    limit: int = 100,
    agent: Optional[int] = None,
) -> Dict[str, Any]:
    """Read chronological PCR events with a cursor, optionally for one Agent."""
    return get_manager().get_events(
        run_id=run_id,
        cursor=cursor,
        limit=limit,
        agent=agent,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_get_agent(
    run_id: str,
    agent: int,
    include_events: bool = True,
    cursor: int = 0,
    event_limit: int = 100,
) -> Dict[str, Any]:
    """Inspect one Agent's status, final response, logs, token usage, and new events."""
    return get_manager().get_agent(
        run_id=run_id,
        agent=agent,
        include_events=include_events,
        cursor=cursor,
        event_limit=event_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_get_diff(
    run_id: str,
    agent: int,
    cursor: int = 0,
    limit: int = 20_000,
) -> Dict[str, Any]:
    """Read an Agent's complete delete-aware patch in lossless paged chunks."""
    return get_manager().get_diff(
        run_id=run_id,
        agent=agent,
        cursor=cursor,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_list_runs(limit: int = 20) -> Dict[str, Any]:
    """List recent plugin-managed PCR runs, including interrupted runs after a restart."""
    return get_manager().list_runs(limit=limit)


@mcp.tool(annotations=LOCAL_WRITE)
def pcr_reject_agent(
    run_id: str,
    agent: int,
    rejected: bool = True,
) -> Dict[str, Any]:
    """Exclude or restore an Agent in this run's recommendation pool."""
    return get_manager().reject_agent(
        run_id=run_id,
        agent=agent,
        rejected=rejected,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_kill_agent(run_id: str, agent: int) -> Dict[str, Any]:
    """Stop one running Agent without cancelling the other candidates."""
    return get_manager().kill_agent(run_id=run_id, agent=agent)


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_stop_run(
    run_id: str,
    wait_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Stop every active Agent without accepting or syncing any candidate."""
    return get_manager().stop_run(run_id=run_id, wait_seconds=wait_seconds)


@mcp.tool(annotations=RUN_CODE)
def pcr_retry_agent(run_id: str, agent: int) -> Dict[str, Any]:
    """Rerun one failed, killed, cancelled, or interrupted Agent from a fresh copy."""
    return get_manager().retry_agent(run_id=run_id, agent=agent)


@mcp.tool(annotations=RUN_CODE)
def pcr_add_agents(
    run_id: str,
    count: int,
    confirm_large_run: bool = False,
) -> Dict[str, Any]:
    """Add independent candidates after the active batch stops.

    A result with status confirmation_required is not a started batch. Present
    its additional storage estimate to the user and call again with
    confirm_large_run only after explicit approval.
    """
    return get_manager().add_agents(
        run_id=run_id,
        count=count,
        confirm_large_run=confirm_large_run,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_accept_agent(
    run_id: str,
    agent: int,
    wait_seconds: float = 45.0,
) -> Dict[str, Any]:
    """Finalize a successful Agent, stop remaining work, sync if enabled, and clean up."""
    return get_manager().accept_agent(
        run_id=run_id,
        agent=agent,
        wait_seconds=wait_seconds,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_recover_finalization(
    run_id: str,
    sync_was_applied: bool,
) -> Dict[str, Any]:
    """Resolve an interrupted sync journal after inspecting the original workspace."""
    return get_manager().recover_finalization(
        run_id=run_id,
        sync_was_applied=sync_was_applied,
    )


@mcp.tool(annotations=RUN_CODE)
def pcr_continue_from_agent(
    run_id: str,
    agent: int,
    prompt: str,
    num_agents: Optional[int] = None,
    max_parallel: Optional[int] = None,
    confirm_large_run: bool = False,
) -> Dict[str, Any]:
    """Accept one Agent and start the next prompt from its promoted Codex session.

    A confirmation_required response does not accept the Agent or start the next
    run. Present the estimate and call again only after explicit confirmation.
    """
    return get_manager().continue_from_agent(
        run_id=run_id,
        agent=agent,
        prompt=prompt,
        num_agents=num_agents,
        max_parallel=max_parallel,
        confirm_large_run=confirm_large_run,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_discard_run(
    run_id: str,
    keep_workspaces: bool = False,
    wait_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Stop and discard an unaccepted run, cleaning candidate workspaces by default."""
    return get_manager().discard_run(
        run_id=run_id,
        keep_workspaces=keep_workspaces,
        wait_seconds=wait_seconds,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def pcr_cleanup_expired_runs() -> Dict[str, Any]:
    """Apply TTL, retention, and storage cleanup to plugin-managed run artifacts."""
    return get_manager().cleanup_expired_runs()


@mcp.tool(annotations=READ_ONLY)
def pcr_list_resume_sessions(
    workspace: str,
    include_non_interactive: bool = False,
    limit: int = 20,
) -> Dict[str, Any]:
    """List Codex sessions that PCR can clone and resume for a workspace."""
    return get_manager().resume_sessions(
        workspace=workspace,
        include_non_interactive=include_non_interactive,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_get_resume_history(
    workspace: str,
    session_id: str,
    cursor: int = 0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Read the visible user, reasoning, and response history of a resumable session."""
    return get_manager().resume_history(
        workspace=workspace,
        session_id=session_id,
        cursor=cursor,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
def pcr_list_models(
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> Dict[str, Any]:
    """List cached Codex models and reasoning efforts compatible with a selection."""
    return get_manager().model_options(model=model, effort=effort)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
