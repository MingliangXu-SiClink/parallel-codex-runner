from __future__ import annotations

import argparse
import re
import shlex
import threading
import time
from pathlib import Path

from ._vendor import activate_textual
from .app import (
    get_codex_home,
    load_codex_session_history,
    normalize_recommend_by,
    subagent_resume_error,
)
from .codex_cli import format_fast_mode
from .diffing import build_workspace_diff_text
from .models import (
    AGENT_ROLE_CANDIDATE,
    DEFAULT_SUBAGENTS_LIMIT,
    CodexHistoryEntry,
)
from .limits import MAX_AGENT_COUNT
from .paths import absolute_path_for_display, choose_run_base
from .synthesis import (
    effective_synthesis_codex_settings,
    normalize_synthesis_effort,
    normalize_synthesis_fast,
    normalize_synthesis_model,
)
from .tui_common import (
    DETAIL_AGENT_MARKER,
    DETAIL_INPUT_MARKER,
    HELP_TEXT,
    SYNTHESIS_INFO_LABELS,
    UNKNOWN_RESUME_MODEL,
    fast_control_value,
    fast_setting_value,
)
from .tui_components import (
    AgentDiffLoaded,
    AgentPane,
    CandidateBatch,
    QueueEditorResult,
    QueueEditorRow,
    QueueEditorScreen,
    QueuedFollowUp,
    ResumeHistoryLoaded,
)

activate_textual()

from textual.widgets import Select


class PcrTextualCommandMixin:
    def _command_error(self, message: str) -> None:
        self.status = message
        self.command_history.append(("✗", message, "red"))
        self._mark_detail_dirty()
        self._sync()

    def _handle_command(self, raw: str) -> None:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            self._command_error(f"Command parse error: {exc}")
            return
        if not parts:
            return
        name = parts[0].lower()
        args = parts[1:]
        if name in {"/exit", "/quit"}:
            self._request_exit()
            return
        if name == "/help":
            self._show_text(HELP_TEXT)
            return
        if name in {"/status", "/config"}:
            self._show_status()
            return
        if self.storage_preflight_inflight:
            self.status = "Storage check in progress; use /exit to cancel"
            self._sync()
            return
        if name == "/clear":
            self.action_clear_view()
            return
        if name == "/accept":
            self._handle_accept(args)
            return
        if name == "/reject":
            self._handle_reject(args)
            return
        if name == "/retry":
            self._handle_retry(args)
            return
        if name == "/more":
            self._handle_more(args)
            return
        if name == "/queue":
            self._handle_queue(args)
            return
        if name in {"/followupdelay", "/follow-up-delay"}:
            self._handle_followupdelay(args)
            return
        if name in {"/synthesis", "/synthesisagents", "/synthesis-agents"}:
            self._handle_synthesis(args)
            return
        if name == "/diff":
            self._handle_diff(args)
            return
        if name in {"/kill", "/killagent", "/kill-agent"}:
            self._handle_kill(args)
            return
        if name in {"/numofagents", "/agents"}:
            self._handle_numofagents(args)
            return
        if name in {"/maxparallel", "/max-parallel"}:
            self._handle_maxparallel(args)
            return
        if name in {"/subagents", "/sub-agents"}:
            self._handle_subagents(args)
            return
        if name in {
            "/subagentslimit",
            "/subagents-limit",
            "/sub-agents-limit",
        }:
            self._handle_subagentslimit(args)
            return
        if name == "/serial":
            self._handle_execution(serial=True)
            return
        if name == "/parallel":
            self._handle_execution(serial=False)
            return
        if name in {"/recommendby", "/recommend-by"}:
            self._handle_recommendby(args)
            return
        if name == "/model":
            self._handle_model(args)
            return
        if name == "/effort":
            self._handle_effort(args)
            return
        if name == "/fast":
            self._handle_fast(args)
            return
        if name in {"/synthesismodel", "/synthesis-model"}:
            self._handle_synthesis_model(args)
            return
        if name in {"/synthesiseffort", "/synthesis-effort"}:
            self._handle_synthesis_effort(args)
            return
        if name in {"/synthesisfast", "/synthesis-fast"}:
            self._handle_synthesis_fast(args)
            return
        if name == "/workspace":
            self._handle_workspace(args)
            return
        if name in {"/runsdir", "/runs-dir"}:
            self._handle_runsdir(args)
            return
        if name in {"/codexbin", "/codex-bin"}:
            self._handle_codexbin(args)
            return
        if name in {"/syncback", "/sync-back"}:
            self._handle_syncback(args)
            return
        if name in {"/keepworkspaces", "/keep-workspaces"}:
            self._handle_keepworkspaces(args)
            return
        if name in {"/promptfile", "/prompt-file", "/runfile"}:
            self._handle_promptfile(args)
            return
        if name in {"/resumeinclude", "/resume-include", "/resume-include-non-interactive"}:
            self._handle_resumeinclude(args)
            return
        if name == "/resume":
            self._handle_resume(args)
            return
        self._command_error(f"Unknown command: {name}")

    def _handle_accept(self, args: list[str]) -> None:
        if args:
            self._command_error("Usage: /accept")
            return
        if (
            self.follow_up_queue
            and self.follow_up_source_finalized
            and not self.running
        ):
            self._dispatch_follow_up()
            return
        pane = self.agents.get(self.selected_agent)
        result = pane.result if pane is not None else None
        if not self._has_pending_run() or not isinstance(result, dict):
            self.status = "No finished agent is available to accept"
            self._sync()
            return
        if result.get("status") != "success":
            self.status = f"AGENT-{self.selected_agent:03d} is not successful"
            self._sync()
            return
        if self._pending_sync_disabled():
            self.status = "Cannot accept while sync back is disabled"
            self._sync()
            return
        if self.running:
            self.pending_accept_agent = self.selected_agent
            self.queued_prompt = ""
            self.queued_agent = None
            self.queued_prompt_records_history = False
            self.queued_prompt_recorded_history_context = None
            self._discard_queued_candidate_batches()
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.status = f"Stopping remaining agents; accepting AGENT-{self.selected_agent:03d}"
            self._sync()
            return
        accepted_idx = self.selected_agent
        if self._finalize_agent(accepted_idx, archive_detail=True):
            if self.follow_up_queue:
                self._rebind_follow_up_queue()
                self.follow_up_source_finalized = True
                self._dispatch_follow_up()
            else:
                self.status = f"Accepted AGENT-{accepted_idx:03d}"
        self._sync()

    def _handle_reject(self, args: list[str]) -> None:
        if args:
            self._command_error("Usage: /reject")
            return
        pane = self.agents.get(self.selected_agent)
        if pane is None:
            self._command_error(f"Unknown agent: {self.selected_agent}")
            return
        if pane.rejected:
            self.status = f"AGENT-{pane.idx:03d} is already excluded from recommendations"
            self._sync()
            return
        pane.rejected = True
        if not self.running or self.recommended_agent is not None:
            self._recompute_recommendation()
        if self.selected_agent == pane.idx:
            recommended = self.recommended_agent
            if recommended is not None:
                recommended_pane = self.agents.get(recommended)
                if recommended_pane is not None and not recommended_pane.rejected:
                    self.selected_agent = recommended
        self.status = f"AGENT-{pane.idx:03d} excluded from recommendations"
        self._mark_detail_dirty(pane)
        self._sync()

    def _handle_retry(self, args: list[str]) -> None:
        if len(args) > 1:
            self._command_error("Usage: /retry [agent]")
            return
        idx = self.selected_agent
        if args:
            match = re.fullmatch(r"(?:agent[-_])?(\d+)", args[0].strip(), re.IGNORECASE)
            if match is None:
                self._command_error("Usage: /retry [agent]")
                return
            idx = int(match.group(1))
        pane = self.agents.get(idx)
        result = pane.result if pane is not None else None
        if pane is None:
            self._command_error(f"Unknown agent: {idx}")
            return
        if self._agent_has_pending_batch(idx):
            self.status = f"AGENT-{idx:03d} is already running or queued"
            self._sync()
            return
        result_status = result.get("status") if isinstance(result, dict) else pane.status
        if result_status not in {"failed", "error", "killed"}:
            self.status = f"AGENT-{idx:03d} is not failed or killed"
            self._sync()
            return
        if not self._can_extend_current_run():
            self.status = "No current question is available to retry"
            self._sync()
            return
        if not self.running and not self.candidate_batches:
            self.completion_notification_sent = False
        live_retry = (
            self.running
            and pane.role == AGENT_ROLE_CANDIDATE
            and self.live_candidate_coordinator is not None
            and self.live_candidate_coordinator.request_retry(idx)
        )
        if live_retry:
            running_before_retry = self._active_agent_count()
            self._record_candidate_change(live=True)
            self.live_candidate_indices.add(idx)
            self._prepare_retry_pane(pane)
            if running_before_retry < self._current_parallel_limit():
                self.status = f"Retrying AGENT-{idx:03d} now"
            else:
                self.status = (
                    f"Retry queued for AGENT-{idx:03d} in the current "
                    "candidate stage"
                )
            self._sync()
            return

        if pane.role == AGENT_ROLE_CANDIDATE:
            self._record_candidate_change(live=False)
        self.candidate_batches.append(
            CandidateBatch(
                [idx],
                {idx},
                role=pane.role,
                prompt=pane.execution_prompt or self.pending_prompt,
                developer_instructions=pane.developer_instructions,
            )
        )
        pane.status = "retry queued"
        self._mark_detail_dirty(pane)
        if self.running:
            self.status = f"Retry queued for AGENT-{idx:03d}"
            self._sync()
        else:
            self._launch_next_pending_stage()

    def _handle_more(self, args: list[str]) -> None:
        if getattr(self.pending_execution_args, "workspace_mode", "isolated") == "source":
            self._command_error("This turn runs in the source folder. Start a new turn to use multiple Agents.")
            return
        if len(args) != 1:
            self._command_error("Usage: /more <positive integer>")
            return
        try:
            count = int(args[0])
        except ValueError:
            count = 0
        if count <= 0:
            self._command_error("Usage: /more <positive integer>")
            return
        if not self._can_extend_current_run():
            self.status = "No current question is available for more candidates"
            self._sync()
            return

        reserved_indices = set(self.agents) | set(self.agent_cancel_events)
        start = max(reserved_indices, default=0) + 1
        indices = list(range(start, start + count))
        for idx in indices:
            self.agents[idx] = AgentPane(
                idx=idx,
                status="queued",
                input_text=self.pending_prompt,
                execution_prompt=self.pending_prompt,
            )
            self.agent_cancel_events[idx] = threading.Event()
        if not self.running and not self.candidate_batches:
            self.completion_notification_sent = False
        live_more = (
            self.running
            and self.live_candidate_coordinator is not None
            and self.live_candidate_coordinator.request_additional(indices)
        )
        self._record_candidate_change(live=live_more)
        if live_more:
            running = self._active_agent_count()
            self.live_candidate_indices.update(indices)
            self._mark_detail_dirty()
            if running < self._current_parallel_limit():
                self.status = f"Starting {count} additional candidates"
            else:
                self.status = (
                    f"Queued {count} additional candidates in the current "
                    "candidate stage"
                )
            self._sync()
            return

        self.candidate_batches.append(CandidateBatch(indices))
        self._mark_detail_dirty()
        if self.running:
            self.status = f"Queued {count} additional candidates"
            self._sync()
        else:
            self._launch_next_pending_stage()

    def _handle_diff(self, args: list[str]) -> None:
        if args and args != ["refresh"]:
            self._command_error("Usage: /diff [refresh]")
            return
        pane = self.agents.get(self.selected_agent)
        if pane is None:
            self._command_error(f"Unknown agent: {self.selected_agent}")
            return
        if pane.show_diff and not args:
            pane.diff_request += 1
            pane.show_diff = False
            pane.diff_loading = False
            self.status = f"Showing AGENT-{pane.idx:03d} conversation"
            self._mark_detail_dirty(pane)
            self._sync()
            return
        result = pane.result if isinstance(pane.result, dict) else {}
        workspace_dir = result.get("workspace_dir")
        if not isinstance(workspace_dir, str) or not workspace_dir:
            self.status = f"AGENT-{pane.idx:03d} diff is available after it finishes"
            self._sync()
            return

        pane.diff_request += 1
        request_id = pane.diff_request
        pane.show_diff = True
        pane.diff_loading = True
        pane.diff_text = ""
        pane.diff_error = ""
        baseline = self._pending_workspace_path()
        candidate = Path(workspace_dir)
        if candidate.resolve() == baseline.resolve() and self.pending_run_root:
            baseline = Path(self.pending_run_root) / "workspaces" / "baseline"
        self.status = f"Loading AGENT-{pane.idx:03d} diff"
        self._mark_detail_dirty(pane)
        self._sync()

        def target() -> None:
            text = ""
            error = ""
            try:
                text = build_workspace_diff_text(baseline, candidate)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            try:
                self.call_from_thread(
                    self.post_message,
                    AgentDiffLoaded(pane.idx, request_id, text, error),
                )
            except RuntimeError:
                pass

        threading.Thread(target=target, name=f"pcr-diff-{pane.idx:03d}", daemon=True).start()

    def _handle_kill(self, args: list[str]) -> None:
        if len(args) > 1:
            self._command_error("Usage: /kill [agent]")
            return
        if not self.running:
            self.status = "No active run"
            self._sync()
            return
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.status = "The run is already stopping"
            self._sync()
            return

        idx = self.selected_agent
        if args:
            match = re.fullmatch(r"(?:agent[-_])?(\d+)", args[0].strip(), re.IGNORECASE)
            if match is None:
                self._command_error("Usage: /kill [agent]")
                return
            idx = int(match.group(1))
        pane = self.agents.get(idx)
        if pane is None:
            self._command_error(f"Unknown agent: {idx}")
            return
        if pane.result is not None:
            self.status = f"AGENT-{idx:03d} has already finished"
            self._sync()
            return

        agent_cancel_event = self.agent_cancel_events.get(idx)
        if agent_cancel_event is None:
            self.status = f"AGENT-{idx:03d} cannot be stopped in this run"
            self._sync()
            return
        if agent_cancel_event.is_set():
            self.status = f"AGENT-{idx:03d} is already stopping"
            self._sync()
            return
        if pane.status != "running":
            self.status = f"AGENT-{idx:03d} is not running; queued agents will start normally"
            self._sync()
            return

        agent_cancel_event.set()
        pane.status = "stopping"
        self.status = f"Stopping AGENT-{idx:03d}; other agents continue"
        self._mark_detail_dirty(pane)
        self._sync()

    def _prepare_config_change(self, label: str) -> bool:
        if self.storage_preflight_inflight:
            self.status = f"Cannot change {label} while checking storage"
            self._sync()
            return False
        if self.running:
            self.status = f"Cannot change {label} while running"
            self._sync()
            return False
        return True

    def _refresh_follow_up_delay_row(self) -> None:
        value = str(self._follow_up_delay_seconds())
        if not self.run_info_rows:
            self.run_info_rows = self._base_info_rows()
            return
        updated = False
        rows: list[tuple[str, str]] = []
        for label, current in self.run_info_rows:
            if label == "FOLLOW_UP_DELAY":
                rows.append((label, value))
                updated = True
            else:
                rows.append((label, current))
        if not updated:
            insert_at = next(
                (
                    index
                    for index, (label, _current) in enumerate(rows)
                    if label == "SYNC_BACK"
                ),
                len(rows),
            )
            rows.insert(insert_at, ("FOLLOW_UP_DELAY", value))
        self.run_info_rows = rows

    def _handle_followupdelay(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"followupdelay={self._follow_up_delay_seconds()}s"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /followupdelay <non-negative seconds>")
            return
        try:
            value = int(args[0])
        except ValueError:
            value = -1
        if value < 0:
            self.status = "followupdelay must be >= 0"
            self._sync()
            return
        if self.storage_preflight_inflight:
            self.status = "Cannot change follow-up delay while checking storage"
            self._sync()
            return

        countdown_active = bool(
            self.follow_up_continue_at is not None
            or self.follow_up_ready
            or self.follow_up_source_finalized
        )
        old_delay = self._follow_up_delay_seconds()
        self.args.follow_up_delay = value
        self._refresh_follow_up_delay_row()
        message = f"followupdelay={value}s"
        if (
            countdown_active
            and self.follow_up_queue
            and not self.running
            and not self.follow_up_queue_editor_open
        ):
            # Adjust the countdown to the new delay, preserving elapsed time.
            if self.follow_up_continue_at is not None:
                remaining = max(0, self.follow_up_continue_at - time.monotonic())
                elapsed = old_delay - remaining
                new_remaining = max(0, value - elapsed)
                if new_remaining == 0:
                    self.follow_up_ready = True
                    self.follow_up_continue_at = None
                    self._follow_up_countdown_second = None
                else:
                    self.follow_up_continue_at = time.monotonic() + new_remaining
                    self._follow_up_countdown_second = int(new_remaining)
                    self._set_follow_up_countdown_status(int(new_remaining))
            else:
                # Not currently counting down; start a fresh countdown.
                self._start_follow_up_countdown()
            self._show_text(message)
        else:
            self._show_setting(message)

    def _synthesis_config_is_editable(self) -> bool:
        if not self.running:
            return True
        coordinator = self.live_synthesis_config_coordinator
        return bool(coordinator is not None and coordinator.active)

    def _prepare_synthesis_config_change(self, label: str) -> bool:
        if self.storage_preflight_inflight:
            self.status = f"Cannot change {label} while checking storage"
            self._sync()
            return False
        if self.running and not self._synthesis_config_is_editable():
            self.status = f"Cannot change {label} after synthesis has started"
            self._sync()
            return False
        return True

    def _synthesis_config_snapshot(
        self,
    ) -> tuple[int, str | None, str | None, bool | None]:
        return (
            self.synthesis_agents,
            self._synthesis_model_setting(),
            self._synthesis_effort_setting(),
            self._synthesis_fast_setting(),
        )

    def _restore_synthesis_config(
        self,
        snapshot: tuple[int, str | None, str | None, bool | None],
    ) -> None:
        agents, model, effort, fast = snapshot
        self.synthesis_agents = agents
        self.args.synthesis_agents = agents
        self.args.synthesis_model = model
        self.args.synthesis_effort = effort
        self.args.synthesis_fast = fast

    def _refresh_synthesis_config_rows(self) -> None:
        current = dict(self._base_info_rows())
        if not self.running or not self.run_info_rows:
            self.run_info_rows = self._base_info_rows()
            return
        self.run_info_rows = [
            (
                label,
                current.get(label, value)
                if label in SYNTHESIS_INFO_LABELS
                else value,
            )
            for label, value in self.run_info_rows
        ]

    def _publish_synthesis_config(
        self,
        previous: tuple[int, str | None, str | None, bool | None],
        label: str,
    ) -> bool:
        if not self.running:
            return True
        coordinator = self.live_synthesis_config_coordinator
        if coordinator is None or not coordinator.update(
            self.synthesis_agents,
            self._synthesis_model_setting(),
            self._synthesis_effort_setting(),
            self._synthesis_fast_setting(),
        ):
            self._restore_synthesis_config(previous)
            self._refresh_synthesis_config_rows()
            self.status = f"Cannot change {label} after synthesis has started"
            self._sync()
            return False
        if self.pending_execution_args is not None:
            self.pending_execution_args.synthesis_agents = self.synthesis_agents
            self.pending_execution_args.synthesis_model = (
                self._synthesis_model_setting()
            )
            self.pending_execution_args.synthesis_effort = (
                self._synthesis_effort_setting()
            )
            self.pending_execution_args.synthesis_fast = (
                self._synthesis_fast_setting()
            )
        return True

    def _prepare_context_change(self, label: str) -> bool:
        if self.storage_preflight_inflight:
            self.status = f"Cannot change {label} while checking storage"
            self._sync()
            return False
        if self.running:
            self.status = f"Cannot change {label} while running"
            self._sync()
            return False
        if self._finalize_displayed_pending(
            require_resume=False,
            archive_detail=True,
        ):
            self._clear_follow_up_queue()
            return True
        self._sync()
        return False

    def _follow_up_context(self) -> tuple[str, str]:
        return (
            str(self.workspace.expanduser().resolve()),
            self.resume_session_id.strip(),
        )

    def _clear_follow_up_queue(self) -> None:
        self._cancel_follow_up_queue_click()
        self.follow_up_queue.clear()
        self.follow_up_queue_editor_resume_after = False
        self.follow_up_continue_at = None
        self.follow_up_ready = False
        self.follow_up_source_finalized = False
        self._follow_up_countdown_second = None
        self._follow_up_queue_cache = ""
        self._follow_up_queue_items_cache = ()
        self._follow_up_queue_refresh_deferred = False
        self._refresh_follow_up_queue()

    def _rebind_follow_up_queue(self) -> None:
        context = self._follow_up_context()
        self.follow_up_queue = [
            QueuedFollowUp(
                item.prompt,
                record_history=item.record_history,
                context=context,
                recorded_history_context=item.recorded_history_context,
            )
            for item in self.follow_up_queue
        ]

    def _handle_queue(self, args: list[str]) -> None:
        self._cancel_follow_up_queue_click()
        if len(args) > 1:
            self._command_error("Usage: /queue [n]")
            return
        if self.storage_preflight_inflight:
            self.status = "Cannot edit the queue while checking storage"
            self._sync()
            return
        if not self.follow_up_queue:
            self.status = "No queued follow-up questions"
            self._sync()
            return
        selected_position = 1
        if args:
            try:
                selected_position = int(args[0])
            except ValueError:
                selected_position = 0
            if not 1 <= selected_position <= len(self.follow_up_queue):
                self.status = (
                    f"Queue item must be between 1 and "
                    f"{len(self.follow_up_queue)}"
                )
                self._sync()
                return
        if self.follow_up_queue_editor_open:
            self.status = "The follow-up queue editor is already open"
            self._sync()
            return

        snapshot = tuple(self.follow_up_queue)
        self.follow_up_queue_editor_open = True
        self.follow_up_queue_editor_resume_after = bool(
            not self.running
            and (
                self.follow_up_continue_at is not None
                or self.follow_up_ready
                or self.follow_up_source_finalized
                or self._successful_agent_result(self.selected_agent) is not None
            )
        )
        # Pause countdown while editing. Save remaining time so it can be
        # restored when the editor closes, rather than restarting from scratch.
        saved_remaining_seconds: float | None = None
        if self.follow_up_queue_editor_resume_after:
            if self.follow_up_ready:
                saved_remaining_seconds = 0.0
            elif self.follow_up_continue_at is not None:
                saved_remaining_seconds = max(0.0, self.follow_up_continue_at - time.monotonic())
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
        self.follow_up_queue_editor_saved_remaining = saved_remaining_seconds
        self.status = "Editing queued follow-ups"
        self._refresh_follow_up_queue()
        self.push_screen(
            QueueEditorScreen(
                [
                    QueueEditorRow(index, item.prompt)
                    for index, item in enumerate(snapshot, 1)
                ],
                selected_position,
            ),
            lambda result: self._finish_follow_up_queue_editor(
                snapshot,
                result,
            ),
        )

    def _finish_follow_up_queue_editor(
        self,
        snapshot: tuple[QueuedFollowUp, ...],
        result: QueueEditorResult | None,
    ) -> None:
        self.follow_up_queue_editor_open = False
        resume_after = self.follow_up_queue_editor_resume_after
        saved_remaining = getattr(self, "follow_up_queue_editor_saved_remaining", None)
        self.follow_up_queue_editor_resume_after = False
        self.follow_up_queue_editor_saved_remaining = None
        message = "Queue unchanged"
        if result is not None:
            if tuple(self.follow_up_queue) != snapshot:
                message = "Queue changed while editing; reopen /queue"
            else:
                rebuilt: list[QueuedFollowUp] = []
                used_positions: set[int] = set()
                invalid = False
                for row in result.rows:
                    position = row.source_position
                    prompt = row.prompt.strip()
                    if (
                        position in used_positions
                        or not 1 <= position <= len(snapshot)
                        or not prompt
                    ):
                        invalid = True
                        break
                    used_positions.add(position)
                    source = snapshot[position - 1]
                    recorded_history_context = source.recorded_history_context
                    if source.record_history and prompt != source.prompt:
                        recorded_history_context = self._record_prompt_history(
                            prompt
                        )
                    rebuilt.append(
                        QueuedFollowUp(
                            prompt,
                            record_history=source.record_history,
                            context=source.context,
                            recorded_history_context=recorded_history_context,
                        )
                    )
                if invalid:
                    message = "Queue edits were invalid; reopen /queue"
                else:
                    self.follow_up_queue = rebuilt
                    message = (
                        f"Queue updated: {len(rebuilt)} "
                        f"{'item' if len(rebuilt) == 1 else 'items'}"
                        if rebuilt
                        else "Queued follow-ups cancelled"
                    )

        if not self.follow_up_queue:
            self._clear_follow_up_queue()
            self.status = message
            self._sync()
            return

        if (
            resume_after
            and not self.running
            and not self.storage_preflight_inflight
        ):
            if (
                self.follow_up_source_finalized
                or self._successful_agent_result(self.selected_agent) is not None
            ):
                # Resume with the remaining time saved when the editor opened.
                # This preserves the countdown rather than restarting it from
                # the full follow_up_delay value on every editor close.
                if isinstance(saved_remaining, (int, float)) and saved_remaining >= 0:
                    if saved_remaining == 0:
                        self.follow_up_ready = True
                        self._follow_up_countdown_second = None
                    else:
                        remaining_int = int(saved_remaining)
                        self.follow_up_continue_at = time.monotonic() + saved_remaining
                        self._follow_up_countdown_second = remaining_int
                        self._set_follow_up_countdown_status(remaining_int)
                else:
                    self._start_follow_up_countdown()
            elif not self._schedule_follow_up_after_completion():
                self.status = message
        else:
            self.status = message
        self._refresh_follow_up_queue()
        self._sync()

    def _determine_exit_target(self) -> tuple[int | None, bool]:
        """Return (agent_idx_to_finalize, needs_confirmation).
        
        Returns:
            (None, False): no pending run or no success → discard
            (idx, False): clear target, no confirmation needed
            (idx, True): target exists but needs user confirmation
            (None, True): all success agents rejected, needs confirmation to discard
        """
        if not self._has_pending_run():
            return None, False
        if self._pending_sync_disabled():
            return None, False
        
        success_panes = [
            pane for pane in self.agents.values()
            if isinstance(pane.result, dict) and pane.result.get("status") == "success"
        ]
        if not success_panes:
            return None, False
        
        unrejected_success = [p for p in success_panes if not p.rejected]
        if not unrejected_success:
            return None, True
        
        displayed = self.agents.get(self.selected_agent)
        if displayed in unrejected_success:
            return self.selected_agent, False
        
        recommended = self.recommended_agent
        if recommended is not None:
            recommended_pane = self.agents.get(recommended)
            if recommended_pane in unrejected_success:
                return recommended, True
        
        return unrejected_success[0].idx, True

    def _finalize_displayed_pending(
        self,
        require_resume: bool = True,
        archive_detail: bool = False,
    ) -> bool:
        if not self._has_pending_run():
            return True
        if self._pending_sync_disabled():
            return self._discard_pending_run()
        if not any(
            isinstance(pane.result, dict) and pane.result.get("status") == "success"
            for pane in self.agents.values()
        ):
            return self._discard_pending_run()
        return self._finalize_agent(
            self.selected_agent,
            require_resume=require_resume,
            archive_detail=archive_detail,
        )

    def _show_setting(self, text: str) -> None:
        self.status = text
        self._show_text(text)

    def _parse_bool_arg(self, args: list[str], label: str) -> bool | None:
        if not args:
            return None
        value = args[0].lower()
        if value in {"1", "yes", "y", "true", "on", "enable", "enabled"}:
            return True
        if value in {"0", "no", "n", "false", "off", "disable", "disabled"}:
            return False
        self._command_error(f"Usage: {label} <on|off>")
        return None

    def _handle_numofagents(self, args: list[str]) -> None:
        if not args:
            self._show_setting(f"numofagents={self.num_agents}")
            return
        try:
            value = int(args[0])
        except ValueError:
            self._command_error("Usage: /numofagents <positive integer>")
            return
        if value <= 0:
            self.status = "numofagents must be > 0"
            self._sync()
            return
        if value > MAX_AGENT_COUNT:
            self.status = f"numofagents must be <= {MAX_AGENT_COUNT}"
            self._sync()
            return
        if not self._prepare_config_change("agent count"):
            return
        preserve_completed_run = self._has_pending_run()
        self.num_agents = value
        self.args.num_agents = value
        if not preserve_completed_run:
            self.selected_agent = min(self.selected_agent, value)
            self.agents = {idx: AgentPane(idx) for idx in range(1, value + 1)}
            self.recommended_agent = None
            self._mark_detail_dirty()
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"Next run will use {value} agents")

    def _handle_synthesis(self, args: list[str]) -> None:
        if self.running and getattr(self.pending_execution_args, "workspace_mode", "isolated") == "source":
            self._command_error("Configure synthesis before the next turn; this turn runs in the source folder.")
            return
        if not args:
            self._show_setting(
                f"synthesis={self.synthesis_agents}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /synthesis <non-negative integer|off>")
            return
        value_text = args[0].strip().lower()
        if value_text in {"off", "none", "disable", "disabled"}:
            value = 0
        else:
            try:
                value = int(value_text)
            except ValueError:
                value = -1
        if value < 0:
            self._command_error("Usage: /synthesis <non-negative integer|off>")
            return
        if value > MAX_AGENT_COUNT:
            self.status = f"synthesis must be <= {MAX_AGENT_COUNT}"
            self._sync()
            return
        if not self._prepare_synthesis_config_change("synthesis agent count"):
            return
        previous = self._synthesis_config_snapshot()
        self.synthesis_agents = value
        self.args.synthesis_agents = value
        if not self._publish_synthesis_config(
            previous,
            "synthesis agent count",
        ):
            return
        self._refresh_synthesis_config_rows()
        scope = "Current run synthesis" if self.running else "Next run"
        if value:
            self._show_setting(
                f"{scope} will use {value} synthesis agents"
            )
        else:
            self._show_setting(f"{scope} will skip synthesis")

    def _handle_maxparallel(self, args: list[str]) -> None:
        current = getattr(self.args, "max_parallel", None)
        if not args:
            self._show_setting(f"maxparallel={current if current is not None else 'auto'}")
            return
        value_text = args[0].lower()
        if value_text in {"auto", "default", "clear", "none"}:
            value = None
        else:
            try:
                value = int(value_text)
            except ValueError:
                self._command_error("Usage: /maxparallel <positive integer|auto>")
                return
            if value <= 0:
                self.status = "maxparallel must be > 0"
                self._sync()
                return
        if not self._prepare_config_change("max parallel"):
            return
        self.args.max_parallel = value
        if isinstance(value, int) and value > 1:
            self.args.serial = False
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"maxparallel={value if value is not None else 'auto'}")

    def _handle_subagents(self, args: list[str]) -> None:
        current = bool(getattr(self.args, "subagents", False))
        if not args:
            self._show_setting(
                "subagents="
                f"{'on' if current else 'off'} "
                f"(limit={getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)})"
            )
            return
        value = self._parse_bool_arg(args, "/subagents")
        if value is None:
            return
        if not self._prepare_config_change("nested agents"):
            return
        self.args.subagents = value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(
            "subagents="
            f"{'on' if value else 'off'} "
            f"(limit={getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)})"
        )

    def _handle_subagentslimit(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                "subagentslimit="
                f"{getattr(self.args, 'subagents_limit', DEFAULT_SUBAGENTS_LIMIT)}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /subagentslimit <positive integer>")
            return
        try:
            value = int(args[0])
        except ValueError:
            value = 0
        if value <= 0:
            self.status = "subagentslimit must be > 0"
            self._sync()
            return
        if not self._prepare_config_change("nested agent limit"):
            return
        self.args.subagents_limit = value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"subagentslimit={value}")

    def _handle_execution(self, serial: bool) -> None:
        if not self._prepare_config_change("execution mode"):
            return
        self.args.serial = serial
        if not serial and getattr(self.args, "max_parallel", None) == 1:
            self.args.max_parallel = None
        self.run_info_rows = self._base_info_rows()
        self._show_setting("execution=serial" if serial else "execution=parallel")

    def _handle_recommendby(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"recommendby={getattr(self.args, 'recommend_by', 'reasoning_tokens')}"
            )
            return
        try:
            value = normalize_recommend_by(args[0])
        except argparse.ArgumentTypeError as exc:
            self.status = str(exc)
            self._sync()
            return
        if not self._prepare_config_change("selection strategy"):
            return
        self.args.recommend_by = value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"recommendby={value}")

    def _resume_model_for_effort(self) -> str | None:
        if self.resume_session_id:
            for session in self.resume_entries:
                if str(getattr(session, "session_id", "")) == self.resume_session_id:
                    resumed_model = str(getattr(session, "model", "") or "").strip()
                    if resumed_model:
                        return resumed_model
        return None

    def _model_for_effort(self) -> str | None:
        configured = str(getattr(self.args, "model", None) or "").strip()
        return configured or self._resume_model_for_effort()

    def _effort_model_is_known(self) -> bool:
        return bool(
            getattr(self.args, "model", None)
            or not self.resume_session_id
            or self._resume_model_for_effort()
        )

    def _effort_options_for_context(self) -> list[tuple[str, str]]:
        model = (
            self._model_for_effort()
            if self._effort_model_is_known()
            else UNKNOWN_RESUME_MODEL
        )
        return self.model_registry.effort_options(
            model,
            getattr(self.args, "effort", None),
        )

    def _coerce_effort_for_model(self) -> bool:
        if not self._effort_model_is_known():
            return False
        current = getattr(self.args, "effort", None)
        if self.model_registry.effort_is_supported(
            self._model_for_effort(),
            current,
        ):
            return False
        self.args.effort = None
        return True

    def _refresh_effort_for_context(self) -> bool:
        return self._coerce_effort_for_model()

    def _synthesis_model_setting(self) -> str | None:
        return normalize_synthesis_model(
            getattr(self.args, "synthesis_model", None)
        )

    def _synthesis_effort_setting(self) -> str | None:
        return normalize_synthesis_effort(
            getattr(self.args, "synthesis_effort", None)
        )

    def _synthesis_fast_setting(self) -> bool | None:
        try:
            return normalize_synthesis_fast(
                getattr(self.args, "synthesis_fast", None)
            )
        except argparse.ArgumentTypeError:
            return None

    def _synthesis_model_control_value(self) -> str:
        return str(self._synthesis_model_setting() or "")

    def _synthesis_effort_control_value(self) -> str:
        return str(self._synthesis_effort_setting() or "")

    def _synthesis_fast_control_value(self) -> str:
        return fast_control_value(self._synthesis_fast_setting())

    def _synthesis_model_for_effort(self) -> str | None:
        configured = effective_synthesis_codex_settings(self.args).model
        return str(configured or "").strip() or self._resume_model_for_effort()

    def _synthesis_effort_model_is_known(self) -> bool:
        settings = effective_synthesis_codex_settings(self.args)
        return bool(
            settings.model
            or not self.resume_session_id
            or self._resume_model_for_effort()
        )

    def _synthesis_model_options(self) -> list[tuple[str, str]]:
        return self.model_registry.model_options(
            self._synthesis_model_setting()
        )

    def _synthesis_effort_options(self) -> list[tuple[str, str]]:
        model = (
            self._synthesis_model_for_effort()
            if self._synthesis_effort_model_is_known()
            else UNKNOWN_RESUME_MODEL
        )
        return self.model_registry.effort_options(
            model,
            self._synthesis_effort_setting(),
        )

    def _synthesis_fast_options(self) -> list[tuple[str, str]]:
        return [
            (
                format_fast_mode(
                    None,
                    self.model_registry.configured_service_tier,
                ),
                "auto",
            ),
            ("YES", "on"),
            ("NO", "off"),
        ]

    def _synthesis_model_display(self) -> str:
        settings = effective_synthesis_codex_settings(self.args)
        return self.model_registry.model_display(settings.model)

    def _synthesis_effort_display(self) -> str:
        settings = effective_synthesis_codex_settings(self.args)
        model = (
            self._synthesis_model_for_effort()
            if self._synthesis_effort_model_is_known()
            else UNKNOWN_RESUME_MODEL
        )
        try:
            display = self.model_registry.effort_display(
                model,
                settings.effort,
            )
        except ValueError:
            display = str(settings.effort or "auto")
        return display

    def _synthesis_fast_display(self) -> str:
        settings = effective_synthesis_codex_settings(self.args)
        return format_fast_mode(
            settings.fast,
            self.model_registry.configured_service_tier,
        )

    def _coerce_synthesis_effort_for_model(self) -> bool:
        if not self._synthesis_effort_model_is_known():
            return False
        settings = effective_synthesis_codex_settings(self.args)
        if self.model_registry.effort_is_supported(
            self._synthesis_model_for_effort(),
            settings.effort,
        ):
            return False
        self.args.synthesis_effort = None
        return True

    def _handle_model(self, args: list[str]) -> None:
        if not args:
            self._show_setting(f"model={getattr(self.args, 'model', None) or 'default'}")
            return
        value = args[0]
        if value.lower() in {"clear", "default", "none"}:
            value = None
        if not self._prepare_config_change("model"):
            return
        self.args.model = value
        effort_reset = self._refresh_effort_for_context()
        synthesis_effort_reset = self._coerce_synthesis_effort_for_model()
        self.run_info_rows = self._base_info_rows()
        setting = f"model={value or 'default'}"
        if effort_reset:
            setting += (
                f"; effort={self.model_registry.effort_display(self._model_for_effort(), None)}"
            )
        if synthesis_effort_reset:
            setting += (
                f"; synthesis_effort={self._synthesis_effort_display()}"
            )
        self._show_setting(setting)

    def _handle_effort(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"effort={self.model_registry.effort_display(self._model_for_effort(), getattr(self.args, 'effort', None))}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /effort <auto|level>")
            return
        if not self._prepare_config_change("effort"):
            return
        requested = args[0].strip().lower()
        value = None if requested in {"auto", "clear", "default", "none"} else requested
        if self._effort_model_is_known():
            try:
                self.model_registry.validate_effort(
                    self._model_for_effort(), value
                )
            except ValueError as exc:
                self.status = str(exc)
                self._sync()
                return
        self.args.effort = value
        synthesis_effort_reset = self._coerce_synthesis_effort_for_model()
        self.run_info_rows = self._base_info_rows()
        setting = (
            f"effort={self.model_registry.effort_display(self._model_for_effort(), value)}"
        )
        if synthesis_effort_reset:
            setting += (
                f"; synthesis_effort={self._synthesis_effort_display()}"
            )
        self._show_setting(setting)

    def _handle_fast(self, args: list[str]) -> None:
        current = getattr(self.args, "fast", None)
        if not args or (
            len(args) == 1 and args[0].strip().lower() == "status"
        ):
            self._show_setting(
                "fast="
                + fast_setting_value(
                    current,
                    self.model_registry.configured_service_tier,
                )
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /fast <on|off|auto>")
            return
        requested = args[0].strip().lower()
        values: dict[str, bool | None] = {
            "auto": None,
            "clear": None,
            "default": None,
            "inherit": None,
            "none": None,
            "on": True,
            "yes": True,
            "true": True,
            "off": False,
            "no": False,
            "false": False,
        }
        if requested not in values:
            self._command_error("Usage: /fast <on|off|auto>")
            return
        if not self._prepare_config_change("Fast mode"):
            return
        self.args.fast = values[requested]
        self.run_info_rows = self._base_info_rows()
        self._show_setting(
            "fast="
            + fast_setting_value(
                self.args.fast,
                self.model_registry.configured_service_tier,
            )
        )

    def _handle_synthesis_model(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"synthesismodel={self._synthesis_model_display()}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /synthesismodel <name|default>")
            return
        if not self._prepare_synthesis_config_change("synthesis model"):
            return
        previous = self._synthesis_config_snapshot()
        self.args.synthesis_model = normalize_synthesis_model(args[0])
        effort_reset = self._coerce_synthesis_effort_for_model()
        if not self._publish_synthesis_config(previous, "synthesis model"):
            return
        self._refresh_synthesis_config_rows()
        setting = f"synthesismodel={self._synthesis_model_display()}"
        if effort_reset:
            setting += (
                f"; synthesis_effort={self._synthesis_effort_display()}"
            )
        self._show_setting(setting)

    def _handle_synthesis_effort(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"synthesiseffort={self._synthesis_effort_display()}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /synthesiseffort <auto|level>")
            return
        if not self._prepare_synthesis_config_change("synthesis effort"):
            return
        previous_config = self._synthesis_config_snapshot()
        value = normalize_synthesis_effort(args[0])
        previous = getattr(
            self.args,
            "synthesis_effort",
            None,
        )
        self.args.synthesis_effort = value
        if self._synthesis_effort_model_is_known():
            settings = effective_synthesis_codex_settings(self.args)
            try:
                self.model_registry.validate_effort(
                    self._synthesis_model_for_effort(),
                    settings.effort,
                )
            except ValueError as exc:
                self.args.synthesis_effort = previous
                self.status = str(exc)
                self._sync()
                return
        if not self._publish_synthesis_config(
            previous_config,
            "synthesis effort",
        ):
            return
        self._refresh_synthesis_config_rows()
        self._show_setting(
            f"synthesiseffort={self._synthesis_effort_display()}"
        )

    def _handle_synthesis_fast(self, args: list[str]) -> None:
        if not args:
            self._show_setting(
                f"synthesisfast={self._synthesis_fast_display()}"
            )
            return
        if len(args) != 1:
            self._command_error("Usage: /synthesisfast <auto|on|off>")
            return
        try:
            value = normalize_synthesis_fast(args[0])
        except argparse.ArgumentTypeError:
            self._command_error("Usage: /synthesisfast <auto|on|off>")
            return
        if not self._prepare_synthesis_config_change("Synthesis Fast mode"):
            return
        previous = self._synthesis_config_snapshot()
        self.args.synthesis_fast = value
        if not self._publish_synthesis_config(
            previous,
            "Synthesis Fast mode",
        ):
            return
        self._refresh_synthesis_config_rows()
        self._show_setting(
            f"synthesisfast={self._synthesis_fast_display()}"
        )

    def _handle_workspace(self, args: list[str]) -> None:
        if not args:
            self._show_setting(f"workspace={absolute_path_for_display(self.workspace)}")
            return
        workspace = Path(args[0]).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            self.status = f"Workspace not found: {workspace}"
            self._sync()
            return
        if not self._prepare_context_change("workspace"):
            return
        self._persist_workspace_settings()
        self.workspace = workspace
        self.args.workspace = str(workspace)
        self.resume_session_id = ""
        self.args.resume_session_id = None
        self._workspace_config_explicit = frozenset()
        self._apply_saved_workspace_settings()
        self.num_agents = self.args.num_agents
        self.synthesis_agents = max(
            0,
            int(getattr(self.args, "synthesis_agents", 0) or 0),
        )
        self.args.synthesis_agents = self.synthesis_agents
        self.resume_session_id = (self.args.resume_session_id or "").strip()
        self._committed_fast_value = fast_control_value(
            getattr(self.args, "fast", None)
        )
        self._committed_synthesis_fast_value = (
            self._synthesis_fast_control_value()
        )
        self._committed_model_effort_values.update(
            {
                "config-model": str(
                    getattr(self.args, "model", None) or ""
                ),
                "config-effort": str(
                    getattr(self.args, "effort", None) or ""
                ),
                "config-synthesis-model": (
                    self._synthesis_model_control_value()
                ),
                "config-synthesis-effort": (
                    self._synthesis_effort_control_value()
                ),
            }
        )
        try:
            fast_control = self.query_one("#config-fast", Select)
        except Exception:
            pass
        else:
            self._set_select_control(
                fast_control,
                self._committed_fast_value,
            )
        self.model_choices = self.model_registry.model_options(
            getattr(self.args, "model", None)
        )
        self.effort_choices = self._effort_options_for_context()
        self._coerce_synthesis_effort_for_model()
        self.synthesis_model_choices = self._synthesis_model_options()
        self.synthesis_effort_choices = self._synthesis_effort_options()
        self.synthesis_fast_choices = self._synthesis_fast_options()
        self._load_prompt_history_context()
        self.resume_history_request += 1
        self._reset_conversation_detail()
        self.run_info_rows = self._base_info_rows()
        self._refresh_resume_control()
        self._show_setting(f"workspace={absolute_path_for_display(workspace)}")

    def _handle_runsdir(self, args: list[str]) -> None:
        if not args:
            self._show_setting(f"runsdir={getattr(self.args, 'runs_dir', None) or 'auto'}")
            return
        value = None if args[0].lower() in {"clear", "default", "auto", "none"} else str(Path(args[0]).expanduser())
        try:
            choose_run_base(self.workspace, value)
        except SystemExit as exc:
            self.status = str(exc)
            self._sync()
            return
        if not self._prepare_config_change("runs dir"):
            return
        self.args.runs_dir = value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"runsdir={value or 'auto'}")

    def _handle_codexbin(self, args: list[str]) -> None:
        if not args:
            self._show_setting(f"codexbin={getattr(self.args, 'codex_bin', 'codex')}")
            return
        if not self._prepare_config_change("codex binary"):
            return
        self.args.codex_bin = args[0]
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"codexbin={args[0]}")

    def _handle_syncback(self, args: list[str]) -> None:
        current = not bool(getattr(self.args, "no_sync_back", False))
        if not args:
            self._show_setting(f"syncback={'on' if current else 'off'}")
            return
        value = self._parse_bool_arg(args, "/syncback")
        if value is None:
            return
        if not self._prepare_config_change("sync back"):
            return
        self.args.no_sync_back = not value
        if self._has_pending_run() and not self.running:
            self.pending_no_sync_back = not value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"syncback={'on' if value else 'off'}")

    def _handle_keepworkspaces(self, args: list[str]) -> None:
        current = bool(getattr(self.args, "keep_workspaces", False))
        if not args:
            self._show_setting(f"keepworkspaces={'on' if current else 'off'}")
            return
        value = self._parse_bool_arg(args, "/keepworkspaces")
        if value is None:
            return
        if not self._prepare_config_change("keep workspaces"):
            return
        self.args.keep_workspaces = value
        if self._has_pending_run() and not self.running:
            self.pending_keep_workspaces = value
        self.run_info_rows = self._base_info_rows()
        self._show_setting(f"keepworkspaces={'on' if value else 'off'}")

    def _handle_promptfile(self, args: list[str]) -> None:
        if not args:
            self._command_error("Usage: /promptfile <path>")
            return
        if self.running:
            self.status = "Cannot run a prompt file while running"
            self._sync()
            return
        path = Path(args[0]).expanduser()
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            self.status = f"Cannot read prompt file: {exc}"
            self._sync()
            return
        if not prompt:
            self.status = "Prompt file is empty"
            self._sync()
            return
        self._submit_task_prompt(prompt)

    def _handle_resumeinclude(self, args: list[str]) -> None:
        current = bool(getattr(self.args, "resume_include_non_interactive", True))
        if not args:
            self._show_setting(f"resumeinclude={'on' if current else 'off'}")
            return
        value = self._parse_bool_arg(args, "/resumeinclude")
        if value is None:
            return
        if self.running:
            self.status = "Cannot change resume include mode while running"
            self._sync()
            return
        self.args.resume_include_non_interactive = value
        self._show_setting(f"resumeinclude={'on' if value else 'off'}")

    def _reset_conversation_detail(self) -> None:
        self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
        self.selected_agent = min(self.selected_agent, self.num_agents)
        self.recommended_agent = None
        self.detail_history.clear()
        self.command_history.clear()
        self._mark_detail_dirty()

    def _history_detail_block(self, entry: CodexHistoryEntry) -> tuple[str, str, str]:
        if entry.category == "user":
            return DETAIL_INPUT_MARKER, entry.text, "detail-input"
        if entry.category == "thought":
            return DETAIL_AGENT_MARKER, entry.text, "detail-thought"
        return DETAIL_AGENT_MARKER, entry.text, "detail-output"

    def _select_resume_session(self, session_id: str, rollout_path: str = "") -> None:
        session_id = session_id.strip()
        self.resume_history_request += 1
        request_id = self.resume_history_request
        self.status = f"Loading resume session: {session_id}"
        self._sync()

        def target() -> None:
            entries: list[CodexHistoryEntry] = []
            load_error = ""
            rejected = False
            try:
                with self._resume_io_lock:
                    load_error = self._subagent_resume_error(
                        self._get_codex_home(),
                        session_id,
                    ) or ""
                    rejected = bool(load_error)
                    if not rejected:
                        entries = self._load_codex_session_history(
                            self._get_codex_home(),
                            session_id,
                            rollout_path,
                        )
            except Exception as exc:  # noqa: BLE001
                load_error = str(exc)
            try:
                self.call_from_thread(
                    self.post_message,
                    ResumeHistoryLoaded(
                        request_id,
                        session_id,
                        entries,
                        load_error,
                        rejected,
                    ),
                )
            except RuntimeError:
                pass

        threading.Thread(target=target, name="pcr-resume-history", daemon=True).start()

    def _handle_resume(self, args: list[str]) -> None:
        if self.running:
            self.status = "Cannot change resume session while running"
            self._sync()
            return
        if args and args[0].lower() in {"clear", "new"}:
            if not self._prepare_context_change("resume session"):
                return
            self.resume_session_id = ""
            self.args.resume_session_id = None
            self._load_prompt_history_context()
            self.resume_history_request += 1
            self.pending_resume_selector = None
            self._reset_conversation_detail()
            self._refresh_effort_for_context()
            self._coerce_synthesis_effort_for_model()
            self.run_info_rows = self._base_info_rows()
            self._refresh_resume_control()
            self.status = "Resume cleared"
            self._persist_workspace_settings()
            self._sync()
            return

        selector = args[0] if args else "list"
        needs_choices = selector.lower() in {"list", "latest"} or selector.isdigit()
        if needs_choices and not self.resume_choices_loaded:
            self.pending_resume_selector = selector
            self.status = "Loading resume sessions"
            self._refresh_resume_control()
            self._sync()
            return
        self._handle_loaded_resume_selector(selector)

    def _handle_loaded_resume_selector(self, selector: str) -> None:
        if selector.lower() == "list":
            self._show_resume_list()
            return
        selector = "1" if selector.lower() == "latest" else selector
        chosen = None
        if selector.isdigit():
            idx = int(selector)
            if 1 <= idx <= len(self.resume_entries):
                chosen = self.resume_entries[idx - 1]
        else:
            for session in self.resume_entries:
                if session.session_id == selector:
                    chosen = session
                    break
            if chosen is None:
                if not self._prepare_context_change("resume session"):
                    return
                self._select_resume_session(selector)
                return
        if chosen is None:
            self.status = "No resumable session found"
            self._sync()
            return
        if not self._prepare_context_change("resume session"):
            return
        self._select_resume_session(chosen.session_id, chosen.rollout_path)

    def _show_resume_list(self) -> None:
        lines = ["Recent sessions:", "Use /resume <number> or /resume <session_id> to load one.", ""]
        for index, session in enumerate(self.resume_entries[:8], 1):
            lines.append(f"{index}. {session.session_id}  {session.title[:80]}")
        self.status = "Choose a resume session" if len(lines) > 3 else "No resumable sessions found"
        self._show_text("\n".join(lines) if len(lines) > 3 else "No resumable sessions found")

    def _show_status(self) -> None:
        self._show_text(self._tree_text())
