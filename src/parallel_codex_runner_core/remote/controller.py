from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import platform
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping

from platformdirs import user_state_path

from ..app import get_codex_home
from ..codex_usage import CodexUsageReader
from ..conversation_service import (
    DEFAULT_CONVERSATION_LIST_PAGE_BYTES,
    MAX_CONVERSATION_LIST_PAGE_BYTES,
    MAX_CONVERSATION_LIST_PAGE_SIZE,
    ConversationApplicationService,
)
from ..conversation_protocol import CONVERSATION_SCHEMA_VERSION
from ..plugin_runtime import (
    DEFAULT_EVENT_PAGE_BYTES,
    DEFAULT_FINAL_MESSAGE_PAGE_BYTES,
    DEFAULT_HISTORY_PAGE_BYTES,
    DEFAULT_RUN_LIST_PAGE_BYTES,
    PluginRunError,
    PluginRunPendingError,
    PluginRunNotAppliedError,
    PluginRunManager,
)
from ..models import (
    DEFAULT_FOLLOW_UP_DELAY_SECONDS,
    DEFAULT_NUM_AGENTS,
    DEFAULT_SUBAGENTS_LIMIT,
    DEFAULT_SYNTHESIS_AGENTS,
)
from ..limits import MAX_AGENT_COUNT
from ..prompt_history import PromptHistoryStore
from ..scoped_history import HistorySchemaCompatibilityError
from ..workspace_config import WorkspaceConfigStore, WorkspaceSettings
from .filesystem import FileSystemBrowser, RemoteFileSystemError
from .journal import CommandJournal
from .devices import DeviceRegistry, DeviceRegistryError
from .controller_relay import (
    ControllerRelayError,
    ControllerRelayHttpError,
    ControllerRelayProxy,
)
from .feedback import FeedbackError, FeedbackStore
from .follow_up_queue import (
    FollowUpQueueCoordinator,
    FollowUpQueueError,
    FollowUpQueueStore,
    canonical_follow_up_settings,
)
from .protocol import (
    MAX_RESPONSE_BYTES,
    MINIMUM_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    RemoteRequest,
)
from .relay_config import RelayConfigurationError, RelayConfigurationStore
from .relay_http import RelayHttpClient, RelayHttpError
from .relay_bridge import RelayBridge, RelayLinkStatus
from .relay_state import RelayStateError
from .notifications import RelayRunCompletionMonitor
from .transcript import TRANSCRIPT_SCHEMA_VERSION, transcript_page
from .workspace_registry import WorkspaceRegistry, WorkspaceRegistryError
from .workspace_files import (
    MAX_WORKSPACE_FILE_CHUNK_BYTES,
    WorkspaceFileContext,
    WorkspaceFileError,
    is_source_workspace_reference,
    read_workspace_file,
    resolve_workspace_item,
)


class RemoteControllerError(RuntimeError):
    """A stable error returned to native remote clients."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = bool(retryable)


def _daemon_version() -> str:
    try:
        return version("parallel-codex-runner")
    except PackageNotFoundError:
        return "development"


def default_remote_state_dir() -> Path:
    return (
        user_state_path("parallel-codex-runner", appauthor=False) / "remote"
    ).expanduser().resolve()


class RemoteController:
    """Stable command surface shared by local UI and remote transports."""

    READ_ONLY_ACTIONS = frozenset(
        {
            "system.health",
            "system.capabilities",
            "commands.get",
            "filesystem.roots",
            "filesystem.list",
            "filesystem.inspect",
            "workspaces.list",
            "workspaces.settings.get",
            "workspace.file.read",
            "workspace.item.resolve",
            "settings.defaults.get",
            "devices.list",
            "runs.estimate",
            "runs.list",
            "runs.get",
            "runs.history",
            "runs.events",
            "runs.agent",
            "runs.question",
            "runs.operation",
            "runs.diff",
            "runs.file.preview",
            "conversations.list",
            "conversations.search",
            "conversations.get",
            "turns.get",
            "storage.summary",
            "codex.resume_sessions",
            "codex.resume_history",
            "codex.model_options",
            "codex.account_usage",
            "prompt_history.list",
            "feedback.list",
            "feedback.get",
            "feedback.attachment",
            "relay.status",
            "controller.status",
            "controller.identity",
            "controller.machines",
            "controller.call",
            "controller.http",
            "controller.completion_watches",
            "followups.get",
            "followups.list",
        }
    )
    MUTATING_ACTIONS = frozenset(
        {
            "filesystem.mkdir",
            "workspaces.add",
            "workspaces.remove",
            "workspaces.select",
            "workspaces.settings.update",
            "settings.defaults.update",
            "devices.pairing_create",
            "devices.pairing_complete",
            "devices.revoke",
            "runs.start",
            "runs.stop",
            "runs.kill_agent",
            "runs.answer_question",
            "runs.retry_agent",
            "runs.reject_agent",
            "runs.add_agents",
            "runs.update_synthesis",
            "runs.accept_agent",
            "runs.recover_finalization",
            "runs.verify_finalization",
            "runs.discard",
            "storage.cleanup",
            "runs.continue",
            "turns.start",
            "turns.continue",
            "turns.finalize",
            "conversations.rename",
            "prompt_history.append",
            "feedback.submit",
            "feedback.add_note",
            "feedback.add_attachment",
            "feedback.remove_attachment",
            "feedback.withdraw",
            "feedback.admin_update",
            "relay.enroll",
            "relay.pairing_create",
            "relay.poll",
            "relay.clear",
            "controller.pair",
            "controller.select",
            "controller.download_macos_update",
            "controller.completion_watch",
            "controller.completion_unwatch",
            "followups.replace",
            "followups.enqueue",
            "followups.reconcile_enqueue",
            "followups.select_agent",
            "followups.clear",
        }
    )

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        manager: PluginRunManager | None = None,
        browser: FileSystemBrowser | None = None,
        registry: WorkspaceRegistry | None = None,
        devices: DeviceRegistry | None = None,
        relay_configuration: RelayConfigurationStore | None = None,
        controller_relay: ControllerRelayProxy | None = None,
        prompt_history: PromptHistoryStore | None = None,
        workspace_config: WorkspaceConfigStore | None = None,
        feedback: FeedbackStore | None = None,
    ) -> None:
        self.state_dir = (state_dir or default_remote_state_dir()).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass
        self.browser = browser or FileSystemBrowser()
        self.registry = registry or WorkspaceRegistry(
            self.state_dir / "workspaces.json",
            self.browser,
        )
        self.devices = devices or DeviceRegistry(self.state_dir / "devices.json")
        self.manager = manager or PluginRunManager(
            self.state_dir / "plugin",
            detached_workers=True,
            # The desktop and phone are full PCR clients. Their safety limit is
            # actual free disk space plus the explicit large-run confirmation,
            # not the smaller quota intended for unattended plugin calls.
            enforce_storage_quota=False,
            # App conversations are durable, not expiring plugin scratch jobs.
            retain_history=True,
        )
        self._owns_manager = manager is None
        self.codex_usage = CodexUsageReader(self.state_dir / "codex-usage-cache")
        self.conversations = ConversationApplicationService(
            self.manager,
            self.state_dir / "conversation-state",
        )
        self.relay = RelayHttpClient(self.state_dir)
        self.relay_link_status = RelayLinkStatus(self.state_dir)
        self.controller_relay = controller_relay or ControllerRelayProxy(self.state_dir)
        self.remote_completion_monitor = RelayRunCompletionMonitor(
            self.controller_relay,
            self.state_dir / "relay-run-completion-notifications.json",
        )
        self.relay_configuration = relay_configuration or RelayConfigurationStore(
            self.state_dir / "server.json"
        )
        self.prompt_history = prompt_history or PromptHistoryStore()
        self.feedback = feedback or FeedbackStore(self.state_dir / "feedback")
        self.workspace_config = workspace_config or (
            WorkspaceConfigStore()
            if state_dir is None
            else WorkspaceConfigStore(
                self.state_dir.parent / "workspace_config.json"
            )
        )
        self.follow_up_queues = FollowUpQueueStore(
            self.state_dir / "follow-up-queues.json"
        )
        self.follow_up_coordinator: FollowUpQueueCoordinator | None = None
        self.command_receipt_reader: Callable[[RemoteRequest], dict[str, Any]] | None = None
        self.prompt_delivery_status: Callable[[], dict[str, Any]] | None = None
        self._prompt_lock = threading.Lock()
        self._prompt_admissions: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()
        self.command_receipt_store_id: str | None = None
        # _conversations_list synchronizes every run page into the conversation
        # store on the first cursor-less call. That full scan re-reads and
        # re-synchronizes all run history even when runs.json has not changed
        # since the last call, serializing clients behind redundant work.
        # Cache the (mtime_ns, size) stamp of runs.json per workspace so a
        # rapid re-list skips the full resync. The stamp is keyed by workspace
        # path so switching workspaces still forces a fresh sync.
        self._conversation_runs_stamps: dict[tuple[str, str], tuple[int, int] | None] = {}
        self._actions: dict[str, Callable[[RemoteRequest], dict[str, Any]]] = {
            "system.health": self._health,
            "system.capabilities": self._capabilities,
            "commands.get": self._command_receipt,
            "filesystem.roots": self._filesystem_roots,
            "filesystem.list": self._filesystem_list,
            "filesystem.inspect": self._filesystem_inspect,
            "filesystem.mkdir": self._filesystem_mkdir,
            "workspaces.list": self._workspaces_list,
            "workspaces.add": self._workspaces_add,
            "workspaces.select": self._workspaces_select,
            "workspaces.remove": self._workspaces_remove,
            "workspaces.settings.get": self._workspaces_settings_get,
            "workspaces.settings.update": self._workspaces_settings_update,
            "workspace.file.read": self._workspace_file_read,
            "workspace.item.resolve": self._workspace_item_resolve,
            "settings.defaults.get": self._default_settings_get,
            "settings.defaults.update": self._default_settings_update,
            "devices.list": self._devices_list,
            "devices.pairing_create": self._devices_pairing_create,
            "devices.pairing_complete": self._devices_pairing_complete,
            "devices.revoke": self._devices_revoke,
            "runs.estimate": self._runs_estimate,
            "runs.start": self._runs_start,
            "runs.list": self._runs_list,
            "runs.get": self._runs_get,
            "runs.history": self._runs_history,
            "runs.events": self._runs_events,
            "runs.agent": self._runs_agent,
            "runs.question": self._runs_question,
            "runs.answer_question": self._runs_answer_question,
            "runs.operation": self._runs_operation,
            "runs.diff": self._runs_diff,
            "runs.file.preview": self._runs_file_preview,
            "conversations.list": self._conversations_list,
            "conversations.search": self._conversations_search,
            "conversations.get": self._conversations_get,
            "conversations.rename": self._conversations_rename,
            "turns.get": self._turns_get,
            "storage.summary": self._storage_summary,
            "runs.stop": self._runs_stop,
            "runs.kill_agent": self._runs_kill_agent,
            "runs.retry_agent": self._runs_retry_agent,
            "runs.reject_agent": self._runs_reject_agent,
            "runs.add_agents": self._runs_add_agents,
            "runs.update_synthesis": self._runs_update_synthesis,
            "runs.accept_agent": self._runs_accept_agent,
            "runs.recover_finalization": self._runs_recover_finalization,
            "runs.verify_finalization": self._runs_recover_finalization,
            "runs.discard": self._runs_discard,
            "storage.cleanup": self._storage_cleanup,
            "runs.continue": self._runs_continue,
            "turns.start": self._runs_start,
            "turns.continue": self._runs_continue,
            "turns.finalize": self._runs_accept_agent,
            "codex.resume_sessions": self._resume_sessions,
            "codex.resume_history": self._resume_history,
            "codex.model_options": self._model_options,
            "codex.account_usage": self._account_usage,
            "prompt_history.list": self._prompt_history_list,
            "prompt_history.append": self._prompt_history_append,
            "feedback.submit": self._feedback_submit,
            "feedback.list": self._feedback_list,
            "feedback.get": self._feedback_get,
            "feedback.add_note": self._feedback_add_note,
            "feedback.add_attachment": self._feedback_add_attachment,
            "feedback.attachment": self._feedback_attachment,
            "feedback.remove_attachment": self._feedback_remove_attachment,
            "feedback.withdraw": self._feedback_withdraw,
            "feedback.admin_update": self._feedback_admin_update,
            "relay.status": self._relay_status,
            "relay.enroll": self._relay_enroll,
            "relay.pairing_create": self._relay_pairing_create,
            "relay.poll": self._relay_poll,
            "relay.clear": self._relay_clear,
            "controller.status": self._controller_status,
            "controller.identity": self._controller_identity,
            "controller.machines": self._controller_machines,
            "controller.pair": self._controller_pair,
            "controller.select": self._controller_select,
            "controller.call": self._controller_call,
            "controller.http": self._controller_http,
            "controller.download_macos_update": self._controller_download_macos_update,
            "controller.completion_watches": self._controller_completion_watches,
            "controller.completion_watch": self._controller_completion_watch,
            "controller.completion_unwatch": self._controller_completion_unwatch,
            "followups.get": self._followups_get,
            "followups.list": self._followups_list,
            "followups.replace": self._followups_replace,
            "followups.enqueue": self._followups_enqueue,
            "followups.reconcile_enqueue": self._followups_reconcile_enqueue,
            "followups.select_agent": self._followups_select_agent,
            "followups.clear": self._followups_clear,
        }
        classified_actions = self.READ_ONLY_ACTIONS | self.MUTATING_ACTIONS
        if classified_actions != self._actions.keys():
            missing = sorted(self._actions.keys() - classified_actions)
            unknown = sorted(classified_actions - self._actions.keys())
            raise RuntimeError(
                "Remote action policy is incomplete"
                f" (missing={missing}, unknown={unknown})"
            )

    @property
    def actions(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    def close(self) -> None:
        if self.follow_up_coordinator is not None:
            self.follow_up_coordinator.stop()
            self.follow_up_coordinator = None
        self.remote_completion_monitor.stop()
        self.controller_relay.close()
        if self._owns_manager:
            self.manager.close()

    def start_follow_up_coordinator(
        self,
        execute: Callable[[str, Mapping[str, Any], str], dict[str, Any]],
    ) -> None:
        if self.follow_up_coordinator is not None:
            return
        self.follow_up_coordinator = FollowUpQueueCoordinator(
            self.follow_up_queues,
            self,
            execute,
        )
        self.follow_up_coordinator.start()

    def dispatch(self, request: RemoteRequest, *, require_unstarted_continuation: bool = False) -> dict[str, Any]:
        handler = self._actions.get(request.action)
        if handler is None:
            raise RemoteControllerError(
                "unknown_action",
                f"Unknown remote action: {request.action}",
                details={"actions": list(self.actions)},
            )
        try:
            with self._prompt_admission(request):
                if require_unstarted_continuation:
                    if request.action not in {"turns.continue", "runs.continue"}:
                        raise RemoteControllerError("invalid_argument", "Only a continuation can recover history preparation")
                    return self._runs_continue(request, require_unstarted_continuation=True)
                return handler(request)
        except RemoteControllerError as exc:
            if (request.action in CommandJournal.PROMPT_ACTIONS
                    and exc.code == "workspace_busy" and exc.retryable):
                # Existing clients already keep pcr_operation_pending commands
                # immutable. Do not let them edit a daemon-owned pending prompt.
                raise RemoteControllerError("pcr_operation_pending", exc.message,
                    details=exc.details, retryable=True) from exc
            raise
        except RemoteFileSystemError as exc:
            raise RemoteControllerError("filesystem_error", str(exc)) from exc
        except WorkspaceRegistryError as exc:
            raise RemoteControllerError("workspace_error", str(exc)) from exc
        except WorkspaceFileError as exc:
            raise RemoteControllerError("workspace_file_error", str(exc)) from exc
        except DeviceRegistryError as exc:
            raise RemoteControllerError("device_error", str(exc)) from exc
        except FeedbackError as exc:
            raise RemoteControllerError("feedback_error", str(exc)) from exc
        except FollowUpQueueError as exc:
            raise RemoteControllerError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
            ) from exc
        except ControllerRelayHttpError as exc:
            suffix = str(exc.status_code) if exc.status_code is not None else "error"
            raise RemoteControllerError(f"relay_http_{suffix}", str(exc)) from exc
        except ControllerRelayError as exc:
            raise RemoteControllerError("controller_relay_error", str(exc)) from exc
        except RelayHttpError as exc:
            if exc.refresh_token_rejected:
                raise RemoteControllerError(
                    "relay_credentials_rejected",
                    "The server no longer accepts this device's credentials. "
                    "Re-pair with the server to continue.",
                ) from exc
            raise RemoteControllerError(
                "relay_unreachable",
                "Could not reach the relay server. Check your network connection.",
            ) from exc
        except RelayStateError as exc:
            raise RemoteControllerError(
                "relay_state_error",
                "The relay state is corrupted or unreadable.",
            ) from exc
        except RelayConfigurationError as exc:
            raise RemoteControllerError(
                "server_not_configured",
                "This installation is not connected to its server. "
                "Run 'pcrd configure-server' on the Mac to set up the connection.",
                details={"reason": str(exc)},
            ) from exc
        except PluginRunPendingError as exc:
            raise RemoteControllerError(
                "pcr_operation_pending",
                str(exc),
                retryable=True,
            ) from exc
        except HistorySchemaCompatibilityError as exc:
            # Preserve the original command for the delivery coordinator. A
            # native schema change is not invalid user input or proof of rejection.
            raise RemoteControllerError(
                "pcr_operation_pending", str(exc),
                details={"reason": "history_schema_compatibility"},
                retryable=request.action in CommandJournal.PROMPT_ACTIONS,
            ) from exc
        except PluginRunNotAppliedError as exc:
            raise RemoteControllerError(
                exc.error_code or "turn_not_applied",
                exc.user_message or str(exc),
                details={"delivery_outcome": "not_applied"},
            ) from exc
        except PluginRunError as exc:
            raise RemoteControllerError(
                exc.error_code or "pcr_error",
                exc.user_message or str(exc),
            ) from exc
        except (TypeError, ValueError, OverflowError) as exc:
            raise RemoteControllerError("invalid_argument", str(exc)) from exc

    @staticmethod
    def _text(body: Mapping[str, Any], name: str, *, required: bool = True) -> str:
        value = str(body.get(name) or "").strip()
        if required and not value:
            raise RemoteControllerError(
                "invalid_argument", f"{name} must not be empty"
            )
        return value

    @staticmethod
    def _integer(
        body: Mapping[str, Any],
        name: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = body.get(name, default)
        if raw is None or isinstance(raw, bool):
            raise RemoteControllerError(
                "invalid_argument", f"{name} must be an integer"
            )
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RemoteControllerError(
                "invalid_argument", f"{name} must be an integer"
            ) from exc
        if minimum is not None and value < minimum:
            raise RemoteControllerError(
                "invalid_argument", f"{name} must be at least {minimum}"
            )
        if maximum is not None and value > maximum:
            raise RemoteControllerError(
                "invalid_argument", f"{name} must not exceed {maximum}"
            )
        return value

    @staticmethod
    def _boolean(
        body: Mapping[str, Any],
        name: str,
        *,
        default: bool = False,
    ) -> bool:
        raw = body.get(name, default)
        if not isinstance(raw, bool):
            raise RemoteControllerError(
                "invalid_argument", f"{name} must be true or false"
            )
        return raw

    @staticmethod
    def _optional_boolean(body: Mapping[str, Any], name: str) -> bool | None:
        raw = body.get(name)
        if raw is None:
            return None
        if not isinstance(raw, bool):
            raise RemoteControllerError(
                "invalid_argument", f"{name} must be true, false, or null"
            )
        return raw

    def _workspace(self, body: Mapping[str, Any]) -> tuple[str, str | None]:
        workspace_id = str(body.get("workspace_id") or "").strip()
        if workspace_id:
            record = self.registry.require(workspace_id)
            return str(self.browser.resolve_directory(record.path)), workspace_id
        raw_path = self._text(body, "workspace")
        return str(self.browser.resolve_directory(raw_path)), None

    def _conversation_machine(self) -> tuple[str, str]:
        status = self.relay.status()
        return (
            str(status.get("mac_device_id") or "local"),
            str(status.get("device_name") or platform.node() or "Computer"),
        )

    def _conversation_context(self, workspace_id: str = "") -> dict[str, str]:
        machine_id, machine_name = self._conversation_machine()
        return {
            "machine_id": machine_id,
            "machine_name": machine_name,
            "workspace_id": workspace_id,
        }

    def _ensure_workspace_context_can_change(self, workspace_id: str) -> None:
        """Prevent a remote client from hiding an unfinished run."""
        record = self.registry.require(workspace_id)
        run_id = str(record.last_run_id or "").strip()
        if not run_id:
            return
        get_run = getattr(self.manager, "get_run", None)
        if not callable(get_run):
            return
        try:
            run = get_run(run_id)
        except PluginRunError:
            return
        status = str(run.get("status") or "").strip().lower()
        if status in {"sync_ambiguous", "cleanup_failed"}:
            raise RemoteControllerError(
                "workspace_busy",
                "Resolve the interrupted finalization before changing workspaces.",
                details={"run_id": run_id, "status": status, "delivery_outcome": "not_applied"},
            )
        worker_active = bool(run.get("worker_active"))
        if not worker_active and status not in {
            "preparing",
            "running",
            "stopping",
            "finalizing",
        }:
            finalized = run.get("finalized_agent")
            agents = run.get("agents")
            has_acceptance_candidate = isinstance(agents, list) and any(
                isinstance(agent, dict) and bool(agent.get("can_accept"))
                for agent in agents
            )
            if finalized is not None or not has_acceptance_candidate:
                return
        raise RemoteControllerError(
            "workspace_busy",
            "Finish or discard the current run before changing workspaces.",
            details={"run_id": run_id, "status": status, "delivery_outcome": "not_applied"},
            retryable=worker_active or status in {"preparing", "running", "stopping", "finalizing"},
        )

    def _ensure_run_start_context(
        self,
        path: str,
        workspace_id: str | None,
    ) -> None:
        """Retain older results; only filesystem activity blocks a new topic."""
        payload = self.registry.list()
        records = payload.get("workspaces", [])
        target_id = workspace_id
        if target_id is None:
            target_id = next(
                (
                    str(record.get("workspace_id"))
                    for record in records
                    if isinstance(record, dict)
                    and str(record.get("path") or "") == path
                ),
                None,
            )
        runs: dict[str, Mapping[str, Any]] = {}
        if target_id:
            last_id = self.registry.require(str(target_id)).last_run_id
            get_run = getattr(self.manager, "get_run", None)
            if last_id and callable(get_run):
                try:
                    runs[last_id] = get_run(last_id)
                except PluginRunError:
                    pass
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            page = self.manager.list_runs(limit=100, workspace=path, cursor=cursor)
            for run in page.get("runs", []):
                if isinstance(run, Mapping) and str(run.get("workspace") or "") == path:
                    runs[str(run.get("run_id") or "")] = run
            cursor = str(page.get("next_cursor") or "")
            if not page.get("has_more") or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        for run_id, run in runs.items():
            status = str(run.get("status") or "").lower()
            if bool(run.get("worker_active")) or status in {
                "preparing", "running", "stopping", "finalizing", "sync_ambiguous", "cleanup_failed",
            }:
                raise RemoteControllerError(
                    "workspace_busy",
                    "This workspace is still running or syncing another turn. Your new conversation can be sent when it finishes.",
                    details={"run_id": run_id, "status": status, "delivery_outcome": "not_applied"},
                    retryable=status not in {"sync_ambiguous", "cleanup_failed"},
                )

    def _ensure_continuation_context(self, run_id: str) -> None:
        """An older conversation may resume only after the current run is settled."""
        records = self.registry.list().get("workspaces", [])
        if any(
            isinstance(value, Mapping)
            and str(value.get("last_run_id") or "").strip() == run_id
            for value in records
        ):
            return
        get_run = getattr(self.manager, "get_run", None)
        if not callable(get_run):
            return
        source = get_run(run_id)
        workspace = str(source.get("workspace") or "").strip()
        if not workspace:
            return
        record = next(
            (
                value
                for value in records
                if isinstance(value, Mapping)
                and str(value.get("path") or "") == workspace
            ),
            None,
        )
        if not isinstance(record, Mapping):
            return
        current_run_id = str(record.get("last_run_id") or "").strip()
        if not current_run_id or current_run_id == run_id:
            return
        # Continuing an older candidate can first sync its retained files back;
        # unlike starting a fresh topic, that still needs the current result settled.
        self._ensure_workspace_context_can_change(str(record["workspace_id"]))
        self._ensure_run_start_context(
            workspace,
            str(record.get("workspace_id") or "").strip() or None,
        )

    def _register_workspace(self, path: str, workspace_id: str | None) -> tuple[str, int]:
        if workspace_id:
            self.registry.require(workspace_id)
            return workspace_id, int(self.registry.list()["revision"])
        registered = self.registry.register(path)
        self.workspace_config.initialize_from_defaults(path)
        workspace = dict(registered["workspace"])
        return str(workspace["workspace_id"]), int(registered["revision"])

    def _update_workspace_run_context(
        self,
        path: str,
        *,
        run_id: str,
        resume_session_id: str | None = None,
        only_if_current_run: bool = False,
        initialize_if_unbound: bool = False,
    ) -> tuple[str, int]:
        resolved = str(self.browser.resolve_directory(path))
        registry = self.registry.list()
        record = next(
            (
                item
                for item in registry.get("workspaces", [])
                if str(item.get("path") or "") == resolved
            ),
            None,
        )
        if record is None:
            if only_if_current_run and not initialize_if_unbound:
                return "", int(registry["revision"])
            registered = self.registry.register(resolved, select=False)
            record = dict(registered["workspace"])
        workspace_id = str(record["workspace_id"])
        if (
            only_if_current_run
            and str(record.get("last_run_id") or "") == run_id
            and resume_session_id is not None
            and str(record.get("last_resume_session_id") or "") == resume_session_id
        ):
            # A poll may repair a delayed promotion, but must not repeatedly
            # replace a new conversation or an explicit session selected later.
            return workspace_id, int(registry["revision"])
        expected_run_id = run_id if only_if_current_run else None
        if only_if_current_run and initialize_if_unbound and not record.get("last_run_id"):
            expected_run_id = ""
        revision = self.registry.update_context(
            workspace_id,
            run_id=run_id,
            resume_session_id=resume_session_id,
            expected_run_id=expected_run_id,
        )
        if revision is None:
            if only_if_current_run:
                return "", int(self.registry.list()["revision"])
            raise WorkspaceRegistryError(
                f"Could not update the workspace for PCR run {run_id}"
            )
        if resume_session_id is not None:
            # The promoted session is the next conversation default on every
            # client, not only registry metadata. Persist it beside the other
            # workspace settings so CLI, Android, and macOS cannot drift after
            # a run finishes while one client is offline.
            self.workspace_config.update(
                resolved,
                {"RESUME": resume_session_id or "NO"},
            )
        return workspace_id, revision

    @contextmanager
    def _prompt_admission(self, request: RemoteRequest):
        if request.action not in CommandJournal.PROMPT_ACTIONS:
            yield
            return
        body = request.body
        path = str(body.get("workspace") or "").strip()
        workspace_id = str(body.get("workspace_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if run_id:
            get_run = getattr(self.manager, "get_run", None)
            if callable(get_run):
                path = str(get_run(run_id).get("workspace") or "").strip()
            else:
                path = str(getattr(self.manager, "workspace", "") or "").strip()
        elif workspace_id:
            path = self.registry.require(workspace_id).path
        key = str(Path(path).expanduser().resolve()) if path else run_id or workspace_id or "default"
        with self._prompt_lock:
            admission = self._prompt_admissions.setdefault(key, threading.Lock())
        if not admission.acquire(blocking=False):
            raise RemoteControllerError("pcr_operation_pending", "This workspace is completing a message handoff",
                                        retryable=True)
        try:
            yield
        finally:
            admission.release()

    def _health(self, _request: RemoteRequest) -> dict[str, Any]:
        pcr = self.manager.health()
        delivery = self.prompt_delivery_status() if self.prompt_delivery_status else {}
        with self._prompt_lock:
            admission_count = sum(admission.locked() for admission in list(self._prompt_admissions.values()))
        if delivery.get("pending_count") or delivery.get("active_count") or admission_count:
            pcr = {**pcr, "active_operation": pcr.get("active_operation") or "prompt_delivery"}
        return {
            "status": "ok",
            "pcr": pcr,
            "prompt_delivery": delivery,
            "prompt_admission_count": admission_count,
            "workspace_registry": self._workspace_registry_payload(),
            "devices": self.devices.list(),
            "relay": self.relay.status(),
            "link": self.relay_link_status.load(),
        }

    def _command_receipt(self, request: RemoteRequest) -> dict[str, Any]:
        original = RemoteRequest.from_mapping({
            "protocol_version": PROTOCOL_VERSION,
            "client_id": request.client_id,
            "command_id": self._text(request.body, "command_id"),
            "action": self._text(request.body, "action"),
            "body": request.body.get("body"),
            "expected_revision": request.body.get("expected_revision"),
        })
        if original.action not in {"runs.start", "runs.continue", "turns.start", "turns.continue"}:
            raise RemoteControllerError("invalid_argument", "Only prompt command receipts can be queried")
        if self.command_receipt_reader is None:
            raise RemoteControllerError("unsupported_action", "Command receipt reader is unavailable")
        return {**self.command_receipt_reader(original), "command_id": original.command_id,
                "receipt_store_id": self.command_receipt_store_id}

    def recover_prompt_command(self, request: RemoteRequest) -> dict[str, Any] | None:
        if request.action not in {"runs.start", "runs.continue", "turns.start", "turns.continue"}:
            return None
        finder = getattr(self.manager, "prompt_command_result", None)
        return finder(CommandJournal.identity(request)) if callable(finder) else None

    def can_retry_prompt_command(self, request: RemoteRequest) -> bool:
        if request.action not in {"runs.continue", "turns.continue"}:
            return False
        run_id, agent = request.body.get("run_id"), request.body.get("agent")
        if not isinstance(run_id, str) or not isinstance(agent, int) or isinstance(agent, bool):
            return False
        checker = getattr(self.manager, "can_retry_unstarted_continuation", None)
        return bool(callable(checker) and checker(run_id, agent))

    def _capabilities(self, _request: RemoteRequest) -> dict[str, Any]:
        return {
            "daemon_name": "pcrd",
            "daemon_version": _daemon_version(),
            "protocol_version": PROTOCOL_VERSION,
            "minimum_client_protocol_version": MINIMUM_CLIENT_PROTOCOL_VERSION,
            "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "transcript_schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "conversation_schema_version": CONVERSATION_SCHEMA_VERSION,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
            "maximum_agent_count": MAX_AGENT_COUNT,
            "transport": "local",
            "platform": platform.system().lower(),
            "platform_release": platform.release(),
            "actions": list(self.actions),
            "mutating_actions": sorted(self.MUTATING_ACTIONS),
            "filesystem_scope": "user",
            "requires_workspace_allowlist": False,
            "supports_multiple_workspaces": True,
            "supports_event_cursor": True,
            "supports_run_list_cursor": True,
            "supports_byte_bounded_pages": True,
            "supports_final_message_paging": True,
            "supports_daemon_completion_notifications": (
                platform.system().lower() == "darwin"
            ),
            "supports_daemon_remote_completion_notifications": (
                platform.system().lower() == "darwin"
            ),
            "supports_transcript_items": True,
            "supports_work_operations": True,
            "supports_agent_questions": True,
            "supports_async_agent_questions": True,
            "supports_workspace_settings": True,
            "supports_workspace_file_transfer": True,
            "supports_visual_file_comparison": True,
            "supports_prompt_receipts": True,
            "supports_durable_prompt_handoff": bool(
                self.prompt_delivery_status and self.prompt_delivery_status().get("running")
            ),
            "command_receipt_store_id": self.command_receipt_store_id,
            "supports_workspace_item_resolution": True,
            "supports_default_settings": True,
            "supports_diff_paging": True,
            "supports_device_pairing": True,
            "supports_end_to_end_encryption": True,
            "supports_storage_management": True,
            "supports_feedback": True,
            "supports_daemon_follow_up_queue": True,
            "supports_follow_up_enqueue_reconciliation": True,
        }

    def _filesystem_roots(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.browser.roots()

    def _filesystem_list(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.browser.list_directory(
            str(body.get("path") or ""),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            limit=self._integer(body, "limit", default=100, minimum=1),
            include_hidden=self._boolean(body, "include_hidden", default=False),
            directories_only=self._boolean(
                body, "directories_only", default=False
            ),
            sort=str(body.get("sort") or "name"),
        )

    def _filesystem_inspect(self, request: RemoteRequest) -> dict[str, Any]:
        return self.browser.inspect(self._text(request.body, "path"))

    def _filesystem_mkdir(self, request: RemoteRequest) -> dict[str, Any]:
        return self.browser.create_directory(
            self._text(request.body, "parent"),
            self._text(request.body, "name"),
        )

    def _workspaces_list(self, _request: RemoteRequest) -> dict[str, Any]:
        return self._workspace_registry_payload()

    def _workspace_file_context(self, request: RemoteRequest, *, literal_paths: bool) -> WorkspaceFileContext:
        body = request.body
        workspace_id = self._text(body, "workspace_id")
        record = self.registry.require(workspace_id)
        workspace = self.browser.resolve_directory(record.path)
        run_id = self._text(body, "run_id", required=False)
        raw_agent = body.get("agent")
        agent = (
            self._integer(body, "agent", minimum=1)
            if raw_agent is not None
            else None
        )
        if bool(run_id) != (agent is not None):
            raise RemoteControllerError(
                "invalid_argument",
                "run_id and agent must be provided together",
            )

        if literal_paths and is_source_workspace_reference(workspace, self._text(body, "reference")):
            return WorkspaceFileContext(workspace_root=workspace, literal_paths=True)

        candidate: Path | None = None
        expected_candidate: Path | None = None
        candidate_synced = False
        if run_id and agent is not None:
            run = self.manager.get_run(run_id, include_events=False)
            run_workspace = Path(str(run.get("workspace") or "")).expanduser()
            try:
                if run_workspace.resolve(strict=True) != workspace:
                    raise RemoteControllerError(
                        "workspace_mismatch",
                        "This run does not belong to the selected workspace",
                    )
            except OSError as exc:
                raise RemoteControllerError(
                    "workspace_mismatch",
                    "This run's workspace is no longer available",
                ) from exc

            detail = self.manager.get_agent(
                run_id,
                agent,
                include_diff=False,
                include_events=False,
            )
            candidate_value = str(detail.get("workspace_dir") or "").strip()
            if candidate_value:
                candidate = Path(candidate_value).expanduser()
            run_root = str(run.get("run_root") or "").strip()
            if (run.get("config") or {}).get("workspace_mode") == "source":
                expected_candidate = workspace
            elif run_root:
                expected_candidate = (
                    Path(run_root).expanduser()
                    / "workspaces"
                    / f"agent_{agent:03d}"
                )
            candidate_synced = (
                run.get("finalized_agent") == agent
                and bool(run.get("synced_back"))
            )

            if candidate is None:
                candidate = expected_candidate

        return WorkspaceFileContext(
            workspace_root=workspace,
            candidate_root=candidate,
            expected_candidate_root=expected_candidate,
            candidate_synced_to_workspace=candidate_synced,
            literal_paths=literal_paths,
        )

    def _workspace_item_resolve(self, request: RemoteRequest) -> dict[str, Any]:
        return resolve_workspace_item(
            self._workspace_file_context(request, literal_paths=True),
            self._text(request.body, "reference"),
            offset=self._integer(request.body, "offset", default=0, minimum=0),
        )

    def _workspace_file_read(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        result = read_workspace_file(
            self._workspace_file_context(request, literal_paths=self._boolean(body, "literal_paths", default=False)),
            self._text(body, "reference"),
            offset=self._integer(body, "offset", default=0, minimum=0),
            max_bytes=self._integer(
                body,
                "max_bytes",
                default=MAX_WORKSPACE_FILE_CHUNK_BYTES,
                minimum=1,
                maximum=MAX_WORKSPACE_FILE_CHUNK_BYTES,
            ),
            expected_file_id=self._text(
                body,
                "expected_file_id",
                required=False,
            ),
        )
        return {
            **result,
            "workspace_id": self._text(body, "workspace_id"),
            "run_id": self._text(body, "run_id", required=False),
            "agent": body.get("agent"),
        }

    def _workspaces_add(self, request: RemoteRequest) -> dict[str, Any]:
        result = self.registry.register(
            self._text(request.body, "path"),
            name=str(request.body.get("name") or ""),
            select=self._boolean(request.body, "select", default=True),
            expected_revision=request.expected_revision,
        )
        workspace = result.get("workspace")
        if isinstance(workspace, dict):
            settings = self.workspace_config.initialize_from_defaults(
                str(workspace.get("path") or "")
            )
            workspace["settings"] = self._workspace_settings_payload(settings)
        return result

    def _workspaces_select(self, request: RemoteRequest) -> dict[str, Any]:
        workspace_id = self._text(request.body, "workspace_id")
        return self.registry.select(
            workspace_id,
            expected_revision=request.expected_revision,
        )

    def _workspaces_remove(self, request: RemoteRequest) -> dict[str, Any]:
        workspace_id = self._text(request.body, "workspace_id")
        self._ensure_workspace_context_can_change(workspace_id)
        return self.registry.remove(
            workspace_id,
            expected_revision=request.expected_revision,
        )

    def _workspace_registry_payload(self) -> dict[str, Any]:
        payload = self.registry.list()
        records = payload.get("workspaces")
        if not isinstance(records, list):
            return payload
        run_ids = {
            str(record.get("last_run_id") or "").strip()
            for record in records
            if isinstance(record, dict)
            and str(record.get("last_run_id") or "").strip()
        }
        run_summaries: dict[str, dict[str, Any]] = {}
        if run_ids:
            listed = self.manager.get_run_overviews(sorted(run_ids))
            for value in listed.get("runs", []):
                if not isinstance(value, dict):
                    continue
                run_id = str(value.get("run_id") or "").strip()
                if run_id in run_ids:
                    run_summaries[run_id] = value
        paths = tuple(
            str(record.get("path") or "")
            for record in records
            if isinstance(record, dict) and record.get("path")
        )
        settings_by_path = self.workspace_config.load_many(paths)
        enriched: list[dict[str, Any]] = []
        for raw_record in records:
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            workspace_path = str(record.get("path") or "")
            run_id = str(record.get("last_run_id") or "").strip()
            run = run_summaries.get(run_id)
            if run_id and run is None:
                # An expired execution is not a resumable current Turn. Keep
                # the durable session and the registry's original lineage,
                # but never advertise a dangling execution to either client.
                record["last_run_id"] = ""
            if run is not None:
                record.update(
                    {
                        "run_status": str(run.get("status") or ""),
                        "run_worker_active": bool(run.get("worker_active")),
                        "run_agents_total": int(run.get("agents_total") or 0),
                        "run_agents_finished": int(run.get("agents_finished") or 0),
                        "run_agents_failed": int(run.get("agents_failed") or 0),
                    }
                )
            record["settings"] = self._workspace_settings_payload(
                settings_by_path.get(
                    self.workspace_config.context_key(workspace_path)
                )
            )
            enriched.append(record)
        return {**payload, "workspaces": enriched}

    @staticmethod
    def _workspace_settings_payload(
        settings: WorkspaceSettings | None,
    ) -> dict[str, Any]:
        configured = settings is not None
        settings = settings or WorkspaceSettings()
        agents = settings.agents or DEFAULT_NUM_AGENTS
        max_parallel = settings.max_parallel
        return {
            "configured": configured,
            "agents": agents,
            "synthesis_agents": (
                settings.synthesis_agents
                if settings.synthesis_agents is not None
                else DEFAULT_SYNTHESIS_AGENTS
            ),
            "follow_up_delay": (
                settings.follow_up_delay
                if settings.follow_up_delay is not None
                else DEFAULT_FOLLOW_UP_DELAY_SECONDS
            ),
            "execution": settings.execution or "parallel",
            "automatic_max_parallel": max_parallel is None,
            "max_parallel": max_parallel or agents,
            "subagents": settings.subagents is True,
            "subagents_limit": settings.subagents_limit or DEFAULT_SUBAGENTS_LIMIT,
            "recommend_by": settings.recommend_by or "reasoning_tokens",
            "model": settings.model or "",
            "effort": settings.effort or "",
            "fast_mode": RemoteController._mode_name(settings.fast),
            "synthesis_model": settings.synthesis_model or "",
            "synthesis_effort": settings.synthesis_effort or "",
            "synthesis_fast_mode": RemoteController._mode_name(
                settings.synthesis_fast
            ),
            "sync_back": settings.sync_back is not False,
            "keep_workspaces": settings.keep_workspaces is True,
            "resume_session_id": (
                "NO"
                if "resume_session_id" in settings.present_fields
                and settings.resume_session_id is None
                else settings.resume_session_id or ""
            ),
            "resume_include_non_interactive": (
                settings.resume_include_non_interactive is True
            ),
            "runs_dir": settings.runs_dir or "",
            "codex_bin": settings.codex_bin or "codex",
        }

    @staticmethod
    def _mode_name(value: bool | None) -> str:
        if value is True:
            return "on"
        if value is False:
            return "off"
        return "automatic"

    def _workspaces_settings_get(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, workspace_id = self._workspace(request.body)
        return {
            "workspace_id": workspace_id,
            "settings": self._workspace_settings_payload(
                self.workspace_config.load(workspace)
            ),
        }

    def _workspaces_settings_update(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        workspace, workspace_id = self._workspace(request.body)
        updates = self._workspace_settings_updates(request.body)
        if not updates:
            raise RemoteControllerError(
                "invalid_argument", "At least one workspace setting is required"
            )
        settings = self.workspace_config.update(workspace, updates)
        if settings is None:
            raise RemoteControllerError(
                "workspace_error", "Could not save workspace settings"
            )
        if "FOLLOW_UP_DELAY" in updates:
            self.follow_up_queues.update_delay(workspace_id, settings.follow_up_delay)
        return {
            "workspace_id": workspace_id,
            "settings": self._workspace_settings_payload(settings),
        }

    def _default_settings_get(self, _request: RemoteRequest) -> dict[str, Any]:
        return {
            "settings": self._workspace_settings_payload(
                self.workspace_config.load_defaults()
            )
        }

    def _default_settings_update(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        updates = self._workspace_settings_updates(request.body)
        allowed = {
            "AGENTS",
            "SYNTHESIS_AGENTS",
            "FOLLOW_UP_DELAY",
            "EXECUTION",
            "MAX_PARALLEL",
            "SUBAGENTS",
            "SUBAGENTS_LIMIT",
            "RECOMMEND_BY",
            "MODEL",
            "EFFORT",
            "FAST",
            "SYNTHESIS_MODEL",
            "SYNTHESIS_EFFORT",
            "SYNTHESIS_FAST",
            "SYNC_BACK",
            "KEEP_WORKSPACES",
        }
        updates = {key: value for key, value in updates.items() if key in allowed}
        if not updates:
            raise RemoteControllerError(
                "invalid_argument", "At least one default conversation setting is required"
            )
        settings = self.workspace_config.update_defaults(updates)
        if settings is None:
            raise RemoteControllerError(
                "workspace_error", "Could not save default conversation settings"
            )
        return {"settings": self._workspace_settings_payload(settings)}

    def _workspace_settings_updates(
        self,
        body: Mapping[str, Any],
    ) -> dict[str, object]:
        updates: dict[str, object] = {}
        integer_fields = {
            "agents": ("AGENTS", 1, MAX_AGENT_COUNT),
            "synthesis_agents": ("SYNTHESIS_AGENTS", 0, MAX_AGENT_COUNT),
            "follow_up_delay": ("FOLLOW_UP_DELAY", 0, None),
            "subagents_limit": ("SUBAGENTS_LIMIT", 1, None),
        }
        for request_name, (stored_name, minimum, maximum) in integer_fields.items():
            if request_name in body:
                updates[stored_name] = self._integer(
                    body,
                    request_name,
                    minimum=minimum,
                    maximum=maximum,
                )

        if "execution" in body:
            execution = str(body.get("execution") or "").strip().lower()
            if execution not in {"parallel", "serial"}:
                raise RemoteControllerError(
                    "invalid_argument", "execution must be parallel or serial"
                )
            updates["EXECUTION"] = execution

        if self._boolean(body, "automatic_max_parallel", default=False):
            updates["MAX_PARALLEL"] = "auto"
        elif "max_parallel" in body:
            updates["MAX_PARALLEL"] = self._integer(
                body,
                "max_parallel",
                minimum=1,
            )

        for request_name, stored_name in {
            "subagents": "SUBAGENTS",
            "sync_back": "SYNC_BACK",
            "keep_workspaces": "KEEP_WORKSPACES",
            "resume_include_non_interactive": "RESUME_INCLUDE_NON_INTERACTIVE",
        }.items():
            if request_name in body:
                updates[stored_name] = self._boolean(body, request_name)

        if "recommend_by" in body:
            recommend_by = str(body.get("recommend_by") or "").strip().lower()
            if recommend_by not in {"duration", "reasoning_tokens"}:
                raise RemoteControllerError(
                    "invalid_argument",
                    "recommend_by must be duration or reasoning_tokens",
                )
            updates["RECOMMEND_BY"] = recommend_by

        for request_name, stored_name, default_value in (
            ("model", "MODEL", "default"),
            ("effort", "EFFORT", "auto"),
            ("synthesis_model", "SYNTHESIS_MODEL", "default"),
            ("synthesis_effort", "SYNTHESIS_EFFORT", "auto"),
            ("resume_session_id", "RESUME", "NO"),
            ("runs_dir", "RUNS_DIR", ""),
            ("codex_bin", "CODEX_BIN", "codex"),
        ):
            if request_name in body:
                updates[stored_name] = (
                    str(body.get(request_name) or "").strip() or default_value
                )

        for request_name, stored_name in (
            ("fast_mode", "FAST"),
            ("synthesis_fast_mode", "SYNTHESIS_FAST"),
        ):
            if request_name not in body:
                continue
            mode = str(body.get(request_name) or "automatic").strip().lower()
            if mode not in {"automatic", "on", "off"}:
                raise RemoteControllerError(
                    "invalid_argument",
                    f"{request_name} must be automatic, on, or off",
                )
            updates[stored_name] = {
                "automatic": "AUTO",
                "on": True,
                "off": False,
            }[mode]
        return updates

    def _devices_list(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.devices.list()

    def _devices_pairing_create(self, request: RemoteRequest) -> dict[str, Any]:
        return self.devices.create_pairing(
            self._integer(request.body, "ttl_seconds", default=300, minimum=30)
        )

    def _devices_pairing_complete(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.devices.complete_pairing(
            self._text(body, "pairing_id"),
            self._text(body, "secret"),
            user_id=self._text(body, "user_id"),
            device_name=self._text(body, "device_name"),
            public_key=self._text(body, "public_key"),
            kind=str(body.get("kind") or "android"),
        )

    def _devices_revoke(self, request: RemoteRequest) -> dict[str, Any]:
        return self.devices.revoke(self._text(request.body, "device_id"))

    def _relay_status(self, _request: RemoteRequest) -> dict[str, Any]:
        status = dict(self.relay.status())
        status["link"] = self.relay_link_status.load()
        return status

    def _relay_enroll(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        base_url = self._text(body, "base_url")
        enrollment_token = self._text(body, "enrollment_token")
        device_name = str(body.get("device_name") or "")
        result = self.relay.enroll(
            base_url=base_url,
            enrollment_token=enrollment_token,
            device_name=device_name,
        )
        self.relay_configuration.configure(
            base_url=base_url,
            enrollment_token=enrollment_token,
            device_name=device_name,
        )
        return result

    def _relay_pairing_create(self, request: RemoteRequest) -> dict[str, Any]:
        current = self.relay.status()
        configuration = self.relay_configuration.resolve(
            current_base_url=str(current.get("base_url") or "")
        )
        if configuration is None:
            raise RemoteControllerError(
                "server_not_configured",
                "This installation is not connected to its server. "
                "Run 'pcrd configure-server' on the Mac to set up the connection.",
            )
        result = self.relay.create_pairing(
            base_url=configuration.base_url,
            enrollment_token=configuration.enrollment_token,
            device_name=configuration.device_name,
            force=self._boolean(request.body, "force", default=False),
        )
        self.relay_configuration.save(configuration)
        return result

    def _relay_poll(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.relay.poll_pairing()

    def _relay_clear(self, _request: RemoteRequest) -> dict[str, Any]:
        return {"cleared": self.relay.forget()}

    def _controller_status(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.controller_relay.status()

    def _controller_identity(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.controller_relay.identity()

    def _controller_machines(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.controller_relay.machines()

    def _controller_pair(self, request: RemoteRequest) -> dict[str, Any]:
        return self.controller_relay.pair(
            self._text(request.body, "pairing_code"),
            device_name=self._text(request.body, "device_name"),
        )

    def _controller_select(self, request: RemoteRequest) -> dict[str, Any]:
        return self.controller_relay.select_machine(
            self._text(request.body, "machine_id", required=False)
        )

    def _controller_completion_watches(
        self,
        _request: RemoteRequest,
    ) -> dict[str, Any]:
        return self.remote_completion_monitor.status()

    def _controller_completion_watch(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        return self.remote_completion_monitor.watch(
            machine_id=self._text(request.body, "machine_id"),
            machine_public_key=self._text(
                request.body,
                "machine_public_key",
            ),
            run_id=self._text(request.body, "run_id"),
            workspace_name=self._text(
                request.body,
                "workspace_name",
                required=False,
            ),
        )

    def _controller_completion_unwatch(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        return self.remote_completion_monitor.unwatch(
            machine_id=self._text(request.body, "machine_id"),
            run_id=self._text(request.body, "run_id"),
        )

    def _controller_call(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        target_action = self._text(body, "target_action")
        if target_action not in RelayBridge.REMOTE_ACTIONS:
            raise RemoteControllerError(
                "not_allowed",
                "This action cannot be forwarded to another computer",
            )
        target_body = body.get("target_body", {})
        if not isinstance(target_body, Mapping):
            raise RemoteControllerError(
                "invalid_argument",
                "target_body must be an object",
            )
        raw_revision = body.get("target_expected_revision")
        expected_revision = (
            None
            if raw_revision is None
            else self._integer(
                body,
                "target_expected_revision",
                minimum=0,
            )
        )
        response = self.controller_relay.call(
            machine_id=self._text(body, "machine_id"),
            machine_public_key=self._text(body, "machine_public_key"),
            action=target_action,
            body=target_body,
            expected_revision=expected_revision,
            command_id=self._text(body, "target_command_id"),
            **({"client_capabilities": request.client_capabilities} if request.client_capabilities else {}),
            timeout_seconds=self._integer(
                body,
                "timeout_seconds",
                default=30,
                minimum=1,
                maximum=15 * 60,
            ),
        )
        if not bool(response.get("ok")):
            error = response.get("error")
            error = error if isinstance(error, Mapping) else {}
            raise RemoteControllerError(
                str(error.get("code") or "remote_error"),
                str(error.get("message") or "The connected computer rejected this request"),
                details=(
                    error.get("details")
                    if isinstance(error.get("details"), Mapping)
                    else None
                ),
            )
        result = response.get("body")
        if not isinstance(result, Mapping):
            raise RemoteControllerError(
                "controller_relay_error",
                "The connected computer returned an unreadable response",
            )
        return dict(result)

    def _controller_http(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        payload = body.get("payload")
        if payload is not None and not isinstance(payload, Mapping):
            raise RemoteControllerError(
                "invalid_argument",
                "payload must be an object",
            )
        return self.controller_relay.http_request(
            method=self._text(body, "method"),
            path=self._text(body, "path"),
            body=payload,
        )

    def _controller_download_macos_update(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        return self.controller_relay.download_macos_update(
            ticket=self._text(request.body, "ticket"),
            release_id=self._text(request.body, "release_id"),
            expected_sha256=self._text(request.body, "expected_sha256"),
            expected_size=self._integer(
                request.body,
                "expected_size",
                minimum=1,
                maximum=512 * 1024 * 1024,
            ),
        )

    def _follow_up_context(
        self,
        body: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        workspace_id = self._text(body, "workspace_id")
        run_id = self._text(body, "run_id")
        workspace = self.registry.require(workspace_id)
        run = self.manager.get_run(run_id, include_events=False)
        run_workspace = str(run.get("workspace") or "").strip()
        if not run_workspace:
            raise RemoteControllerError(
                "invalid_argument",
                "The queued message's run has no workspace.",
            )
        if Path(run_workspace).expanduser().resolve() != Path(
            workspace.path
        ).expanduser().resolve():
            raise RemoteControllerError(
                "invalid_argument",
                "The queued message does not belong to this workspace.",
            )
        conversation_id = str(run.get("conversation_id") or run_id).strip()
        if run.get("worker_active") or run.get("status") in {"preparing", "running", "stopping"}:
            self.follow_up_queues.reset_selection(workspace_id, run_id)
        return workspace_id, run_id, conversation_id

    def _followups_select_agent(self, request: RemoteRequest) -> dict[str, Any]:
        workspace_id, run_id, conversation_id = self._follow_up_context(request.body)
        run = self.manager.get_run(run_id, include_events=False)
        agent = self._integer(request.body, "agent", minimum=1, maximum=MAX_AGENT_COUNT)
        if self.registry.require(workspace_id).last_run_id != run_id:
            raise RemoteControllerError("follow_up_context_conflict", "Open the latest turn before choosing where the queue continues.")
        if (run.get("worker_active")
                or run.get("status") in {"preparing", "running", "stopping", "finalizing", "sync_ambiguous", "cleanup_failed"}
                or not FollowUpQueueCoordinator._all_agents_terminal(run)):
            raise RemoteControllerError("follow_up_not_ready", "Wait for all Agents to finish before choosing where the queue continues.")
        if run.get("finalized_agent") not in {None, agent}:
            raise RemoteControllerError("follow_up_already_finalized", "The queue continues from the result already adopted.")
        if not any(candidate.get("agent") == agent for candidate in FollowUpQueueCoordinator._eligible_agents(run)):
            raise RemoteControllerError("follow_up_agent_unavailable", "This Agent cannot continue the queued conversation.")
        return self.follow_up_queues.select_agent(
            workspace_id=workspace_id, run_id=run_id,
            conversation_id=conversation_id, agent=agent,
        )

    def _followups_get(self, request: RemoteRequest) -> dict[str, Any]:
        workspace_id = self._text(request.body, "workspace_id")
        self.registry.require(workspace_id)
        return self.follow_up_queues.get(workspace_id)

    def _followups_list(self, _request: RemoteRequest) -> dict[str, Any]:
        return self.follow_up_queues.list()

    def _followups_replace(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        workspace_id, run_id, conversation_id = self._follow_up_context(body)
        raw_items = body.get("items")
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                if not canonical_follow_up_settings(item.get("settings"))["sync_back"]:
                    raise RemoteControllerError(
                        "follow_up_requires_sync_back",
                        "Queued messages require workspace sync-back.",
                    )
        selected_agent = (
            self._integer(
                body,
                "selected_agent",
                minimum=1,
                maximum=MAX_AGENT_COUNT,
            )
            if body.get("selected_agent") is not None
            else None
        )
        return self.follow_up_queues.replace(
            workspace_id=workspace_id,
            run_id=run_id,
            conversation_id=conversation_id,
            items=raw_items,
            selected_agent=selected_agent,
            reset_countdown=self._boolean(
                body,
                "reset_countdown",
                default=False,
            ),
            expected_generation=(self._integer(body, "expected_generation", minimum=0)
                                 if body.get("expected_generation") is not None else None),
        )

    def _followups_enqueue(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        workspace_id, run_id, conversation_id = self._follow_up_context(body)
        raw_item = body.get("item")
        if not isinstance(raw_item, Mapping):
            raise RemoteControllerError(
                "invalid_argument", "item must be a queued message object"
            )
        if not canonical_follow_up_settings(raw_item.get("settings"))["sync_back"]:
            raise RemoteControllerError(
                "follow_up_requires_sync_back",
                "Queued messages require workspace sync-back.",
            )
        selected_agent = (
            self._integer(
                body,
                "selected_agent",
                minimum=1,
                maximum=MAX_AGENT_COUNT,
            )
            if body.get("selected_agent") is not None
            else None
        )
        return self.follow_up_queues.enqueue(
            workspace_id=workspace_id,
            run_id=run_id,
            conversation_id=conversation_id,
            item=raw_item,
            selected_agent=selected_agent,
            ancestor_run_ids=self._follow_up_ancestors(workspace_id, run_id, conversation_id),
        )

    def _follow_up_ancestors(
        self, workspace_id: str, run_id: str, conversation_id: str,
    ) -> frozenset[str]:
        queue = self.follow_up_queues.get_internal(workspace_id)
        anchor = str((queue or {}).get("run_id") or "")
        if not anchor or anchor == run_id or (queue or {}).get("conversation_id") != conversation_id:
            return frozenset()
        workspace = Path(self.registry.require(workspace_id).path).expanduser().resolve()
        ancestors: set[str] = set()
        seen: set[str] = set()
        current = run_id
        for _ in range(256):
            if current in seen:
                return frozenset()
            seen.add(current)
            try:
                run = self.manager.get_run(current, include_events=False)
            except Exception:
                break
            if (run.get("run_id") != current or run.get("conversation_id") != conversation_id
                    or not run.get("workspace")
                    or Path(str(run["workspace"])).expanduser().resolve() != workspace):
                break
            if current != run_id:
                ancestors.add(current)
            if current == anchor:
                return frozenset(ancestors)
            current = str(run.get("continued_from_run_id") or "").strip()
            if not current:
                break
        return frozenset()

    def _followups_reconcile_enqueue(self, request: RemoteRequest) -> dict[str, Any]:
        enqueue = request.body.get("enqueue")
        if not isinstance(enqueue, Mapping):
            raise RemoteControllerError("invalid_argument", "enqueue must contain the original enqueue parameters")
        original = RemoteRequest(
            action="followups.enqueue", client_id=request.client_id,
            command_id=self._text(request.body, "enqueue_command_id"), body=dict(enqueue),
        )
        if self.command_receipt_reader is None:
            raise RemoteControllerError("follow_up_receipt_unavailable", "The original enqueue receipt is unavailable")
        receipt = self.command_receipt_reader(original)
        if receipt.get("status") != "completed" or not isinstance(receipt.get("result"), Mapping):
            # Receipt expiry/deletion is not permission to enqueue this item again.
            raise RemoteControllerError("follow_up_receipt_unavailable", "The original enqueue receipt is unavailable")
        return self.reconcile_follow_up_enqueue(original, receipt["result"])

    def reconcile_follow_up_enqueue(
        self, request: RemoteRequest, receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Called only after the journal authenticates a completed command replay
        # against its original fingerprint. No new submission is executed here.
        workspace_id = str(request.body.get("workspace_id") or "")
        original = receipt.get("queue")
        if not isinstance(original, Mapping) or not request.body.get("run_id"):
            return self.follow_up_queues.get(workspace_id)
        workspace_id, run_id, conversation_id = self._follow_up_context(request.body)
        if self.registry.require(workspace_id).last_run_id != run_id:
            return self.follow_up_queues.get(workspace_id)
        return self.follow_up_queues.reconcile_enqueue(
            workspace_id=workspace_id, run_id=run_id, conversation_id=conversation_id,
            item=request.body.get("item"), original_queue=original,
            ancestor_run_ids=self._follow_up_ancestors(workspace_id, run_id, conversation_id),
        )

    def _followups_clear(self, request: RemoteRequest) -> dict[str, Any]:
        run_id = self._text(request.body, "run_id", required=False)
        if run_id:
            workspace_id, run_id, conversation_id = self._follow_up_context(
                request.body
            )
        else:
            workspace_id = self._text(request.body, "workspace_id")
            self.registry.require(workspace_id)
            conversation_id = ""
        return self.follow_up_queues.clear(
            workspace_id,
            run_id=run_id,
            conversation_id=conversation_id,
        )

    def _runs_estimate(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _workspace_id = self._workspace(request.body)
        return self.manager.estimate(
            workspace=workspace,
            num_agents=self._integer(
                request.body,
                "num_agents",
                default=4,
                minimum=1,
                maximum=MAX_AGENT_COUNT,
            ),
            runs_dir=str(request.body.get("runs_dir") or "") or None,
            resume_session_id=(
                str(request.body.get("resume_session_id") or "") or None
            ),
            synthesis_agents=self._integer(
                request.body,
                "synthesis_agents",
                default=DEFAULT_SYNTHESIS_AGENTS,
                minimum=0,
                maximum=MAX_AGENT_COUNT,
            ),
        )

    def _runs_start(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        workspace, existing_id = self._workspace(body)
        self._ensure_run_start_context(workspace, existing_id)
        workspace_id, revision = self._register_workspace(workspace, existing_id)
        max_parallel = body.get("max_parallel")
        if max_parallel is not None:
            max_parallel = self._integer(body, "max_parallel", minimum=1)
        result = self.conversations.start_turn(
            **self._conversation_context(workspace_id),
            submission_command=CommandJournal.identity(request),
            **({"supports_agent_questions": True} if "agent_questions" in request.client_capabilities else {}),
            prompt=self._text(body, "prompt"),
            workspace=workspace,
            num_agents=self._integer(
                body,
                "num_agents",
                default=4,
                minimum=1,
                maximum=MAX_AGENT_COUNT,
            ),
            max_parallel=max_parallel,
            serial=self._boolean(body, "serial", default=False),
            recommend_by=str(body.get("recommend_by") or "reasoning_tokens"),
            model=str(body.get("model") or "") or None,
            effort=str(body.get("effort") or "") or None,
            resume_session_id=(str(body.get("resume_session_id") or "") or None),
            runs_dir=str(body.get("runs_dir") or "") or None,
            codex_bin=str(body.get("codex_bin") or "codex"),
            sync_back=self._boolean(body, "sync_back", default=True),
            keep_workspaces=self._boolean(body, "keep_workspaces", default=False),
            confirm_large_run=self._boolean(
                body, "confirm_large_run", default=False
            ),
            fast=self._optional_boolean(body, "fast"),
            synthesis_agents=self._integer(
                body,
                "synthesis_agents",
                default=DEFAULT_SYNTHESIS_AGENTS,
                minimum=0,
                maximum=MAX_AGENT_COUNT,
            ),
            follow_up_delay=self._integer(
                body,
                "follow_up_delay",
                default=DEFAULT_FOLLOW_UP_DELAY_SECONDS,
                minimum=0,
            ),
            synthesis_model=str(body.get("synthesis_model") or "") or None,
            synthesis_effort=str(body.get("synthesis_effort") or "") or None,
            synthesis_fast=self._optional_boolean(body, "synthesis_fast"),
            subagents=self._boolean(body, "subagents", default=False),
            subagents_limit=self._integer(
                body,
                "subagents_limit",
                default=DEFAULT_SUBAGENTS_LIMIT,
                minimum=1,
            ),
        )
        run_id = str(result.get("run_id") or "")
        if run_id:
            context_revision = self.registry.update_context(
                workspace_id,
                run_id=run_id,
                resume_session_id=str(body.get("resume_session_id") or ""),
            )
            if context_revision is not None:
                revision = context_revision
        return {
            **result,
            "workspace_id": workspace_id,
            "workspace_revision": revision,
        }

    def _runs_list(self, request: RemoteRequest) -> dict[str, Any]:
        options: dict[str, Any] = {
            "limit": self._integer(request.body, "limit", default=20, minimum=1),
            "cursor": self._text(request.body, "cursor", required=False),
            "max_bytes": self._integer(
                request.body,
                "max_bytes",
                default=DEFAULT_RUN_LIST_PAGE_BYTES,
                minimum=4 * 1024,
            ),
        }
        workspace = str(request.body.get("workspace") or "").strip()
        if workspace:
            options["workspace"] = workspace
        return self.manager.list_runs(**options)

    def _conversations_list(self, request: RemoteRequest) -> dict[str, Any]:
        workspace_id = str(request.body.get("workspace_id") or "").strip()
        conversation_cursor = self._text(request.body, "cursor", required=False)
        workspace = ""
        if workspace_id:
            workspace, _ = self._workspace({"workspace_id": workspace_id})
        context = self._conversation_context(workspace_id)
        if not conversation_cursor:
            # The cursor-less (first-page) call historically re-scanned every
            # run page to synchronize the conversation store. With hundreds of
            # historical runs that is many list_runs round-trips on every call.
            # Skip the full resync when runs.json has not changed since the last
            # sync for this workspace: the conversation store is already current.
            state_path = getattr(self.manager, "state_path", None)
            stamp_key = (workspace, workspace_id)
            current_stamp: tuple[int, int] | None = None
            if state_path is not None:
                try:
                    st = state_path.stat()
                    current_stamp = (st.st_mtime_ns, st.st_size)
                except OSError:
                    current_stamp = None
            if (
                state_path is not None
                and current_stamp is not None
                and current_stamp == self._conversation_runs_stamps.get(stamp_key)
            ):
                pass  # conversation store already synchronized for this runs.json
            else:
                run_cursor = ""
                seen_cursors: set[str] = set()
                while True:
                    page = self.manager.list_runs(
                        limit=100,
                        cursor=run_cursor,
                        max_bytes=DEFAULT_RUN_LIST_PAGE_BYTES,
                        workspace=workspace,
                    )
                    page_runs = [
                        run
                        for run in page.get("runs", [])
                        if isinstance(run, Mapping)
                    ]
                    self.conversations.synchronize_runs(page_runs, **context)
                    if not page.get("has_more"):
                        break
                    next_cursor = str(page.get("next_cursor") or "").strip()
                    if not next_cursor or next_cursor == run_cursor or next_cursor in seen_cursors:
                        raise PluginRunError(
                            "Run history pagination stopped making progress",
                            error_code="pcr_state_error",
                            user_message="Conversation history could not be loaded completely. Try again.",
                        )
                    seen_cursors.add(next_cursor)
                    run_cursor = next_cursor
                self._conversation_runs_stamps[stamp_key] = current_stamp
        return self.conversations.list_conversations(
            machine_id=context["machine_id"],
            workspace_id=workspace_id,
            limit=self._integer(
                request.body,
                "limit",
                default=MAX_CONVERSATION_LIST_PAGE_SIZE,
                minimum=1,
                maximum=MAX_CONVERSATION_LIST_PAGE_SIZE,
            ),
            cursor=conversation_cursor,
            max_bytes=self._integer(
                request.body,
                "max_bytes",
                default=DEFAULT_CONVERSATION_LIST_PAGE_BYTES,
                minimum=4 * 1024,
                maximum=MAX_CONVERSATION_LIST_PAGE_BYTES,
            ),
        )

    def _conversations_search(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _ = self._workspace(request.body)
        return self.manager.search_conversation_text(
            query=self._text(request.body, "query"), workspace=workspace,
            cursor=self._text(request.body, "cursor", required=False),
        )

    def _conversations_get(self, request: RemoteRequest) -> dict[str, Any]:
        return self.conversations.get_conversation(
            self._text(request.body, "conversation_id")
        )

    def _conversations_rename(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, workspace_id = self._workspace(request.body)
        title = self._text(request.body, "title")
        conversation_id = self._text(request.body, "conversation_id")
        record = self.conversations.state.conversation(conversation_id)
        resume_session_id = ""
        if record is None:
            run_id = self._text(request.body, "run_id", required=False)
            if run_id:
                run = self.manager.get_run(run_id)
                if str(Path(str(run.get("workspace") or "")).resolve()) != workspace:
                    raise ValueError("Conversation belongs to another workspace")
                result = self.conversations.synchronize_run(
                    run, **self._conversation_context(workspace_id))
                conversation_id = result["conversation_snapshot"]["conversation"]["conversation_id"]
            else:
                resume_session_id = self._text(request.body, "resume_session_id")
                if conversation_id != f"resume:{resume_session_id}":
                    raise ValueError("Conversation does not match the resume session")
                # Resolve through the existing workspace-checked session reader.
                self.manager.resume_history(workspace, resume_session_id, limit=1)
        record = self.conversations.state.rename_conversation(
            conversation_id, title, workspace_id=workspace_id,
            resume_session_id=resume_session_id, workspace_path=workspace,
            **{key: value for key, value in self._conversation_context(workspace_id).items()
               if key != "workspace_id"},
        )
        return {"conversation": record.to_mapping()}

    def _turns_get(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        result = self.conversations.get_turn(
            run_id=self._text(body, "run_id", required=False),
            turn_id=self._text(body, "turn_id", required=False),
            **self._conversation_context(
                str(body.get("workspace_id") or "").strip()
            ),
            include_events=self._boolean(body, "include_events", default=False),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            event_limit=self._integer(body, "event_limit", default=50, minimum=1),
            event_max_bytes=self._integer(
                body,
                "event_max_bytes",
                default=DEFAULT_EVENT_PAGE_BYTES,
                minimum=4 * 1024,
            ),
        )
        return self._refresh_workspace_context_from_turn(result)

    def _runs_get(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        result = self.conversations.get_turn(
            run_id=self._text(body, "run_id"),
            **self._conversation_context(
                str(body.get("workspace_id") or "").strip()
            ),
            include_events=self._boolean(body, "include_events", default=False),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            event_limit=self._integer(body, "event_limit", default=50, minimum=1),
            event_max_bytes=self._integer(
                body,
                "event_max_bytes",
                default=DEFAULT_EVENT_PAGE_BYTES,
                minimum=4 * 1024,
            ),
        )
        return self._refresh_workspace_context_from_turn(result)

    def _refresh_workspace_context_from_turn(
        self,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = str(result.get("workspace") or "").strip()
        run_id = str(result.get("run_id") or "").strip()
        workspace_id = ""
        if workspace:
            resolved_workspace = str(Path(workspace).expanduser().resolve())
            registry = self.registry.list()
            record = next(
                (
                    item
                    for item in registry.get("workspaces", [])
                    if str(item.get("path") or "") == resolved_workspace
                ),
                None,
            )
            if isinstance(record, Mapping):
                workspace_id = str(record.get("workspace_id") or "").strip()
        snapshot = result.get("conversation_snapshot")
        snapshot_workspace = (
            snapshot.get("workspace") if isinstance(snapshot, Mapping) else None
        )
        snapshot_workspace_id = (
            str(snapshot_workspace.get("workspace_id") or "").strip()
            if isinstance(snapshot_workspace, Mapping)
            else ""
        )
        if (
            workspace_id
            and snapshot_workspace_id != workspace_id
            and isinstance(result.get("agents"), list)
        ):
            result = self.conversations.synchronize_run(
                result,
                **self._conversation_context(workspace_id),
            )
        if workspace_id:
            result["workspace_id"] = workspace_id

        promotion = result.get("session_promotion")
        session_id = (
            str(promotion.get("session_id") or "").strip()
            if isinstance(promotion, dict)
            else ""
        )
        if session_id and workspace and run_id:
            workspace_id, revision = self._update_workspace_run_context(
                workspace,
                run_id=run_id,
                resume_session_id=session_id,
                only_if_current_run=True,
            )
            if workspace_id:
                result["workspace_id"] = workspace_id
                result["workspace_revision"] = revision
        return result

    def _runs_events(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        raw_agent = body.get("agent")
        return self.manager.get_events(
            self._text(body, "run_id"),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            limit=self._integer(body, "limit", default=100, minimum=1),
            max_bytes=self._integer(
                body,
                "max_bytes",
                default=DEFAULT_EVENT_PAGE_BYTES,
                minimum=4 * 1024,
            ),
            agent=(
                self._integer(body, "agent", minimum=1)
                if raw_agent is not None
                else None
            ),
        )

    def _runs_history(self, request: RemoteRequest) -> dict[str, Any]:
        return self.manager.run_history(
            self._text(request.body, "run_id"),
            cursor=self._integer(request.body, "cursor", default=0, minimum=0),
            limit=self._integer(request.body, "limit", default=50, minimum=1),
            max_bytes=self._integer(
                request.body,
                "max_bytes",
                default=DEFAULT_HISTORY_PAGE_BYTES,
                minimum=4 * 1024,
            ),
            conversation_items_only=self._boolean(
                request.body,
                "conversation_items_only",
                default=False,
            ),
        )

    def _runs_agent(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        run_id = self._text(body, "run_id")
        agent = self._integer(body, "agent", minimum=1)
        transcript_items_only = self._boolean(
            body,
            "transcript_items_only",
            default=False,
        )
        conversation_items_only = self._boolean(
            body,
            "conversation_items_only",
            default=False,
        )
        result = self.manager.get_agent(
            run_id,
            agent,
            include_diff=self._boolean(body, "include_diff", default=False),
            include_events=self._boolean(body, "include_events", default=True),
            include_final_message=not transcript_items_only,
            include_final_message_page=transcript_items_only,
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            event_limit=self._integer(body, "event_limit", default=100, minimum=1),
            event_max_bytes=self._integer(
                body,
                "event_max_bytes",
                default=DEFAULT_EVENT_PAGE_BYTES,
                minimum=4 * 1024,
            ),
            final_message_cursor=self._integer(
                body,
                "final_message_cursor",
                default=0,
                minimum=0,
            ),
            final_message_max_bytes=self._integer(
                body,
                "final_message_max_bytes",
                default=DEFAULT_FINAL_MESSAGE_PAGE_BYTES,
                minimum=4 * 1024,
            ),
            transcript_kinds=(
                {"output", "warning", "error"}
                if conversation_items_only
                else None
            ),
        )
        page = transcript_page(result.get("event_page"))
        if page is not None:
            outputs = [item for item in page.get("items", [])
                       if isinstance(item, dict) and item.get("kind") == "output"]
            if outputs:
                # Resolve the accepted workspace once per page, not once per
                # streamed fragment under the manager's global state lock.
                texts = self.manager.rewrite_agent_visible_texts(
                    run_id, agent, [str(item.get("text") or "") for item in outputs])
                for item, text in zip(outputs, texts, strict=True):
                    item["text"] = text
            result["transcript_page"] = page
            if transcript_items_only:
                result.pop("event_page", None)
        return result

    def _runs_operation(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.get_operation_details(
            self._text(body, "run_id"),
            self._integer(body, "agent", minimum=1),
            self._integer(body, "event_id", minimum=0),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            max_bytes=self._integer(body, "max_bytes", default=32 * 1024, minimum=4096),
        )

    def _runs_diff(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.get_diff(
            self._text(body, "run_id"),
            self._integer(body, "agent", minimum=1),
            cursor=self._integer(body, "cursor", default=0, minimum=0),
            limit=self._integer(body, "limit", default=20_000, minimum=1),
            full_context=self._boolean(body, "full_context", default=False),
            snapshot_id=str(body.get("snapshot_id") or ""),
        )

    def _runs_file_preview(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.get_file_preview(
            self._text(body, "run_id"), self._integer(body, "agent", minimum=1),
            self._text(body, "path"), side=self._text(body, "side", required=False),
            file_id=self._text(body, "file_id", required=False),
            offset=self._integer(body, "offset", default=0, minimum=0),
            max_bytes=self._integer(body, "max_bytes", default=MAX_WORKSPACE_FILE_CHUNK_BYTES,
                                    minimum=1, maximum=MAX_WORKSPACE_FILE_CHUNK_BYTES),
        )

    def _storage_summary(self, request: RemoteRequest) -> dict[str, Any]:
        return self.manager.storage_summary(
            limit=self._integer(request.body, "limit", default=50, minimum=1)
        )

    def _runs_stop(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        run_id = self._text(body, "run_id")
        self.manager.get_run(run_id, include_events=False)
        self.follow_up_queues.pause_for_run(run_id)
        return self.manager.stop_run(
            run_id,
            wait_seconds=float(body.get("wait_seconds", 30.0)),
        )

    def _runs_question(self, request: RemoteRequest) -> dict[str, Any]:
        from ..agent_questions import QuestionError

        body = request.body
        try:
            return self.manager.get_question(self._text(body, "run_id"),
                self._integer(body, "agent", minimum=1), self._text(body, "request_id"))
        except QuestionError as exc:
            raise RemoteControllerError("question_conflict", str(exc)) from exc

    def _runs_answer_question(self, request: RemoteRequest) -> dict[str, Any]:
        from ..agent_questions import QuestionError

        body = request.body
        try:
            return self.manager.answer_question(
                self._text(body, "run_id"), self._integer(body, "agent", minimum=1),
                self._text(body, "request_id"), self._text(body, "connection_id"),
                self._text(body, "answer_id"), body.get("answers"),
            )
        except QuestionError as exc:
            raise RemoteControllerError("question_conflict", str(exc)) from exc

    def _runs_kill_agent(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.kill_agent(
            self._text(body, "run_id"),
            self._integer(body, "agent", minimum=1),
        )

    def _runs_retry_agent(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        options = {}
        if "refresh_startup_runtime" in body:
            options["refresh_startup_runtime"] = self._boolean(body, "refresh_startup_runtime", default=False)
        return self.manager.retry_agent(
            self._text(body, "run_id"),
            self._integer(body, "agent", minimum=1),
            **options,
        )

    def _runs_reject_agent(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.reject_agent(
            self._text(body, "run_id"),
            self._integer(body, "agent", minimum=1),
            rejected=self._boolean(body, "rejected", default=True),
        )

    def _runs_add_agents(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.add_agents(
            self._text(body, "run_id"),
            self._integer(
                body,
                "count",
                minimum=1,
                maximum=MAX_AGENT_COUNT,
            ),
            confirm_large_run=self._boolean(
                body, "confirm_large_run", default=False
            ),
        )

    def _runs_update_synthesis(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.update_synthesis_settings(
            self._text(body, "run_id"),
            self._integer(
                body,
                "synthesis_agents",
                minimum=0,
                maximum=MAX_AGENT_COUNT,
            ),
            model=str(body.get("synthesis_model") or "") or None,
            effort=str(body.get("synthesis_effort") or "") or None,
            fast=self._optional_boolean(body, "synthesis_fast"),
        )

    def _runs_accept_agent(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        candidate = str(body.get("candidate_id") or "").strip()
        run_id_value = self._text(body, "run_id", required=not candidate)
        agent_value = (
            self._integer(body, "agent", minimum=1)
            if "agent" in body
            else None
        )
        if not candidate and agent_value is None:
            raise RemoteControllerError(
                "invalid_argument",
                "agent is required when candidate_id is not provided",
            )
        result = self.conversations.finalize_candidate(
            run_id=run_id_value,
            agent=agent_value,
            candidate_id=candidate,
            **self._conversation_context(
                str(body.get("workspace_id") or "").strip()
            ),
            wait_seconds=float(body.get("wait_seconds", 45.0)),
            keep_workspaces=(
                self._boolean(body, "keep_workspaces")
                if "keep_workspaces" in body
                else None
            ),
            sync_back=(
                self._boolean(body, "sync_back")
                if "sync_back" in body
                else None
            ),
        )
        workspace = str(result.get("workspace") or "").strip()
        run_id = str(result.get("run_id") or "").strip()
        promotion = result.get("session_promotion")
        session_id = (
            str(promotion.get("session_id") or "").strip() or None
            if isinstance(promotion, dict)
            else None
        )
        if workspace and run_id:
            # Finalization can finish after a continuation has already started.
            # Complete the old result without moving the workspace back to it.
            workspace_id, revision = self._update_workspace_run_context(
                workspace,
                run_id=run_id,
                resume_session_id=session_id,
                only_if_current_run=True,
                initialize_if_unbound=True,
            )
            if workspace_id:
                result["workspace_id"] = workspace_id
                result["workspace_revision"] = revision
        if session_id and workspace:
            prompt = str(result.get("prompt") or "").strip()
            if prompt:
                self.prompt_history.append(
                    workspace,
                    session_id,
                    prompt,
                    deduplicate_last=True,
                )
        return result

    def _runs_discard(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        return self.manager.discard_run(
            self._text(body, "run_id"),
            keep_workspaces=self._boolean(
                body, "keep_workspaces", default=False
            ),
            wait_seconds=float(body.get("wait_seconds", 30.0)),
        )

    def _storage_cleanup(self, request: RemoteRequest) -> dict[str, Any]:
        body = request.body
        run_id = self._text(body, "run_id")
        run = self.manager.get_run(run_id)
        finalized = run.get("finalized_agent") is not None or str(
            run.get("status") or ""
        ) in {"finalized", "discarded", "expired"}
        if finalized:
            return self.manager.cleanup_run_storage(run_id)
        if not self._boolean(body, "discard_unselected", default=False):
            raise RemoteControllerError(
                "confirmation_required",
                "Cleaning this run will discard every unselected Agent result",
            )
        return self.manager.discard_run(
            run_id,
            keep_workspaces=False,
            wait_seconds=float(body.get("wait_seconds", 30.0)),
        )

    def _runs_recover_finalization(
        self,
        request: RemoteRequest,
    ) -> dict[str, Any]:
        body = request.body
        if request.action == "runs.verify_finalization":
            return self.manager.recover_finalization(self._text(body, "run_id"), None)
        if "sync_was_applied" not in body:
            raise RemoteControllerError(
                "invalid_argument",
                "sync_was_applied is required when recovering finalization",
            )
        return self.manager.recover_finalization(
            self._text(body, "run_id"),
            self._boolean(body, "sync_was_applied", default=False),
        )

    def _runs_continue(self, request: RemoteRequest, *, require_unstarted_continuation: bool = False) -> dict[str, Any]:
        body = request.body
        run_id = self._text(body, "run_id")
        self._ensure_continuation_context(run_id)
        raw_agents = body.get("num_agents")
        raw_parallel = body.get("max_parallel")
        overrides: dict[str, Any] = {}
        if request.client_id != "pcrd-follow-up":
            overrides["supports_agent_questions"] = "agent_questions" in request.client_capabilities
        if "automatic_max_parallel" in body and self._boolean(
            body,
            "automatic_max_parallel",
            default=False,
        ):
            overrides["max_parallel"] = None
        elif raw_parallel is not None:
            overrides["max_parallel"] = self._integer(
                body,
                "max_parallel",
                minimum=1,
            )
        if "serial" in body:
            overrides["serial"] = self._boolean(body, "serial", default=False)
        if "recommend_by" in body:
            overrides["recommend_by"] = str(
                body.get("recommend_by") or "reasoning_tokens"
            )
        for key in (
            "model",
            "effort",
            "synthesis_model",
            "synthesis_effort",
            "runs_dir",
        ):
            if key in body:
                overrides[key] = str(body.get(key) or "").strip() or None
        if "codex_bin" in body:
            overrides["codex_bin"] = str(body.get("codex_bin") or "codex").strip()
        for request_key, config_key in (
            ("fast_mode", "fast"),
            ("synthesis_fast_mode", "synthesis_fast"),
        ):
            if request_key in body:
                mode = str(body.get(request_key) or "automatic").strip().lower()
                try:
                    overrides[config_key] = {
                        "automatic": None,
                        "on": True,
                        "off": False,
                    }[mode]
                except KeyError as exc:
                    raise RemoteControllerError(
                        "invalid_argument",
                        f"{request_key} must be automatic, on, or off",
                    ) from exc
        if "synthesis_agents" in body:
            overrides["synthesis_agents"] = self._integer(
                body,
                "synthesis_agents",
                minimum=0,
                maximum=MAX_AGENT_COUNT,
            )
        if "follow_up_delay" in body:
            overrides["follow_up_delay"] = self._integer(
                body,
                "follow_up_delay",
                minimum=0,
            )
        if "sync_back" in body:
            overrides["sync_back"] = self._boolean(
                body,
                "sync_back",
                default=True,
            )
        if "keep_workspaces" in body:
            overrides["keep_workspaces"] = self._boolean(
                body,
                "keep_workspaces",
                default=False,
            )
        if "subagents" in body:
            overrides["subagents"] = self._boolean(
                body,
                "subagents",
                default=False,
            )
        if "subagents_limit" in body:
            overrides["subagents_limit"] = self._integer(
                body,
                "subagents_limit",
                minimum=1,
            )
        result = self.conversations.continue_turn(
            run_id=run_id,
            agent=self._integer(body, "agent", minimum=1),
            submission_command=CommandJournal.identity(request),
            prompt=self._text(body, "prompt"),
            **self._conversation_context(
                str(body.get("workspace_id") or "").strip()
            ),
            num_agents=(
                self._integer(
                    body,
                    "num_agents",
                    minimum=1,
                    maximum=MAX_AGENT_COUNT,
                )
                if raw_agents is not None
                else None
            ),
            max_parallel=(
                self._integer(body, "max_parallel", minimum=1)
                if raw_parallel is not None
                else None
            ),
            config_overrides=overrides,
            confirm_large_run=self._boolean(
                body, "confirm_large_run", default=False
            ),
            **({"require_unstarted_continuation": True} if require_unstarted_continuation else {}),
        )
        run_id = str(result.get("run_id") or "").strip()
        run = result.get("run")
        workspace = (
            str(run.get("workspace") or "").strip()
            if isinstance(run, dict)
            else ""
        )
        continued_from = result.get("continued_from")
        session_id = (
            str(continued_from.get("session_id") or "").strip() or None
            if isinstance(continued_from, dict)
            else None
        )
        if run_id and workspace:
            workspace_id, revision = self._update_workspace_run_context(
                workspace,
                run_id=run_id,
                resume_session_id=session_id,
            )
            result["workspace_id"] = workspace_id
            result["workspace_revision"] = revision
        if session_id and workspace and isinstance(continued_from, dict):
            source_prompt = str(continued_from.get("prompt") or "").strip()
            if source_prompt:
                self.prompt_history.append(
                    workspace,
                    session_id,
                    source_prompt,
                    deduplicate_last=True,
                )
        return result

    def _resume_sessions(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _workspace_id = self._workspace(request.body)
        return self.manager.resume_sessions(
            workspace,
            include_non_interactive=self._boolean(
                request.body,
                "include_non_interactive",
                default=False,
            ),
            limit=self._integer(request.body, "limit", default=20, minimum=1),
        )

    def _resume_history(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _workspace_id = self._workspace(request.body)
        return self.manager.resume_history(
            workspace,
            self._text(request.body, "session_id"),
            cursor=self._integer(request.body, "cursor", default=0, minimum=0),
            limit=self._integer(request.body, "limit", default=50, minimum=1),
            max_bytes=self._integer(
                request.body,
                "max_bytes",
                default=DEFAULT_HISTORY_PAGE_BYTES,
                minimum=4 * 1024,
            ),
            conversation_items_only=self._boolean(
                request.body,
                "conversation_items_only",
                default=False,
            ),
        )

    def _model_options(self, request: RemoteRequest) -> dict[str, Any]:
        return self.manager.model_options(
            model=str(request.body.get("model") or "") or None,
            effort=str(request.body.get("effort") or "") or None,
        )

    def _account_usage(self, request: RemoteRequest) -> dict[str, Any]:
        from ..codex_auth import selected_home

        settings = self.workspace_config.load_defaults()
        codex_bin = settings.codex_bin if settings and settings.codex_bin else "codex"
        if request.body.get("workspace_id"):
            workspace, _ = self._workspace(request.body)
            settings = self.workspace_config.load(workspace)
            if settings and settings.codex_bin:
                codex_bin = settings.codex_bin
        value = self.codex_usage.read(
            codex_bin, selected_home(),
            force=self._boolean(request.body, "refresh", default=False),
        )
        execution_status = getattr(self.manager, "account_execution_status", None)
        if callable(execution_status):
            value = {**value, "execution_accounts": execution_status()}
        return value

    def _prompt_history_list(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _workspace_id = self._workspace(request.body)
        session_id = str(request.body.get("session_id") or "").strip() or None
        return {
            "workspace": workspace,
            "session_id": session_id,
            "entries": self.prompt_history.entries(workspace, session_id),
        }

    def _prompt_history_append(self, request: RemoteRequest) -> dict[str, Any]:
        workspace, _workspace_id = self._workspace(request.body)
        session_id = str(request.body.get("session_id") or "").strip() or None
        prompt = self._text(request.body, "prompt")
        if not self.prompt_history.append(workspace, session_id, prompt):
            raise RemoteControllerError(
                "history_error",
                "The prompt could not be added to history",
            )
        return {
            "workspace": workspace,
            "session_id": session_id,
            "entries": self.prompt_history.entries(workspace, session_id),
        }

    def _feedback_submit(self, request: RemoteRequest) -> dict[str, Any]:
        diagnostics = request.body.get("diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            raise RemoteControllerError(
                "invalid_argument",
                "diagnostics must be an object",
            )
        return {
            "feedback": self.feedback.submit(
                client_id=request.client_id,
                client_feedback_id=self._text(
                    request.body,
                    "client_feedback_id",
                ),
                kind=self._text(request.body, "kind"),
                description=self._text(request.body, "description"),
                diagnostics=diagnostics,
            )
        }

    def _feedback_list(self, request: RemoteRequest) -> dict[str, Any]:
        return self.feedback.list(
            client_id=self._feedback_client_scope(request),
            cursor=self._integer(request.body, "cursor", default=0, minimum=0),
            limit=self._integer(request.body, "limit", default=20, minimum=1),
            status=str(request.body.get("status") or ""),
            app_version=str(request.body.get("app_version") or ""),
            device_id=str(request.body.get("device_id") or ""),
            workspace_id=str(request.body.get("workspace_id") or ""),
        )

    def _feedback_get(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "feedback": self.feedback.get(
                self._text(request.body, "feedback_id"),
                client_id=self._feedback_client_scope(request),
            )
        }

    def _feedback_add_note(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "feedback": self.feedback.add_note(
                self._text(request.body, "feedback_id"),
                self._text(request.body, "text"),
                client_id=self._feedback_client_scope(request),
            )
        }

    def _feedback_add_attachment(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "feedback": self.feedback.add_attachment(
                self._text(request.body, "feedback_id"),
                client_id=self._feedback_client_scope(request),
                content_type=self._text(request.body, "content_type"),
                encoded=self._text(request.body, "data"),
            )
        }

    def _feedback_attachment(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "attachment": self.feedback.attachment(
                self._text(request.body, "feedback_id"),
                self._text(request.body, "attachment_id"),
                client_id=self._feedback_client_scope(request),
            )
        }

    def _feedback_remove_attachment(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "feedback": self.feedback.remove_attachment(
                self._text(request.body, "feedback_id"),
                self._text(request.body, "attachment_id"),
                client_id=self._feedback_client_scope(request),
            )
        }

    def _feedback_withdraw(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "feedback": self.feedback.withdraw(
                self._text(request.body, "feedback_id"),
                client_id=self._feedback_client_scope(request),
            )
        }

    @staticmethod
    def _feedback_client_scope(request: RemoteRequest) -> str | None:
        return (
            None
            if request.client_id in {"local", "local-cli"}
            else request.client_id
        )

    def _feedback_admin_update(self, request: RemoteRequest) -> dict[str, Any]:
        if request.client_id not in {"local", "local-cli"}:
            raise RemoteControllerError(
                "not_allowed",
                "feedback administration is available only on this machine",
            )
        return {
            "feedback": self.feedback.admin_update(
                self._text(request.body, "feedback_id"),
                status=self._text(request.body, "status"),
                developer_reply=self._text(
                    request.body,
                    "developer_reply",
                    required=False,
                ),
                fixed_version=self._text(
                    request.body,
                    "fixed_version",
                    required=False,
                ),
            )
        }
