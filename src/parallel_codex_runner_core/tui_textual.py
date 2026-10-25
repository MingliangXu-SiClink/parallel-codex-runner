from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._vendor import activate_textual
from .codex_cli import format_fast_mode
from .codex_models import CodexModelRegistry
from .prompt_history import PromptHistoryNavigator, PromptHistoryStore
from .visible_paths import WorkspacePathMapping, rewrite_workspace_references
from .workspace_config import WorkspaceConfigStore, WorkspaceSettings

from .tui_common import (
    COMMAND_SPINNER_FRAMES,
    DETAIL_ACTIVITY_MARKER,
    DETAIL_AGENT_MARKER,
    DETAIL_BULLET_STYLE,
    DETAIL_CODE_FENCE_STYLE,
    DETAIL_CODE_STYLE,
    DETAIL_DIVIDER_STYLE,
    DETAIL_HEADING_STYLE,
    DETAIL_INPUT_MARKER,
    DETAIL_STYLE_PREFIX,
    DETAIL_TEXT_STYLES,
    FOLLOW_UP_CONFIG_CONTROL_ID,
    HELP_TEXT,
    MAX_SUGGESTIONS,
    RECOMMEND_BORDER_COLORS,
    RECOMMEND_BORDER_REFRESH_SECONDS,
    RECOMMEND_TITLE_BACKGROUND,
    RUNNER_THREE_PAIR_MIN_WIDTH,
    RUNNER_TWO_PAIR_MIN_WIDTH,
    SYNTHESIS_CONFIG_CONTROL_IDS,
    SYNTHESIS_INFO_LABELS,
    TEXTUAL_COMMANDS,
    TIP_ICON,
    TIP_ICON_COLORS,
    TIP_ICON_REFRESH_SECONDS,
    TIP_ROTATION_SECONDS,
    TUI_TIPS,
    UNKNOWN_RESUME_MODEL,
    _column_major_indices,
    _recorded_history_context_kwargs,
    build_help_text,
    command_detail_display,
    command_suggestions,
    compact_block,
    display_line_from_output,
    display_line_parts_from_output,
    fast_control_value,
    fast_setting_value,
    fast_value_from_control,
    fold_text_by_cells,
    format_reasoning_tokens_title,
    format_seconds,
    normalize_prompt_text,
    normalize_reasoning_token_counts,
    resume_select_options,
    same_display_message,
    split_command_detail,
)


try:
    activate_textual()
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.color import Color
    from textual.content import Content
    from textual.containers import Grid, Vertical, VerticalScroll
    from textual.geometry import Region
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.widget import Widget
    from textual.widgets import Button, Input, Select, Static, TextArea
except (ModuleNotFoundError, RuntimeError) as exc:
    _TEXTUAL_IMPORT_ERROR = exc

    def run_textual_tui(
        _args: argparse.Namespace, _exc: Exception = _TEXTUAL_IMPORT_ERROR
    ) -> int:
        raise SystemExit(
            "交互式 TUI 无法加载项目内置 Textual：请运行 python3 -m pip install -e . 重新安装。"
        ) from _exc

else:
    from .app import (
        LARGE_RUN_STORAGE_WARNING_BYTES,
        LiveCandidateCoordinator,
        LiveSynthesisConfigCoordinator,
        RunStorageEstimate,
        available_storage_bytes,
        cancel_agent_including_queued,
        estimate_staged_run_storage,
        format_storage_bytes,
        get_codex_home,
        infer_codex_thread_id_for_result,
        list_resume_sessions,
        load_codex_session_history,
        normalize_recommend_by,
        promote_best_codex_session_to_workspace,
        refresh_run_result_files,
        remove_agent_codex_homes,
        run_additional_agents,
        run_once,
        select_best_result,
        subagent_resume_error,
    )
    from .diffing import build_workspace_diff_text
    from .finalization import FinalizationStageError, finalize_candidate_workspace
    from .models import (
        AGENT_ROLE_CANDIDATE,
        AGENT_ROLE_SYNTHESIS,
        DEFAULT_FOLLOW_UP_DELAY_SECONDS,
        DEFAULT_SUBAGENTS_LIMIT,
        AgentResult,
        CodexHistoryEntry,
        normalize_agent_role,
    )
    from .paths import absolute_path_for_display, choose_run_base, is_relative_to
    from .runtime_pinning import preload_tui_runtime
    from .synthesis import (
        create_synthesis_context,
        effective_synthesis_codex_settings,
        normalize_synthesis_effort,
        normalize_synthesis_fast,
        normalize_synthesis_model,
    )
    from .workspace import (
        cleanup_workspace_copies,
        estimate_path_storage_bytes,
        execution_workspace_mode,
        sync_best_workspace_back,
    )
    from rich.cells import cell_len
    from rich.panel import Panel
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text

    preload_tui_runtime()

    def codex_model_options(current_model: str | None) -> list[tuple[str, str]]:
        return CodexModelRegistry.load(get_codex_home()).model_options(current_model)


    def codex_effort_options(
        model: str | None,
        current_effort: str | None,
    ) -> list[tuple[str, str]]:
        return CodexModelRegistry.load(get_codex_home()).effort_options(
            model,
            current_effort,
        )


    from .tui_components import (
        AgentDiffLoaded,
        AgentPane,
        AgentSwitchRequested,
        CandidateBatch,
        DetailScroll,
        DetailView,
        FollowUpQueueView,
        PromptEditor,
        PromptHistoryRequested,
        PromptSubmitted,
        QueueEditorResult,
        QueueEditorRow,
        QueueEditorScreen,
        QueuedFollowUp,
        RainbowDetailFrame,
        ResumeChoicesLoaded,
        ResumeHistoryLoaded,
        RunnerEvent,
        StoragePreflightFinished,
        StorageWarningScreen,
        ExitConfirmationScreen,
    )
    from .tui_commands import PcrTextualCommandMixin
    from .tui_rendering import PcrTextualRenderingMixin

    class PcrTextualApp(
        PcrTextualCommandMixin,
        PcrTextualRenderingMixin,
        App[None],
    ):
        CSS = """
        Screen {
            background: #101216;
            color: #e7ebf2;
        }
        #root {
            height: 100%;
        }
        .caption {
            height: 1;
            margin: 0 1;
            padding: 0 1;
            color: #8fb3ff;
            text-style: bold;
        }
        #runner-frame {
            height: auto;
            max-height: 50%;
            margin: 0 1;
            padding: 0 1;
            border: round cyan;
            border-title-style: bold;
            overflow-x: hidden;
            overflow-y: auto;
            scrollbar-size: 1 1;
        }
        #runner-grid {
            height: auto;
            grid-size: 2;
            grid-columns: 18 1fr;
            grid-rows: auto;
            grid-gutter: 0 1;
        }
        .runner-key {
            height: auto;
            min-height: 1;
            color: #e7ebf2;
            text-style: bold;
        }
        .runner-value {
            height: 1;
            min-height: 1;
            max-height: 1;
            text-wrap: nowrap;
            text-overflow: clip;
        }
        .runner-control {
            height: 1;
            min-height: 1;
            max-height: 1;
            border: none;
            padding: 0;
            background: #171d25;
        }
        .runner-control > SelectCurrent,
        .runner-control > SelectCurrent > #label {
            height: 1;
            min-height: 1;
            max-height: 1;
            text-wrap: nowrap;
            text-overflow: ellipsis;
        }
        .runner-control > SelectOverlay {
            width: auto;
            min-width: 100%;
            max-width: 90vw;
        }
        .runner-control > SelectOverlay > .option-list--option {
            text-wrap: nowrap;
            text-overflow: ellipsis;
        }
        .runner-control:focus {
            background: #243448;
            color: #ffffff;
        }
        #config-agents, #config-synthesis-agents, #config-max-parallel,
        #config-subagents-limit, #config-follow-up-delay {
            width: 12;
        }
        #config-execution, #config-recommend-by, #config-effort,
        #config-synthesis-effort {
            width: 24;
        }
        #config-model, #config-synthesis-model {
            width: 36;
        }
        #config-subagents, #config-sync-back, #config-keep-workspaces {
            width: 10;
        }
        #config-fast, #config-synthesis-fast {
            width: 20;
        }
        #detail-frame {
            height: 1fr;
            margin: 0 1;
            border: round cyan;
            border-title-align: left;
            border-title-color: #c8edf5;
            border-title-background: #101216;
            border-title-style: bold;
        }
        #detail-scroll {
            height: 1fr;
        }
        #detail {
            width: 100%;
            padding: 0 1;
        }
        #suggestions {
            height: 0;
            min-height: 0;
            max-height: 24;
            margin: 0 1;
            padding: 0 1;
            color: #b7c8e6;
            background: #101216;
        }
        #tips {
            height: 1;
            min-height: 1;
            max-height: 1;
            margin: 0 1;
            padding: 0 1;
            color: #aebed3;
            background: #171d25;
            content-align: left middle;
            text-wrap: nowrap;
            text-overflow: clip;
        }
        #follow-up-queue-frame {
            height: 2;
            min-height: 2;
            max-height: 4;
            margin: 0 1;
            background: #141b23;
            color: #d6e4f0;
            scrollbar-size: 1 1;
        }
        #follow-up-queue {
            width: 100%;
            height: auto;
            padding: 0 1;
            pointer: pointer;
            text-wrap: wrap;
            text-overflow: clip;
        }
        #prompt {
            height: 3;
            min-height: 3;
            max-height: 20;
            margin: 0 1;
            padding: 0 1;
            border: round #4c5f74;
            background: #0d1117;
        }
        #state {
            height: 1;
            margin: 0 1;
            padding: 0 1;
            content-align: left middle;
            background: #171d25;
            color: #ffffff;
            text-style: bold;
        }
        """

        @staticmethod
        def _list_resume_sessions(*args: Any, **kwargs: Any) -> Any:
            return list_resume_sessions(*args, **kwargs)

        @staticmethod
        def _load_codex_session_history(*args: Any, **kwargs: Any) -> Any:
            return load_codex_session_history(*args, **kwargs)

        @staticmethod
        def _subagent_resume_error(*args: Any, **kwargs: Any) -> Any:
            return subagent_resume_error(*args, **kwargs)

        @staticmethod
        def _get_codex_home() -> Path:
            return get_codex_home()

        BINDINGS = [
            ("ctrl+c", "interrupt_or_exit", "Copy/Clear/Exit"),
            ("super+c", "copy_selection", "Copy"),
            ("ctrl+l", "clear_view", "Clear"),
        ]

        def __init__(self, args: argparse.Namespace) -> None:
            super().__init__()
            self.args = args
            self.args.resume_include_non_interactive = True
            self.workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
            self.workspace_config_store = WorkspaceConfigStore()
            self._workspace_config_explicit = frozenset(
                getattr(args, "_pcr_explicit_tui_settings", set())
            )
            self._apply_saved_workspace_settings()
            self.args.synthesis_model = normalize_synthesis_model(
                getattr(self.args, "synthesis_model", None)
            )
            self.args.synthesis_effort = normalize_synthesis_effort(
                getattr(self.args, "synthesis_effort", None)
            )
            try:
                self.args.synthesis_fast = normalize_synthesis_fast(
                    getattr(self.args, "synthesis_fast", None)
                )
            except argparse.ArgumentTypeError:
                self.args.synthesis_fast = None
            self.num_agents = args.num_agents
            self.synthesis_agents = max(
                0,
                int(getattr(args, "synthesis_agents", 0) or 0),
            )
            self.args.synthesis_agents = self.synthesis_agents
            self.resume_session_id = (args.resume_session_id or "").strip()
            self.resume_entries = []
            self.model_registry = CodexModelRegistry.load(get_codex_home())
            resume_model_pending = bool(
                (getattr(args, "resume", False) or self.resume_session_id)
                and not getattr(args, "model", None)
            )
            if not resume_model_pending and not self.model_registry.effort_is_supported(
                getattr(args, "model", None), getattr(args, "effort", None)
            ):
                self.args.effort = None
            if not resume_model_pending:
                self._coerce_synthesis_effort_for_model()
            self.model_choices = self.model_registry.model_options(
                getattr(args, "model", None)
            )
            self.effort_choices = self.model_registry.effort_options(
                UNKNOWN_RESUME_MODEL
                if resume_model_pending
                else getattr(args, "model", None),
                getattr(args, "effort", None),
            )
            self.synthesis_model_choices = self._synthesis_model_options()
            self.synthesis_effort_choices = self._synthesis_effort_options()
            self.synthesis_fast_choices = self._synthesis_fast_options()
            self.resume_choices = resume_select_options([], self.resume_session_id)
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.selected_agent = 1
            self.running = False
            self.status = "Ready"
            self.recommended_agent: int | None = None
            self.started_at: float | None = None
            self.prompt_height = 3
            self.suggestion_line_count = 0
            self.tip_index = 0
            self.tip_icon_index = 0
            self.recommend_border_frame = 0
            self.recommend_border_edge_index = 0
            self.work_frame = 0
            self.exit_after_run = False
            self.cancel_event: threading.Event | None = None
            self.agent_cancel_events: dict[int, threading.Event] = {}
            self.runner_thread: threading.Thread | None = None
            self._shutdown_cleanup_started = False
            self.run_info_rows = self._base_info_rows()
            self.pending_run_root: Path | None = None
            self.pending_workspaces_root: Path | None = None
            self.pending_workspace: Path | None = None
            self.pending_no_sync_back: bool | None = None
            self.pending_keep_workspaces: bool | None = None
            self.pending_prompt = ""
            self.pending_prompt_records_history = False
            self.pending_execution_args: argparse.Namespace | None = None
            self.candidate_batches: list[CandidateBatch] = []
            self.active_batch_indices: set[int] = set()
            self.live_candidate_indices: set[int] = set()
            self.live_candidate_coordinator: LiveCandidateCoordinator | None = None
            self.live_synthesis_config_coordinator: (
                LiveSynthesisConfigCoordinator | None
            ) = None
            self.candidate_revision = 0
            self.synthesis_refresh_needed = False
            self.core_synthesis_revision: int | None = None
            self.pending_accept_agent: int | None = None
            self.detail_history: list[tuple[str, str, str]] = []
            self.command_history: list[tuple[str, str, str]] = []
            self.prompt_history_store = PromptHistoryStore()
            self.prompt_history_context = self.prompt_history_store.context_key(
                self.workspace,
                self.resume_session_id,
            )
            self.prompt_history_drafts: dict[tuple[str, str], str] = {
                self.prompt_history_context: ""
            }
            self.prompt_history_navigator = PromptHistoryNavigator(
                self._prompt_history_entries(self.prompt_history_context)
            )
            self._prompt_history_programmatic_values: list[str] = []
            self.detail_revision = 0
            self.resume_history_request = 0
            self.resume_choices_request = 0
            self.resume_choices_loaded = False
            self.resume_choices_inflight: tuple[int, Path] | None = None
            self.pending_resume_selector: str | None = None
            self._resume_io_lock = threading.Lock()
            self.storage_preflight_request = 0
            self.storage_preflight_inflight = False
            self._approved_large_storage_workspaces: set[str] = set()
            self.queued_prompt = ""
            self.queued_agent: int | None = None
            self.queued_prompt_records_history = False
            self.queued_prompt_recorded_history_context: tuple[str, str] | None = None
            # Follow-ups submitted from unfinished panes wait without stopping
            # the current run; queued_prompt above remains the immediate path.
            self.follow_up_queue: list[QueuedFollowUp] = []
            self.follow_up_continue_at: float | None = None
            self.follow_up_ready = False
            self.follow_up_source_finalized = False
            self._follow_up_countdown_second: int | None = None
            self._follow_up_queue_cache = ""
            self._follow_up_queue_items_cache: tuple[str, ...] = ()
            self._follow_up_queue_refresh_deferred = False
            self._follow_up_queue_click_timer: Any | None = None
            self._follow_up_queue_click_item: QueuedFollowUp | None = None
            self.follow_up_queue_editor_open = False
            self.follow_up_queue_editor_resume_after = False
            self._updating_controls = False
            self._latest_select_event_time: dict[str, float] = {}
            self._committed_model_effort_values: dict[str, str] = {
                "config-model": str(getattr(self.args, "model", None) or ""),
                "config-effort": str(getattr(self.args, "effort", None) or ""),
                "config-synthesis-model": self._synthesis_model_control_value(),
                "config-synthesis-effort": self._synthesis_effort_control_value(),
            }
            self._committed_fast_value = fast_control_value(
                getattr(self.args, "fast", None)
            )
            self._committed_synthesis_fast_value = (
                self._synthesis_fast_control_value()
            )
            self._committed_input_values = {
                "config-agents": str(self.num_agents),
                "config-synthesis-agents": str(self.synthesis_agents),
                "config-max-parallel": dict(self.run_info_rows).get("MAX_PARALLEL", ""),
                "config-subagents-limit": str(
                    getattr(
                        self.args,
                        "subagents_limit",
                        DEFAULT_SUBAGENTS_LIMIT,
                    )
                ),
                "config-follow-up-delay": str(
                    self._follow_up_delay_seconds()
                ),
            }
            self._mouse_down_in_runner_control = False
            self._last_screen_selection = ""
            self._sync_deferred_for_selection = False
            self._tip_refresh_deferred_for_selection = False
            self._recommend_border_deferred_for_selection = False
            self._runner_grid_columns = 0
            self._runner_config_pairs: tuple[tuple[Widget, Widget], ...] = ()
            self._detail_cache_key: tuple[Any, ...] | None = None
            self._detail_cache_renderable: object = ""
            self.app_in_foreground = True
            self.completion_notification_sent = False

        def _apply_saved_workspace_settings(self) -> bool:
            settings = self.workspace_config_store.load(self.workspace)
            if settings is None:
                return False
            settings.apply_to_args(self.args, self._workspace_config_explicit)
            return True

        def _persist_workspace_settings(self) -> bool:
            settings = WorkspaceSettings.from_runtime(
                self.num_agents,
                self.synthesis_agents,
                self.args,
                self.resume_session_id,
            )
            return self.workspace_config_store.save(self.workspace, settings)

        def _is_runner_control(self, node: Any) -> bool:
            while node is not None:
                if isinstance(node, (Input, Select)) and node.has_class("runner-control"):
                    return True
                node = node.parent
            return False

        def _is_detail_widget(self, node: Any) -> bool:
            while node is not None:
                if getattr(node, "id", None) in {
                    "detail",
                    "detail-scroll",
                    "detail-frame",
                }:
                    return True
                node = node.parent
            return False

        def _pointer_requests_agent_switch(self) -> bool:
            try:
                node, _offset = self.screen.get_widget_and_offset_at(
                    *self.mouse_position
                )
            except Exception:
                return False
            return self._is_detail_widget(node)

        @staticmethod
        def _is_node_within(node: Any, ancestor: Any) -> bool:
            while node is not None:
                if node is ancestor:
                    return True
                node = node.parent
            return False

        def _release_stale_mouse_capture(self, clicked_widget: Any) -> None:
            captured = self.mouse_captured
            if captured is not None and not self._is_node_within(clicked_widget, captured):
                # A fresh press outside the captured widget means its previous
                # mouse-up was lost (for example, outside the terminal window).
                # Leaving capture active prevents Screen from starting any
                # subsequent text selection.
                self.capture_mouse(None)

        def _selection_drag_active(self) -> bool:
            try:
                return bool(self.screen._selecting)
            except Exception:
                return False

        def _screen_selection_active(self) -> bool:
            try:
                if not self.screen.selections:
                    return False
                return bool(self.screen.get_selected_text())
            except Exception:
                return False

        def _clear_screen_selection_for_interaction(self) -> None:
            try:
                if self.screen.selections:
                    self.screen.clear_selection()
            except Exception:
                return
            self._last_screen_selection = ""
            self._flush_selection_deferred_updates()

        def _flush_selection_deferred_updates(self) -> None:
            if self._selection_drag_active():
                return
            refresh_tip = self._tip_refresh_deferred_for_selection
            refresh_follow_up_queue = self._follow_up_queue_refresh_deferred
            sync = self._sync_deferred_for_selection
            refresh_recommend_border = (
                self._recommend_border_deferred_for_selection
                and not self._screen_selection_active()
            )
            self._tip_refresh_deferred_for_selection = False
            self._follow_up_queue_refresh_deferred = False
            self._sync_deferred_for_selection = False
            if refresh_recommend_border:
                self._recommend_border_deferred_for_selection = False
            if refresh_tip:
                self._refresh_tip()
            if refresh_follow_up_queue:
                self._refresh_follow_up_queue()
            if sync:
                self._sync()

        async def on_event(self, event: events.Event) -> None:
            if isinstance(event, events.AppBlur):
                self.app_in_foreground = False
                if self.mouse_captured is not None:
                    self.capture_mouse(None)
            elif isinstance(event, events.AppFocus):
                self.app_in_foreground = True

            if isinstance(event, events.MouseDown) and not event.is_forwarded:
                try:
                    clicked_widget, _offset = self.screen.get_widget_and_offset_at(
                        event.x,
                        event.y,
                    )
                except Exception:
                    self._mouse_down_in_runner_control = False
                else:
                    self._release_stale_mouse_capture(clicked_widget)
                    self._mouse_down_in_runner_control = self._is_runner_control(clicked_widget)
                    if not self._is_detail_widget(clicked_widget):
                        self._last_screen_selection = ""

            if (
                isinstance(event, events.Key)
                and not event.is_forwarded
                and event.key not in {"ctrl+c", "super+c"}
            ):
                self._clear_screen_selection_for_interaction()

            is_text_input = (
                isinstance(event, events.Key) and event.is_printable
            ) or isinstance(event, events.Paste)
            if (
                is_text_input
                and not event.is_forwarded
                and not self.follow_up_queue_editor_open
                and not self._is_runner_control(self.focused)
                and not isinstance(self.focused, TextArea)
            ):
                self._last_screen_selection = ""
                try:
                    prompt = self.query_one("#prompt", PromptEditor)
                except Exception:
                    pass
                else:
                    if self.focused is not prompt:
                        prompt.focus()
                        insert = event.character if isinstance(event, events.Key) else event.text
                        if insert:
                            result = prompt.replace(
                                insert,
                                *prompt.selection,
                                maintain_selection_offset=False,
                            )
                            prompt.move_cursor(result.end_location)
                        event.stop()
                        event.prevent_default()
                        return
            await super().on_event(event)

        @on(events.TextSelected)
        def _on_screen_text_selected(self, _event: events.TextSelected) -> None:
            self._complete_pointer_selection()

        @on(events.Click)
        def _on_screen_multi_click(self, event: events.Click) -> None:
            if event.chain >= 2:
                # Double/triple click selection is applied by Widget._on_click,
                # after Screen has already emitted TextSelected for mouse-up.
                self.call_after_refresh(self._complete_pointer_selection)

        def _cancel_follow_up_queue_click(self) -> None:
            timer = self._follow_up_queue_click_timer
            if timer is not None:
                timer.stop()
            self._follow_up_queue_click_timer = None
            self._follow_up_queue_click_item = None

        def _open_clicked_follow_up(self, item: QueuedFollowUp) -> None:
            self._follow_up_queue_click_timer = None
            if self._follow_up_queue_click_item is not item:
                return
            self._follow_up_queue_click_item = None
            if self.follow_up_queue_editor_open or self._screen_selection_active():
                return
            for position, queued in enumerate(self.follow_up_queue, 1):
                if queued is item:
                    self._handle_queue([str(position)])
                    return

        @on(events.Click, "#follow-up-queue")
        def _on_follow_up_queue_clicked(self, event: events.Click) -> None:
            if event.button != 1:
                return
            if event.chain >= 2:
                self._cancel_follow_up_queue_click()
                return
            if (
                event.chain != 1
                or self.follow_up_queue_editor_open
                or not self.follow_up_queue
                or self._selection_drag_active()
                or self._screen_selection_active()
            ):
                return
            position = event.style.meta.get("pcr_queue_position")
            if isinstance(position, bool) or not isinstance(position, int):
                position = 1
            if not 1 <= position <= len(self.follow_up_queue):
                return
            item = self.follow_up_queue[position - 1]
            self._cancel_follow_up_queue_click()
            self._follow_up_queue_click_item = item
            # Waiting for the click chain keeps double-click text selection intact.
            self._follow_up_queue_click_timer = self.set_timer(
                self.CLICK_CHAIN_TIME_THRESHOLD,
                lambda: self._open_clicked_follow_up(item),
            )
            event.stop()

        def _complete_pointer_selection(self) -> None:
            selected = self.screen.get_selected_text() or ""
            if selected:
                if selected != self._last_screen_selection:
                    self.copy_to_clipboard(selected)
                self._last_screen_selection = selected
                self._flush_selection_deferred_updates()
                return
            self._last_screen_selection = ""
            if not self._mouse_down_in_runner_control:
                self.query_one("#prompt", PromptEditor).focus()
            self._flush_selection_deferred_updates()

        def compose(self) -> ComposeResult:
            with Vertical(id="root"):
                with Vertical(id="runner-frame"):
                    with Grid(id="runner-grid"):
                        yield Static("CONVERSATION", classes="runner-key")
                        yield Static(
                            "",
                            id="runner-conversation",
                            classes="runner-value runner-context-value",
                            markup=False,
                        )
                        yield Static("CODEX_BIN", classes="runner-key")
                        yield Static(
                            "",
                            id="runner-codex-bin",
                            classes="runner-value runner-context-value",
                            markup=False,
                        )
                        yield Static("WORKSPACE", classes="runner-key")
                        yield Static(
                            "",
                            id="runner-workspace",
                            classes="runner-value runner-context-value",
                            markup=False,
                        )
                        yield Static("RUNS_ROOT", classes="runner-key")
                        yield Static(
                            "",
                            id="runner-runs-root",
                            classes="runner-value runner-context-value",
                            markup=False,
                        )
                        yield Static("AGENTS", classes="runner-key")
                        yield Input(
                            str(self.num_agents),
                            id="config-agents",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("SYNTHESIS_AGENTS", classes="runner-key")
                        yield Input(
                            str(self.synthesis_agents),
                            id="config-synthesis-agents",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("EXECUTION", classes="runner-key")
                        yield Select(
                            [("parallel", "parallel"), ("serial", "serial")],
                            value="serial" if getattr(self.args, "serial", False) else "parallel",
                            allow_blank=False,
                            compact=True,
                            id="config-execution",
                            classes="runner-control",
                        )
                        yield Static("MAX_PARALLEL", classes="runner-key")
                        yield Input(
                            str(dict(self._base_info_rows())["MAX_PARALLEL"]),
                            id="config-max-parallel",
                            classes="runner-control",
                        )
                        yield Static("SUBAGENTS", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=bool(getattr(self.args, "subagents", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-subagents",
                            classes="runner-control",
                        )
                        yield Static("SUBAGENTS_LIMIT", classes="runner-key")
                        yield Input(
                            str(
                                getattr(
                                    self.args,
                                    "subagents_limit",
                                    DEFAULT_SUBAGENTS_LIMIT,
                                )
                            ),
                            id="config-subagents-limit",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("RECOMMEND_BY", classes="runner-key")
                        yield Select(
                            [("reasoning_tokens", "reasoning_tokens"), ("duration", "duration")],
                            value=str(
                                getattr(self.args, "recommend_by", "reasoning_tokens")
                            ),
                            allow_blank=False,
                            compact=True,
                            id="config-recommend-by",
                            classes="runner-control",
                        )
                        yield Static("MODEL", classes="runner-key")
                        yield Select(
                            self.model_choices,
                            value=str(getattr(self.args, "model", None) or ""),
                            allow_blank=False,
                            compact=True,
                            id="config-model",
                            classes="runner-control",
                        )
                        yield Static("EFFORT", classes="runner-key")
                        yield Select(
                            self.effort_choices,
                            value=str(getattr(self.args, "effort", None) or ""),
                            allow_blank=False,
                            compact=True,
                            id="config-effort",
                            classes="runner-control",
                        )
                        yield Static("FAST", classes="runner-key")
                        yield Select(
                            [
                                (
                                    format_fast_mode(
                                        None,
                                        self.model_registry.configured_service_tier,
                                    ),
                                    "auto",
                                ),
                                ("YES", "on"),
                                ("NO", "off"),
                            ],
                            value=fast_control_value(
                                getattr(self.args, "fast", None)
                            ),
                            allow_blank=False,
                            compact=True,
                            id="config-fast",
                            classes="runner-control",
                        )
                        yield Static("SYNTHESIS_MODEL", classes="runner-key")
                        yield Select(
                            self.synthesis_model_choices,
                            value=self._synthesis_model_control_value(),
                            allow_blank=False,
                            compact=True,
                            id="config-synthesis-model",
                            classes="runner-control",
                        )
                        yield Static("SYNTHESIS_EFFORT", classes="runner-key")
                        yield Select(
                            self.synthesis_effort_choices,
                            value=self._synthesis_effort_control_value(),
                            allow_blank=False,
                            compact=True,
                            id="config-synthesis-effort",
                            classes="runner-control",
                        )
                        yield Static("SYNTHESIS_FAST", classes="runner-key")
                        yield Select(
                            self.synthesis_fast_choices,
                            value=self._synthesis_fast_control_value(),
                            allow_blank=False,
                            compact=True,
                            id="config-synthesis-fast",
                            classes="runner-control",
                        )
                        yield Static("FOLLOW_UP_DELAY", classes="runner-key")
                        yield Input(
                            str(self._follow_up_delay_seconds()),
                            id="config-follow-up-delay",
                            classes="runner-control",
                            type="integer",
                        )
                        yield Static("SYNC_BACK", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=not bool(getattr(self.args, "no_sync_back", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-sync-back",
                            classes="runner-control",
                        )
                        yield Static("KEEP_WORKSPACES", classes="runner-key")
                        yield Select(
                            [("YES", True), ("NO", False)],
                            value=bool(getattr(self.args, "keep_workspaces", False)),
                            allow_blank=False,
                            compact=True,
                            id="config-keep-workspaces",
                            classes="runner-control",
                        )
                        yield Static("RESUME", classes="runner-key")
                        yield Select(
                            self.resume_choices,
                            value=self.resume_session_id,
                            allow_blank=False,
                            compact=True,
                            id="config-resume",
                            classes="runner-control",
                        )
                        yield Static(
                            "RECOMMENDED AGENT",
                            id="runner-recommended-agent-key",
                            classes="runner-key",
                        )
                        yield Static("", id="runner-recommended-agent", classes="runner-value", markup=False)
                with RainbowDetailFrame(id="detail-frame"):
                    with DetailScroll(id="detail-scroll"):
                        yield DetailView("", id="detail")
                yield Static("", id="suggestions")
                yield Static(self._tip_renderable(), id="tips")
                with VerticalScroll(id="follow-up-queue-frame"):
                    yield FollowUpQueueView("", id="follow-up-queue", markup=False)
                yield PromptEditor(
                    "",
                    id="prompt",
                    soft_wrap=True,
                    show_line_numbers=False,
                    placeholder="输入需求，或输入 / 查看命令",
                )
                yield Static("", id="state")

        def on_mount(self) -> None:
            self.query_one("#runner-frame", Vertical).border_title = "PARALLEL-CODEX-RUNNER"
            self._sync_runner_layout(self.size.width)
            self.set_interval(0.25, self._tick)
            self.set_interval(TIP_ROTATION_SECONDS, self._advance_tip)
            self.set_interval(TIP_ICON_REFRESH_SECONDS, self._advance_tip_icon)
            self.set_interval(
                RECOMMEND_BORDER_REFRESH_SECONDS,
                self._advance_recommend_border,
            )
            if getattr(self.args, "resume", False) and not self.resume_session_id:
                self._handle_resume([])
            elif self.resume_session_id:
                self._select_resume_session(self.resume_session_id)
            else:
                self._sync()
            # Restoring the selected session must not suppress discovery of
            # other sessions in the picker.
            if not self.is_headless:
                self._refresh_resume_control()
            self.query_one("#prompt", PromptEditor).focus()

        def on_resize(self, event: events.Resize) -> None:
            self._sync_runner_layout(event.size.width)

        def _sync_runner_layout(self, width: int) -> None:
            if width >= RUNNER_THREE_PAIR_MIN_WIDTH:
                columns = 6
            elif width >= RUNNER_TWO_PAIR_MIN_WIDTH:
                columns = 4
            else:
                columns = 2
            if columns == self._runner_grid_columns:
                return
            try:
                grid = self.query_one("#runner-grid", Grid)
            except Exception:
                return

            with self.batch_update():
                context_span = columns - 1
                for value in self.query(".runner-context-value"):
                    value.styles.column_span = context_span
                grid.styles.grid_columns = " ".join(
                    ["18", "1fr"] * (columns // 2)
                )
                grid.styles.grid_size_columns = columns
                self._reorder_runner_config_pairs(grid, columns // 2)
            self._runner_grid_columns = columns

        def _reorder_runner_config_pairs(
            self,
            grid: Grid,
            pair_columns: int,
        ) -> None:
            if not self._runner_config_pairs or any(
                key.parent is not grid or value.parent is not grid
                for key, value in self._runner_config_pairs
            ):
                children = list(grid.children)
                pairs: list[tuple[Widget, Widget]] = []
                for index, child in enumerate(children[1:], 1):
                    if child.has_class("runner-control") or (
                        child.id == "runner-recommended-agent"
                    ):
                        pairs.append((children[index - 1], child))
                self._runner_config_pairs = tuple(pairs)

            anchor = grid.query_one("#runner-runs-root")
            for pair_index in _column_major_indices(
                len(self._runner_config_pairs),
                pair_columns,
            ):
                for child in self._runner_config_pairs[pair_index]:
                    children = list(grid.children)
                    anchor_index = children.index(anchor)
                    if (
                        anchor_index + 1 >= len(children)
                        or children[anchor_index + 1] is not child
                    ):
                        grid.move_child(child, after=anchor)
                    anchor = child

        def _current_prompt_history_context(self) -> tuple[str, str]:
            return self.prompt_history_store.context_key(
                self.workspace,
                self.resume_session_id,
            )

        def _prompt_history_entries(
            self,
            context: tuple[str, str],
        ) -> list[str]:
            return [
                normalize_prompt_text(entry)
                for entry in self.prompt_history_store.entries(*context)
            ]

        def _save_prompt_history_draft(self) -> None:
            self.prompt_history_drafts[self.prompt_history_context] = (
                self.prompt_history_navigator.draft
            )

        def _replace_prompt_text(self, text: str) -> None:
            text = normalize_prompt_text(text)
            try:
                prompt = self.query_one("#prompt", PromptEditor)
            except Exception:
                return
            if prompt.text != text:
                self._prompt_history_programmatic_values.append(text)
                prompt.text = text
            lines = text.split("\n")
            prompt.move_cursor((len(lines) - 1, len(lines[-1])))
            self._update_suggestions(text)
            self._sync_prompt_height()
            prompt.focus()

        def _clear_prompt_draft(self) -> None:
            self.prompt_history_navigator.note_edit("")
            self.prompt_history_drafts[self.prompt_history_context] = ""
            self._replace_prompt_text("")

        def _load_prompt_history_context(self, draft: str | None = None) -> None:
            self._save_prompt_history_draft()
            context = self._current_prompt_history_context()
            if draft is None:
                draft = self.prompt_history_drafts.get(context, "")
            draft = normalize_prompt_text(draft)
            self.prompt_history_context = context
            self.prompt_history_drafts[context] = draft
            self.prompt_history_navigator.reset(
                self._prompt_history_entries(context),
                draft,
            )
            self._replace_prompt_text(draft)

        def _storage_estimate_inputs(self) -> tuple[Path, str | None, Path | None]:
            source_workspace = self.workspace
            resume_session_id = self.resume_session_id or None
            resume_codex_home: Path | None = None
            if self._has_pending_run() and not self._pending_sync_disabled():
                pane = self.agents.get(self.selected_agent)
                result = pane.result if pane is not None else None
                if isinstance(result, dict) and result.get("status") == "success":
                    workspace_dir = result.get("workspace_dir")
                    if isinstance(workspace_dir, str) and workspace_dir:
                        source_workspace = Path(workspace_dir).expanduser().resolve()
                    thread_id = result.get("codex_thread_id")
                    if isinstance(thread_id, str) and thread_id.strip():
                        resume_session_id = thread_id.strip()
                    codex_home = result.get("codex_home")
                    if isinstance(codex_home, str) and codex_home:
                        resume_codex_home = Path(codex_home).expanduser().resolve()
            return source_workspace, resume_session_id, resume_codex_home

        def _storage_run_base(self) -> Path:
            return choose_run_base(
                self.workspace,
                getattr(self.args, "runs_dir", None),
            )

        def _storage_preflight_configuration(self) -> tuple[Any, ...]:
            pane = self.agents.get(self.selected_agent)
            result = pane.result if pane is not None else None
            result_values = (
                (
                    result.get("workspace_dir"),
                    result.get("codex_home"),
                    result.get("codex_thread_id"),
                )
                if isinstance(result, dict)
                else (None, None, None)
            )
            return (
                str(self.workspace),
                self.num_agents,
                self.synthesis_agents,
                self.resume_session_id,
                str(getattr(self.args, "runs_dir", None) or ""),
                self.selected_agent,
                str(self.pending_run_root or ""),
                self._pending_sync_disabled(),
                self._pending_keep_enabled(),
                *result_values,
            )

        def _large_storage_approval_key(self) -> str:
            return str(self.workspace.expanduser().resolve())

        def _restore_prompt_after_storage_abort(self, prompt_text: str) -> None:
            try:
                prompt = self.query_one("#prompt", PromptEditor)
            except Exception:
                return
            if prompt.text:
                return
            self.prompt_history_drafts[self.prompt_history_context] = prompt_text
            self.prompt_history_navigator.note_edit(prompt_text)
            self._replace_prompt_text(prompt_text)

        def _request_run_with_storage_check(
            self,
            prompt: str,
            record_history: bool = False,
            *,
            from_follow_up_queue: bool = False,
            bypass_follow_up_queue: bool = False,
            recorded_history_context: tuple[str, str] | None = None,
        ) -> bool:
            prompt = normalize_prompt_text(prompt).strip()
            if not prompt:
                return False
            if (
                self.follow_up_queue
                and not from_follow_up_queue
                and not bypass_follow_up_queue
                and not self.running
            ):
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                    recorded_history_context=recorded_history_context,
                )
            if self.running:
                return self._start_run(
                    prompt,
                    record_history=record_history,
                    **_recorded_history_context_kwargs(
                        recorded_history_context
                    ),
                )
            if self.storage_preflight_inflight:
                self.status = "A storage check is already in progress"
                self._sync()
                return False
            if not self._commit_runner_inputs():
                self._sync()
                return False

            source_workspace, resume_session_id, resume_codex_home = (
                self._storage_estimate_inputs()
            )
            try:
                run_base = self._storage_run_base()
            except Exception as exc:  # noqa: BLE001
                self.status = f"Run failed: cannot resolve run storage: {exc}"
                self._sync()
                return False

            reclaimable_root: Path | None = None
            if (
                self._has_pending_run()
                and not self._pending_keep_enabled()
                and self.pending_run_root is not None
                and self.pending_workspaces_root is not None
                and self.pending_run_root.parent.resolve() == run_base.resolve()
            ):
                reclaimable_root = self.pending_workspaces_root

            self.storage_preflight_request += 1
            request_id = self.storage_preflight_request
            self.storage_preflight_inflight = True
            candidate_agents = self.num_agents
            synthesis_agents = self.synthesis_agents
            codex_home = get_codex_home()
            configuration_key = self._storage_preflight_configuration()
            self.status = (
                f"Estimating storage for {candidate_agents} candidates"
                + (
                    f" + {synthesis_agents} synthesis agents"
                    if synthesis_agents
                    else ""
                )
            )
            self._sync()

            def target() -> None:
                estimate: RunStorageEstimate | None = None
                error = ""
                reclaimable_bytes = 0
                try:
                    estimate = estimate_staged_run_storage(
                        source_workspace,
                        candidate_agents,
                        synthesis_agents,
                        resume_session_id=resume_session_id,
                        codex_home=codex_home,
                        resume_codex_home=resume_codex_home,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                if not error and reclaimable_root is not None:
                    try:
                        reclaimable_bytes = estimate_path_storage_bytes(
                            reclaimable_root
                        )
                    except OSError:
                        reclaimable_bytes = 0
                try:
                    self.call_from_thread(
                        self.post_message,
                        StoragePreflightFinished(
                            request_id,
                            prompt,
                            record_history,
                            run_base,
                            estimate,
                            error,
                            reclaimable_bytes,
                            configuration_key,
                            from_follow_up_queue,
                            bypass_follow_up_queue,
                            recorded_history_context,
                        ),
                    )
                except RuntimeError:
                    pass

            threading.Thread(
                target=target,
                name="pcr-storage-preflight",
                daemon=True,
            ).start()
            return True

        def _restart_changed_storage_preflight(
            self,
            event: StoragePreflightFinished,
        ) -> bool:
            if (
                event.configuration_key
                and event.configuration_key != self._storage_preflight_configuration()
            ):
                self.storage_preflight_inflight = False
                if not self._request_run_with_storage_check(
                    event.prompt,
                    record_history=event.record_history,
                    from_follow_up_queue=event.from_follow_up_queue,
                    bypass_follow_up_queue=event.bypass_follow_up_queue,
                    **_recorded_history_context_kwargs(
                        event.recorded_history_context
                    ),
                ):
                    if not event.from_follow_up_queue:
                        self._restore_prompt_after_storage_abort(event.prompt)
                return True
            return False

        def _complete_storage_preflight(
            self,
            event: StoragePreflightFinished,
            continue_run: bool,
            *,
            remember_large_storage_approval: bool = False,
        ) -> None:
            if event.request_id != self.storage_preflight_request:
                return
            if not continue_run:
                self.storage_preflight_inflight = False
                if event.from_follow_up_queue:
                    self._consume_follow_up(event.prompt)
                else:
                    self._restore_prompt_after_storage_abort(event.prompt)
                self.status = "Run canceled: storage confirmation declined"
                self._sync()
                self.query_one("#prompt", PromptEditor).focus()
                return
            if self._restart_changed_storage_preflight(event):
                return

            estimate = event.estimate
            if estimate is None:
                self.storage_preflight_inflight = False
                self.status = "Run failed: storage estimate is unavailable"
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_abort(event.prompt)
                self._sync()
                return
            if remember_large_storage_approval:
                self._approved_large_storage_workspaces.add(
                    self._large_storage_approval_key()
                )
            try:
                available = available_storage_bytes(event.run_base)
            except Exception as exc:  # noqa: BLE001
                self.storage_preflight_inflight = False
                self.status = f"Run failed: cannot inspect available disk space: {exc}"
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_abort(event.prompt)
                self._sync()
                return
            usable = available + event.reclaimable_bytes
            if estimate.total_bytes > usable:
                self.storage_preflight_inflight = False
                available_text = format_storage_bytes(available)
                if event.reclaimable_bytes:
                    available_text += (
                        f" + {format_storage_bytes(event.reclaimable_bytes)} "
                        "reclaimable"
                    )
                self.status = (
                    "Run failed: insufficient disk space "
                    f"(need {format_storage_bytes(estimate.total_bytes)}, "
                    f"available {available_text})"
                )
                if not event.from_follow_up_queue:
                    self._restore_prompt_after_storage_abort(event.prompt)
                self._sync()
                return

            self.storage_preflight_inflight = False
            started = self._start_run(
                event.prompt,
                record_history=event.record_history,
                **_recorded_history_context_kwargs(
                    event.recorded_history_context
                ),
            )
            if started and event.from_follow_up_queue:
                self._consume_follow_up(event.prompt)
            elif not started and not event.from_follow_up_queue:
                self._restore_prompt_after_storage_abort(event.prompt)
                self._sync()

        @on(StoragePreflightFinished)
        def _on_storage_preflight_finished(
            self,
            event: StoragePreflightFinished,
        ) -> None:
            if event.request_id != self.storage_preflight_request:
                return
            if self._restart_changed_storage_preflight(event):
                return
            if event.error or event.estimate is None:
                self.storage_preflight_inflight = False
                message = event.error or "storage estimate is unavailable"
                self.status = f"Run failed: cannot estimate storage: {message}"
                if event.from_follow_up_queue:
                    self._consume_follow_up(event.prompt)
                else:
                    self._restore_prompt_after_storage_abort(event.prompt)
                self._sync()
                return
            warning_approved = (
                self._large_storage_approval_key()
                in self._approved_large_storage_workspaces
            )
            if (
                event.estimate.total_bytes > LARGE_RUN_STORAGE_WARNING_BYTES
                and not event.from_follow_up_queue
                and not warning_approved
            ):
                self.status = (
                    "Storage confirmation required: "
                    f"{format_storage_bytes(event.estimate.total_bytes)} estimated"
                )
                self._sync()
                self.push_screen(
                    StorageWarningScreen(event.estimate),
                    lambda confirmed: self._complete_storage_preflight(
                        event,
                        bool(confirmed),
                        remember_large_storage_approval=bool(confirmed),
                    ),
                )
                return
            self._complete_storage_preflight(event, True)

        def _submit_task_prompt(self, prompt: str) -> bool:
            prompt = normalize_prompt_text(prompt).strip()
            if not prompt:
                return False
            actual_context = self._current_prompt_history_context()
            if actual_context != self.prompt_history_context:
                self._load_prompt_history_context()
            submitted_from_context = self.prompt_history_context
            if not self._request_run_with_storage_check(prompt, record_history=True):
                return False
            self.prompt_history_drafts[submitted_from_context] = ""
            self._load_prompt_history_context(draft="")
            return True

        def _record_prompt_history(
            self,
            prompt: str,
            context: tuple[str, str] | None = None,
        ) -> tuple[str, str] | None:
            prompt = normalize_prompt_text(prompt)
            context = context or self._current_prompt_history_context()
            if not self.prompt_history_store.append(*context, prompt):
                return None
            if context == self.prompt_history_context:
                draft = self.prompt_history_navigator.draft
                self.prompt_history_navigator.reset(
                    self._prompt_history_entries(context),
                    draft,
                )
            return context

        def _record_started_prompt(self, prompt: str) -> None:
            self._record_prompt_history(prompt)

        def _associate_pending_prompt_with_context(
            self,
            context: tuple[str, str],
        ) -> None:
            prompt = self.pending_prompt
            if not prompt:
                return
            self.prompt_history_store.append(
                *context,
                prompt,
                deduplicate_last=True,
            )

        @on(PromptSubmitted)
        def _on_prompt(self, event: PromptSubmitted) -> None:
            prompt = self.query_one("#prompt", PromptEditor)
            value = event.value.strip()
            if not value:
                return
            if value.startswith("/"):
                self._clear_prompt_draft()
                self._handle_command(value)
            else:
                self._submit_task_prompt(value)
            prompt.focus()

        @on(TextArea.Changed)
        def _on_text_changed(self, event: TextArea.Changed) -> None:
            if event.text_area.id == "prompt":
                text = event.text_area.text
                try:
                    expected_index = self._prompt_history_programmatic_values.index(text)
                except ValueError:
                    self._prompt_history_programmatic_values.clear()
                    self.prompt_history_navigator.note_edit(text)
                    self.prompt_history_drafts[self.prompt_history_context] = text
                else:
                    del self._prompt_history_programmatic_values[: expected_index + 1]
                self._update_suggestions(event.text_area.text)
                self._sync_prompt_height()

        @on(PromptHistoryRequested)
        def _on_prompt_history_requested(self, event: PromptHistoryRequested) -> None:
            context = self._current_prompt_history_context()
            if context != self.prompt_history_context:
                self._load_prompt_history_context()
            prompt = self.query_one("#prompt", PromptEditor)
            text = self.prompt_history_navigator.navigate(
                event.direction,
                prompt.text,
            )
            self._replace_prompt_text(text)

        @on(AgentSwitchRequested)
        def _on_switch(self, event: AgentSwitchRequested) -> None:
            self._switch_agent(event.delta)

        def _set_committed_input_value(self, control: Input, value: str) -> None:
            self._updating_controls = True
            try:
                control.value = value
                if control.id is not None:
                    self._committed_input_values[control.id] = value
            finally:
                self._updating_controls = False

        def _commit_agents_control(self) -> bool:
            try:
                control = self.query_one("#config-agents", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get("config-agents"):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_numofagents([value_text])
            applied = requested is not None and requested > 0 and self.num_agents == requested
            self._set_committed_input_value(control, str(self.num_agents))
            return applied

        def _commit_synthesis_agents_control(self) -> bool:
            try:
                control = self.query_one("#config-synthesis-agents", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get(
                "config-synthesis-agents"
            ):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_synthesis([value_text])
            applied = (
                requested is not None
                and requested >= 0
                and self.synthesis_agents == requested
            )
            self._set_committed_input_value(
                control,
                str(self.synthesis_agents),
            )
            return applied

        def _commit_max_parallel_control(self) -> bool:
            try:
                control = self.query_one("#config-max-parallel", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get("config-max-parallel"):
                return True
            normalized = value_text.lower()
            if normalized in {"auto", "default", "clear", "none"}:
                requested: int | None = None
                valid = True
            else:
                try:
                    requested = int(normalized)
                except ValueError:
                    requested = None
                    valid = False
                else:
                    valid = requested > 0
            self._handle_maxparallel([value_text])
            applied = valid and getattr(self.args, "max_parallel", None) == requested
            display_value = dict(self._base_info_rows()).get("MAX_PARALLEL", "")
            self._set_committed_input_value(control, display_value)
            return applied

        def _commit_subagents_limit_control(self) -> bool:
            try:
                control = self.query_one("#config-subagents-limit", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get(
                "config-subagents-limit"
            ):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_subagentslimit([value_text])
            applied = (
                requested is not None
                and requested > 0
                and getattr(self.args, "subagents_limit", None) == requested
            )
            display_value = str(
                getattr(
                    self.args,
                    "subagents_limit",
                    DEFAULT_SUBAGENTS_LIMIT,
                )
            )
            self._set_committed_input_value(control, display_value)
            return applied

        def _commit_follow_up_delay_control(self) -> bool:
            try:
                control = self.query_one("#config-follow-up-delay", Input)
            except Exception:
                return True
            value_text = control.value.strip()
            if value_text == self._committed_input_values.get(
                "config-follow-up-delay"
            ):
                return True
            try:
                requested = int(value_text)
            except ValueError:
                requested = None
            self._handle_followupdelay([value_text])
            applied = (
                requested is not None
                and requested >= 0
                and self._follow_up_delay_seconds() == requested
            )
            self._set_committed_input_value(
                control,
                str(self._follow_up_delay_seconds()),
            )
            return applied

        def _model_effort_control_has_pending_value(
            self,
            control: Select,
        ) -> bool:
            control_id = control.id
            if control_id not in self._committed_model_effort_values:
                return False
            return str(control.value or "") != self._committed_model_effort_values[
                control_id
            ]

        def _mark_model_effort_control_committed(
            self,
            control: Select,
        ) -> None:
            if control.id in self._committed_model_effort_values:
                self._committed_model_effort_values[control.id] = str(
                    control.value or ""
                )

        def _fast_control_has_pending_value(self, control: Select) -> bool:
            committed = (
                self._committed_synthesis_fast_value
                if control.id == "config-synthesis-fast"
                else self._committed_fast_value
            )
            return str(control.value or "") != committed

        def _mark_fast_control_committed(self, control: Select) -> None:
            if control.id == "config-fast":
                self._committed_fast_value = str(control.value or "auto")
            elif control.id == "config-synthesis-fast":
                self._committed_synthesis_fast_value = str(
                    control.value or "auto"
                )

        def _commit_model_control(self) -> bool:
            try:
                control = self.query_one("#config-model", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip()
            current = str(getattr(self.args, "model", None) or "").strip()
            if requested != current:
                self._handle_model([requested or "clear"])
                current = str(getattr(self.args, "model", None) or "").strip()
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_effort_control(self) -> bool:
            # MODEL and EFFORT are one logical setting. Textual may deliver the
            # EFFORT event first, so commit the model currently shown beside it
            # before validating the requested effort.
            if not self._commit_model_control():
                return False
            try:
                control = self.query_one("#config-effort", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip().lower()
            current = str(getattr(self.args, "effort", None) or "").strip().lower()
            if requested != current:
                self._handle_effort([requested or "auto"])
                current = (
                    str(getattr(self.args, "effort", None) or "").strip().lower()
                )
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_model_effort_controls(self) -> bool:
            try:
                model_control = self.query_one("#config-model", Select)
                effort_control = self.query_one("#config-effort", Select)
            except Exception:
                return True
            model = str(model_control.value or "").strip() or None
            effort = str(effort_control.value or "").strip().lower() or None

            # Do not route this snapshot through the command handlers: they
            # repaint the panel and could overwrite another unsubmitted input
            # before _commit_runner_inputs has read it.
            self.args.model = model
            self._mark_model_effort_control_committed(model_control)
            if self._effort_model_is_known():
                try:
                    self.model_registry.validate_effort(
                        self._model_for_effort(),
                        effort,
                    )
                except ValueError as exc:
                    self.status = str(exc)
                    self.run_info_rows = self._base_info_rows()
                    effort_control.focus()
                    return False

            self.args.effort = effort
            self._mark_model_effort_control_committed(effort_control)
            self.run_info_rows = self._base_info_rows()
            return True

        def _commit_synthesis_model_control(self) -> bool:
            try:
                control = self.query_one("#config-synthesis-model", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip()
            current = self._synthesis_model_control_value()
            if requested != current:
                self._handle_synthesis_model([requested or "default"])
                current = self._synthesis_model_control_value()
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_synthesis_effort_control(self) -> bool:
            if not self._commit_synthesis_model_control():
                return False
            try:
                control = self.query_one("#config-synthesis-effort", Select)
            except Exception:
                return True
            requested = str(control.value or "").strip().lower()
            current = self._synthesis_effort_control_value()
            if requested != current:
                self._handle_synthesis_effort([requested or "auto"])
                current = self._synthesis_effort_control_value()
            if current != requested:
                control.focus()
                return False
            self._mark_model_effort_control_committed(control)
            return True

        def _commit_synthesis_model_effort_controls(self) -> bool:
            try:
                model_control = self.query_one(
                    "#config-synthesis-model",
                    Select,
                )
                effort_control = self.query_one(
                    "#config-synthesis-effort",
                    Select,
                )
            except Exception:
                return True
            model_value = str(model_control.value or "").strip()
            effort_value = str(effort_control.value or "").strip().lower()
            requested_model = normalize_synthesis_model(
                model_value or "default"
            )
            requested_effort = normalize_synthesis_effort(
                effort_value or "auto"
            )
            if (
                requested_model == self._synthesis_model_setting()
                and requested_effort == self._synthesis_effort_setting()
            ):
                self._mark_model_effort_control_committed(model_control)
                self._mark_model_effort_control_committed(effort_control)
                return True
            if not self._prepare_synthesis_config_change("synthesis settings"):
                return False
            previous = self._synthesis_config_snapshot()
            self.args.synthesis_model = requested_model
            self.args.synthesis_effort = requested_effort
            if self._synthesis_effort_model_is_known():
                settings = effective_synthesis_codex_settings(self.args)
                try:
                    self.model_registry.validate_effort(
                        self._synthesis_model_for_effort(),
                        settings.effort,
                    )
                except ValueError as exc:
                    self._restore_synthesis_config(previous)
                    self.status = str(exc)
                    self._refresh_synthesis_config_rows()
                    effort_control.focus()
                    return False

            if not self._publish_synthesis_config(previous, "synthesis settings"):
                return False
            self._mark_model_effort_control_committed(model_control)
            self._mark_model_effort_control_committed(effort_control)
            self._refresh_synthesis_config_rows()
            return True

        def _commit_fast_control(self) -> bool:
            try:
                control = self.query_one("#config-fast", Select)
            except Exception:
                return True
            requested_text = str(control.value or "").strip().lower()
            values: dict[str, bool | None] = {
                "auto": None,
                "on": True,
                "off": False,
            }
            if requested_text not in values:
                self.status = "Usage: /fast <on|off|auto>"
                control.focus()
                return False
            self.args.fast = values[requested_text]
            self._mark_fast_control_committed(control)
            self.run_info_rows = self._base_info_rows()
            return True

        def _commit_synthesis_fast_control(self) -> bool:
            try:
                control = self.query_one("#config-synthesis-fast", Select)
            except Exception:
                return True
            requested_text = str(control.value or "").strip().lower()
            try:
                value = normalize_synthesis_fast(requested_text)
            except argparse.ArgumentTypeError:
                self.status = "Usage: /synthesisfast <auto|on|off>"
                control.focus()
                return False
            if value == self._synthesis_fast_setting():
                self._mark_fast_control_committed(control)
                return True
            if not self._prepare_synthesis_config_change("Synthesis Fast mode"):
                return False
            previous = self._synthesis_config_snapshot()
            self.args.synthesis_fast = value
            if not self._publish_synthesis_config(
                previous,
                "Synthesis Fast mode",
            ):
                return False
            self._mark_fast_control_committed(control)
            self._refresh_synthesis_config_rows()
            return True

        def _commit_runner_inputs(self) -> bool:
            # Select.Changed is queued. Commit the visible MODEL/EFFORT pair
            # before numeric handlers can trigger a repaint from stale args.
            if not self._commit_model_effort_controls():
                return False
            if not self._commit_synthesis_model_effort_controls():
                return False
            if not self._commit_fast_control():
                return False
            if not self._commit_synthesis_fast_control():
                return False
            if not self._commit_agents_control():
                self.query_one("#config-agents", Input).focus()
                return False
            if not self._commit_synthesis_agents_control():
                self.query_one("#config-synthesis-agents", Input).focus()
                return False
            if not self._commit_max_parallel_control():
                self.query_one("#config-max-parallel", Input).focus()
                return False
            if not self._commit_subagents_limit_control():
                self.query_one("#config-subagents-limit", Input).focus()
                return False
            if not self._commit_follow_up_delay_control():
                self.query_one("#config-follow-up-delay", Input).focus()
                return False
            return True

        @on(Input.Submitted, "#config-agents")
        def _on_agents_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_agents_control()

        @on(events.DescendantBlur, "#config-agents")
        def _on_agents_blurred(self, _event: events.DescendantBlur) -> None:
            if not self._updating_controls:
                self._commit_agents_control()

        @on(Input.Submitted, "#config-synthesis-agents")
        def _on_synthesis_agents_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_synthesis_agents_control()

        @on(events.DescendantBlur, "#config-synthesis-agents")
        def _on_synthesis_agents_blurred(
            self,
            _event: events.DescendantBlur,
        ) -> None:
            if not self._updating_controls:
                self._commit_synthesis_agents_control()

        @on(Input.Submitted, "#config-max-parallel")
        def _on_max_parallel_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_max_parallel_control()

        @on(events.DescendantBlur, "#config-max-parallel")
        def _on_max_parallel_blurred(self, _event: events.DescendantBlur) -> None:
            if not self._updating_controls:
                self._commit_max_parallel_control()

        @on(Input.Submitted, "#config-subagents-limit")
        def _on_subagents_limit_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_subagents_limit_control()

        @on(events.DescendantBlur, "#config-subagents-limit")
        def _on_subagents_limit_blurred(
            self,
            _event: events.DescendantBlur,
        ) -> None:
            if not self._updating_controls:
                self._commit_subagents_limit_control()

        @on(Input.Submitted, "#config-follow-up-delay")
        def _on_follow_up_delay_submitted(self, _event: Input.Submitted) -> None:
            if self._updating_controls:
                return
            self._commit_follow_up_delay_control()

        @on(events.DescendantBlur, "#config-follow-up-delay")
        def _on_follow_up_delay_blurred(
            self,
            _event: events.DescendantBlur,
        ) -> None:
            if not self._updating_controls:
                self._commit_follow_up_delay_control()

        def _accept_select_event(self, event: Select.Changed) -> bool:
            """Ignore delayed Select messages that predate the current choice."""
            if self._updating_controls:
                return False
            control_key = event.select.id or str(id(event.select))
            previous_time = self._latest_select_event_time.get(control_key)
            if previous_time is not None and event.time < previous_time:
                return False
            self._latest_select_event_time[control_key] = event.time
            return True

        @on(Select.Changed, "#config-execution")
        def _on_execution_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            serial = str(event.value) == "serial"
            current_serial = dict(self._tree_rows()).get("EXECUTION") == "serial"
            if serial != current_serial:
                self._handle_execution(serial=serial)

        @on(Select.Changed, "#config-subagents")
        def _on_subagents_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            value = bool(event.value)
            if value != bool(getattr(self.args, "subagents", False)):
                self._handle_subagents(["on" if value else "off"])

        @on(Select.Changed, "#config-recommend-by")
        def _on_recommend_by_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            value = str(event.value)
            if value != str(
                getattr(self.args, "recommend_by", "reasoning_tokens")
            ):
                self._handle_recommendby([value])

        @on(Select.Changed, "#config-model")
        def _on_model_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_model_control()

        @on(Select.Changed, "#config-effort")
        def _on_effort_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_effort_control()

        @on(Select.Changed, "#config-fast")
        def _on_fast_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            requested = fast_value_from_control(event.value)
            current = getattr(self.args, "fast", None)
            if requested is current:
                self._mark_fast_control_committed(event.select)
                return
            if not self._prepare_config_change("Fast mode"):
                return
            if self._commit_fast_control():
                self._show_setting(
                    "fast="
                    + fast_setting_value(
                        self.args.fast,
                        self.model_registry.configured_service_tier,
                    )
                )

        @on(Select.Changed, "#config-synthesis-model")
        def _on_synthesis_model_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_synthesis_model_control()

        @on(Select.Changed, "#config-synthesis-effort")
        def _on_synthesis_effort_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            self._commit_synthesis_effort_control()

        @on(Select.Changed, "#config-synthesis-fast")
        def _on_synthesis_fast_selected(self, event: Select.Changed) -> None:
            if (
                event.value != event.select.value
                or not self._accept_select_event(event)
            ):
                return
            requested = normalize_synthesis_fast(event.value)
            current = getattr(
                self.args,
                "synthesis_fast",
                None,
            )
            if requested == current:
                self._mark_fast_control_committed(event.select)
                return
            if not self._prepare_config_change("Synthesis Fast mode"):
                return
            if self._commit_synthesis_fast_control():
                self._show_setting(
                    f"synthesisfast={self._synthesis_fast_display()}"
                )

        @on(Select.Changed, "#config-sync-back")
        def _on_sync_back_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            current = not bool(getattr(self.args, "no_sync_back", False))
            value = bool(event.value)
            if value != current:
                self._handle_syncback(["on" if value else "off"])

        @on(Select.Changed, "#config-keep-workspaces")
        def _on_keep_workspaces_toggled(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            current = bool(getattr(self.args, "keep_workspaces", False))
            value = bool(event.value)
            if value != current:
                self._handle_keepworkspaces(["on" if value else "off"])

        @on(Select.Changed, "#config-resume")
        def _on_resume_selected(self, event: Select.Changed) -> None:
            if not self._accept_select_event(event):
                return
            session_id = str(event.value)
            if session_id == self.resume_session_id:
                return
            self._handle_resume([session_id] if session_id else ["clear"])

        async def _on_key(self, event: events.Key) -> None:
            if event.key in {"left", "right"}:
                prompt = self.query_one("#prompt", PromptEditor)
                if (
                    self.focused is not prompt
                    and not isinstance(self.focused, (Input, Select, TextArea))
                    and (
                        not prompt.text.strip()
                        or self._pointer_requests_agent_switch()
                    )
                ):
                    event.stop()
                    event.prevent_default()
                    self._switch_agent(-1 if event.key == "left" else 1)
                    prompt.focus()
                    return
            # MessagePump dispatches App._on_key separately through the MRO.
            # Calling it here would execute focused-widget bindings twice.

        def _switch_agent(self, delta: int) -> None:
            if self.storage_preflight_inflight:
                self.status = "Cannot switch agents while checking storage"
                self._sync()
                return
            last_agent = max(self.agents, default=self.num_agents)
            previous_agent = self.selected_agent
            self.selected_agent = min(last_agent, max(1, self.selected_agent + delta))
            # Switching agents changes the continuation source but preserves the
            # countdown. Restarting the timer here would extend the delay by the
            # full follow_up_delay every time the user pressed ←/→.
            self._sync()

        def _agent_kill_requested(self, idx: int) -> bool:
            event = self.agent_cancel_events.get(idx)
            return bool(event is not None and event.is_set())

        @on(RunnerEvent)
        def _on_runner_event(self, event: RunnerEvent) -> None:
            payload = event.payload
            kind = str(payload.get("type") or "")
            idx = int(payload.get("idx") or 0)
            role = normalize_agent_role(payload.get("role"))
            if kind == "synthesis_started":
                self.core_synthesis_revision = self.candidate_revision
                raw_indices = payload.get("indices")
                indices = (
                    [
                        int(value)
                        for value in raw_indices
                        if isinstance(value, int) and value > 0
                    ]
                    if isinstance(raw_indices, list)
                    else []
                )
                user_prompt = str(
                    payload.get("user_prompt")
                    or payload.get("prompt")
                    or self.pending_prompt
                )
                developer_instructions = str(
                    payload.get("developer_instructions") or ""
                )
                for synthesis_idx in indices:
                    pane = self.agents.get(synthesis_idx)
                    if pane is None:
                        self.agents[synthesis_idx] = AgentPane(
                            idx=synthesis_idx,
                            role=AGENT_ROLE_SYNTHESIS,
                            status="queued",
                            stale_synthesis=self.synthesis_refresh_needed,
                            input_text=user_prompt,
                            execution_prompt=user_prompt,
                            developer_instructions=developer_instructions,
                        )
                    else:
                        pane.role = AGENT_ROLE_SYNTHESIS
                        pane.stale_synthesis = self.synthesis_refresh_needed
                        pane.input_text = user_prompt
                        pane.execution_prompt = user_prompt
                        pane.developer_instructions = developer_instructions
                    self.agent_cancel_events.setdefault(
                        synthesis_idx,
                        threading.Event(),
                    )
                self.active_batch_indices = set(indices)
                if self.synthesis_refresh_needed:
                    self._cancel_stale_synthesis_agents()
                self.status = f"Running {len(indices)} synthesis agents"
                self._mark_detail_dirty()
            pane = self.agents.get(idx)
            if (
                pane is None
                and idx > 0
                and role == AGENT_ROLE_SYNTHESIS
                and kind in {
                    "agent_status",
                    "agent_started",
                    "agent_tokens",
                    "agent_line",
                    "agent_finished",
                }
            ):
                pane = AgentPane(
                    idx=idx,
                    role=role,
                    input_text=self.pending_prompt,
                )
                self.agents[idx] = pane
                self.agent_cancel_events.setdefault(idx, threading.Event())
            if kind == "run_prepared":
                rows = payload.get("rows")
                if isinstance(rows, list):
                    run_rows = [(str(k), str(v)) for k, v in rows if isinstance(k, str)]
                    self._remember_run_paths(run_rows)
                    prepared = dict(run_rows)
                    if self.running and self._synthesis_config_is_editable():
                        current = dict(self._base_info_rows())
                        for label in (
                            "SYNTHESIS_AGENTS",
                            "SYNTHESIS_MODEL",
                            "SYNTHESIS_EFFORT",
                            "SYNTHESIS_FAST",
                        ):
                            prepared[label] = current[label]
                    merged_rows = [
                        (label, prepared.pop(label, value))
                        for label, value in self._base_info_rows()
                    ]
                    merged_rows.extend(prepared.items())
                    self.run_info_rows = self._visible_info_rows(merged_rows)
                    prepared_effort = dict(run_rows).get("EFFORT", "")
                    if self.pending_execution_args is not None and prepared_effort:
                        self.pending_execution_args.effort = (
                            None if prepared_effort == "default" else prepared_effort
                        )
            elif kind == "agent_status" and pane is not None:
                pane.role = role
                pane.status = (
                    "stopping"
                    if self._agent_kill_requested(idx)
                    else str(payload.get("status") or pane.status)
                )
                self._mark_detail_dirty(pane)
            elif kind == "agent_started" and pane is not None:
                pane.role = role
                pane.status = "stopping" if self._agent_kill_requested(idx) else "running"
                self._mark_detail_dirty(pane)
            elif kind == "agent_tokens" and pane is not None:
                value = payload.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
                if "reasoning_token_counts" in payload:
                    pane.reasoning_token_counts = normalize_reasoning_token_counts(
                        payload.get("reasoning_token_counts")
                    )
            elif kind == "agent_line" and pane is not None:
                if not self._agent_kill_requested(idx):
                    pane.status = "running"
                category, text = display_line_parts_from_output(str(payload.get("text") or ""))
                pane.append(text, category)
                self._mark_detail_dirty(pane)
            elif kind == "agent_finished" and pane is not None:
                self.live_candidate_indices.discard(idx)
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                pane.role = normalize_agent_role(result.get("role", role))
                pane.result = result
                pane.status = str(result.get("status") or "finished")
                pane.diff_request += 1
                pane.show_diff = False
                pane.diff_loading = False
                pane.diff_text = ""
                pane.diff_error = ""
                value = result.get("reasoning_tokens")
                pane.reasoning_tokens = int(value) if isinstance(value, int) else pane.reasoning_tokens
                if "reasoning_token_counts" in result:
                    pane.reasoning_token_counts = normalize_reasoning_token_counts(
                        result.get("reasoning_token_counts")
                    )
                final_text = self._read_final_message(result) if pane.status == "success" else ""
                if final_text:
                    pane.final_text = final_text
                self._mark_detail_dirty(pane)
            elif kind == "synthesis_finished":
                self.active_batch_indices.clear()
                error = str(payload.get("error") or "")
                raw_indices = payload.get("indices")
                indices = raw_indices if isinstance(raw_indices, list) else []
                synthesis_indices = {
                    int(value)
                    for value in indices
                    if isinstance(value, int) and value > 0
                }
                if (
                    self.core_synthesis_revision is None
                    or self.core_synthesis_revision != self.candidate_revision
                    or self.synthesis_refresh_needed
                ):
                    self._mark_synthesis_results_stale(synthesis_indices)
                    if self._configured_synthesis_agents() > 0:
                        self.synthesis_refresh_needed = True
                else:
                    for value in synthesis_indices:
                        current_pane = self.agents.get(value)
                        if current_pane is not None:
                            current_pane.stale_synthesis = False
                self.core_synthesis_revision = None
                unresolved_status = ""
                if error:
                    unresolved_status = "error"
                elif payload.get("cancelled"):
                    unresolved_status = "cancelled"
                if unresolved_status:
                    for value in indices:
                        if not isinstance(value, int):
                            continue
                        unresolved = self.agents.get(value)
                        if unresolved is not None and unresolved.result is None:
                            unresolved.status = unresolved_status
                            self._mark_detail_dirty(unresolved)
                if error:
                    self.status = "Synthesis failed; using first-stage candidates"
                elif payload.get("cancelled"):
                    self.status = "Stopping synthesis agents"
                else:
                    self.status = "Synthesis complete"
            elif kind == "run_finished":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                run_root = payload.get("run_root")
                if isinstance(run_root, str) and run_root:
                    self.pending_run_root = Path(run_root)
                    self.pending_workspaces_root = self.pending_run_root / "workspaces"
                if payload.get("cancelled"):
                    self._finish_cancelled_runner()
                else:
                    fallback = payload.get("best_agent") if isinstance(payload.get("best_agent"), int) else None
                    self._recompute_recommendation(fallback)
                    if not self._launch_next_pending_stage():
                        self.status = self._completed_status()
                        self._notify_completion_if_background()
                        self._select_recommended_agent_after_completion()
                        self._schedule_follow_up_after_completion()
            elif kind == "run_failed":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                self.live_candidate_indices.clear()
                if self.pending_accept_agent is not None or (
                    self.queued_prompt and self.queued_agent is not None
                ):
                    self._finish_cancelled_runner()
                elif self.follow_up_queue and self._has_pending_run():
                    self._recompute_recommendation()
                    if self.recommended_agent is not None:
                        self._schedule_follow_up_after_completion()
                    else:
                        self.follow_up_ready = False
                        self.status = (
                            f"Run failed: {payload.get('message') or ''}; "
                            "queued follow-up is waiting for /retry or /more"
                        )
                else:
                    self.status = f"Run failed: {payload.get('message') or ''}"
                    if self._cleanup_after_pending_run():
                        self._clear_pending_run()
            elif kind == "candidate_batch_finished":
                self.running = False
                self.cancel_event = None
                self.active_batch_indices.clear()
                if payload.get("cancelled"):
                    self._finish_cancelled_runner()
                else:
                    batch_role = normalize_agent_role(payload.get("role"))
                    raw_batch_indices = payload.get("indices")
                    batch_indices = {
                        int(value)
                        for value in raw_batch_indices
                        if isinstance(value, int) and value > 0
                    } if isinstance(raw_batch_indices, list) else set()
                    batch_revision = payload.get("synthesis_revision")
                    if batch_role == AGENT_ROLE_SYNTHESIS:
                        if (
                            not isinstance(batch_revision, int)
                            or batch_revision != self.candidate_revision
                        ):
                            self._mark_synthesis_results_stale(batch_indices)
                            self.synthesis_refresh_needed = (
                                self._configured_synthesis_agents() > 0
                            )
                        else:
                            for value in batch_indices:
                                synthesis_pane = self.agents.get(value)
                                if synthesis_pane is not None:
                                    synthesis_pane.stale_synthesis = False
                    self._recompute_recommendation()
                    if not self._launch_next_pending_stage():
                        self.status = self._completed_status()
                        self._notify_completion_if_background()
                        self._select_recommended_agent_after_completion()
                        self._schedule_follow_up_after_completion()
            elif kind == "candidate_batch_failed":
                self.running = False
                self.cancel_event = None
                for active_idx in self.active_batch_indices:
                    active_pane = self.agents.get(active_idx)
                    if active_pane is not None and active_pane.result is None:
                        active_pane.status = "error"
                        self._mark_detail_dirty(active_pane)
                self.active_batch_indices.clear()
                if self.exit_after_run or self.pending_accept_agent is not None or (
                    self.queued_prompt and self.queued_agent is not None
                ):
                    self._finish_cancelled_runner()
                elif not self._launch_next_pending_stage():
                    self._recompute_recommendation()
                    self.status = f"Additional candidates failed: {payload.get('message') or ''}"
                    self._notify_completion_if_background()
                    self._select_recommended_agent_after_completion()
                    self._schedule_follow_up_after_completion()
            if kind not in {"agent_line", "agent_tokens", "agent_status"}:
                self._sync()
            if kind in {
                "run_finished",
                "run_failed",
                "candidate_batch_finished",
                "candidate_batch_failed",
            } and self.exit_after_run and not self._has_pending_run():
                self.exit()

        @on(AgentDiffLoaded)
        def _on_agent_diff_loaded(self, event: AgentDiffLoaded) -> None:
            pane = self.agents.get(event.idx)
            if pane is None or pane.diff_request != event.request_id:
                return
            pane.diff_loading = False
            pane.diff_text = event.text
            pane.diff_error = event.error
            self.status = (
                f"Cannot load AGENT-{event.idx:03d} diff: {event.error}"
                if event.error
                else f"Showing AGENT-{event.idx:03d} diff"
            )
            self._mark_detail_dirty(pane)
            self._sync()

        @on(ResumeHistoryLoaded)
        def _on_resume_history_loaded(self, event: ResumeHistoryLoaded) -> None:
            if event.request_id != self.resume_history_request:
                return
            if event.rejected:
                self.status = event.error or f"Cannot resume session: {event.session_id}"
                self._sync()
                return

            self.resume_session_id = event.session_id
            self.args.resume_session_id = event.session_id
            self._load_prompt_history_context()
            self._reset_conversation_detail()
            self._refresh_effort_for_context()
            self._coerce_synthesis_effort_for_model()
            self.run_info_rows = self._base_info_rows()
            if event.error:
                loaded_status = f"Resume history unavailable: {event.error}"
            else:
                self.detail_history = [self._history_detail_block(entry) for entry in event.entries]
                loaded_status = (
                    f"Resume session loaded: {event.session_id}"
                    if event.entries
                    else f"Resume session loaded without readable history: {event.session_id}"
                )
            if not self.running and not self._has_pending_run():
                self.status = loaded_status
            self._mark_detail_dirty()
            self._persist_workspace_settings()
            self._sync()

        @on(ResumeChoicesLoaded)
        def _on_resume_choices_loaded(self, event: ResumeChoicesLoaded) -> None:
            if self.resume_choices_inflight == (event.request_id, event.workspace):
                self.resume_choices_inflight = None
            if event.request_id != self.resume_choices_request or event.workspace != self.workspace:
                return
            if event.error:
                selector = self.pending_resume_selector
                self.pending_resume_selector = None
                self.resume_choices_loaded = False
                if selector is not None:
                    self.status = f"Cannot load resume sessions: {event.error}"
                    self._sync()
                return
            self.resume_choices_loaded = True
            self._apply_resume_choices(event.entries)
            selector = self.pending_resume_selector
            self.pending_resume_selector = None
            if selector is not None:
                self._handle_loaded_resume_selector(selector)

        def action_clear_view(self) -> None:
            if self.storage_preflight_inflight:
                self.status = "Cannot clear while checking storage"
                self._sync()
                return
            if self.running:
                self.status = "Cannot clear while a run is active"
                self._sync()
                return
            if self._has_pending_run():
                if self.recommended_agent is None:
                    message = "No agent succeeded successfully. Clear and discard all workspace copies?"
                    buttons = [
                        ("Discard and clear", "discard", "warning"),
                        ("Cancel", "cancel", "primary"),
                    ]
                    screen = ExitConfirmationScreen(message, buttons)
                    
                    def on_clear_confirmed(action: str | None) -> None:
                        if action == "discard":
                            if not self._discard_pending_run():
                                self._sync()
                                return
                            self._reset_conversation_detail()
                            self.run_info_rows = self._base_info_rows()
                            self.status = "Ready"
                            self._sync()
                    
                    self.push_screen(screen, on_clear_confirmed)
                    return
                else:
                    self.command_history.clear()
                    self._mark_detail_dirty()
                    self.status = "Cleared command view"
                    self._sync()
                    return
            for pane in self.agents.values():
                pane.status = "idle"
                pane.rejected = False
                pane.reasoning_tokens = None
                pane.reasoning_token_counts.clear()
                pane.input_text = ""
                pane.final_text = ""
                pane.result = None
                pane.clear_detail()
            self.recommended_agent = None
            self.resume_history_request += 1
            self.detail_history.clear()
            self.command_history.clear()
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Ready"
            self._sync()

        def copy_to_clipboard(self, text: str) -> None:
            if sys.platform == "darwin" and shutil.which("pbcopy") is not None:
                # Textual's fallback emits the entire payload as OSC 52. Large,
                # accumulated Detail histories can saturate the terminal writer
                # and make the UI appear to stop accepting mouse selection.
                self._clipboard = text
                try:
                    subprocess.run(
                        ["pbcopy"],
                        input=text,
                        text=True,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                    return
                except (OSError, subprocess.SubprocessError):
                    return
            super().copy_to_clipboard(text)

        def _copy_active_text(self) -> bool:
            if self._last_screen_selection:
                selected = self._last_screen_selection
                self._last_screen_selection = ""
                self.copy_to_clipboard(selected)
                return True
            screen_selection = self.screen.get_selected_text()
            if screen_selection:
                self._last_screen_selection = screen_selection
                self.copy_to_clipboard(screen_selection)
                return True
            selected_text = getattr(self.focused, "selected_text", "")
            if isinstance(selected_text, str) and selected_text:
                self.copy_to_clipboard(selected_text)
                return True
            if isinstance(self.focused, Input):
                if self.focused.value:
                    self.copy_to_clipboard(self.focused.value)
                    return True
            if isinstance(self.focused, Select):
                value = self.focused.value
                if isinstance(value, bool):
                    text = "YES" if value else "NO"
                elif value == "":
                    if self.focused.id in {
                        "config-model",
                        "config-synthesis-model",
                    }:
                        text = "default"
                    elif self.focused.id in {
                        "config-effort",
                        "config-synthesis-effort",
                    }:
                        if self.focused.id == "config-synthesis-effort":
                            text = self._synthesis_effort_display()
                        else:
                            text = self.model_registry.effort_display(
                                self._model_for_effort(),
                                None,
                            )
                    else:
                        text = "NO"
                else:
                    text = str(value)
                self.copy_to_clipboard(text)
                return True
            return False

        def action_copy_selection(self) -> None:
            self._copy_active_text()

        async def action_quit(self) -> None:
            # Textual's priority Ctrl-Q binding calls this action. Keep it on
            # PCR's cancellation/finalization path instead of exiting directly.
            self._request_exit()

        def action_interrupt_or_exit(self) -> None:
            if self._copy_active_text():
                return
            prompt = self.query_one("#prompt", PromptEditor)
            if prompt.text.strip():
                self._clear_prompt_draft()
            else:
                self._request_exit()

        def _request_exit(self) -> None:
            self._persist_workspace_settings()
            if self.storage_preflight_inflight:
                self.storage_preflight_request += 1
                self.storage_preflight_inflight = False
            self._clear_follow_up_queue()
            if self.running:
                self.queued_prompt = ""
                self.queued_agent = None
                self.queued_prompt_records_history = False
                self.queued_prompt_recorded_history_context = None
                self.pending_accept_agent = None
                self._discard_queued_candidate_batches()
                self.exit_after_run = True
                if self.cancel_event is not None:
                    self.cancel_event.set()
                self.status = "Stopping and cleaning up"
                self._sync()
                return
            
            target_idx, needs_confirm = self._determine_exit_target()
            if not needs_confirm:
                if target_idx is not None:
                    if not self._finalize_agent(target_idx, require_resume=False):
                        return
                else:
                    if not self._finalize_displayed_pending(require_resume=False):
                        return
                self._persist_workspace_settings()
                self.exit()
                return
            
            if target_idx is None:
                message = "All successful agents have been rejected. Exit without syncing any results?"
                buttons = [
                    ("Discard and exit", "discard", "warning"),
                    ("Cancel", "cancel", "primary"),
                ]
            else:
                message = (
                    f"The displayed agent is not available for sync.\n"
                    f"Finalize AGENT-{target_idx:03d} instead?"
                )
                buttons = [
                    (f"Finalize AGENT-{target_idx:03d} and exit", f"finalize-{target_idx}", "warning"),
                    ("Discard and exit", "discard", "default"),
                    ("Cancel", "cancel", "primary"),
                ]
            
            screen = ExitConfirmationScreen(message, buttons)
            
            def on_exit_confirmed(action: str | None) -> None:
                if action is None or action == "cancel":
                    return
                if action == "discard":
                    self._discard_pending_run()
                    self._persist_workspace_settings()
                    self.exit()
                    return
                if action.startswith("finalize-"):
                    idx = int(action.removeprefix("finalize-"))
                    if self._finalize_agent(idx, require_resume=False):
                        self._persist_workspace_settings()
                        self.exit()
            
            self.push_screen(screen, on_exit_confirmed)


        def _pending_recommend_by(self) -> str:
            if self.pending_execution_args is not None:
                return str(
                    getattr(
                        self.pending_execution_args,
                        "recommend_by",
                        "reasoning_tokens",
                    )
                )
            return str(
                getattr(self.args, "recommend_by", "reasoning_tokens")
            )

        def _recompute_recommendation(self, fallback: int | None = None) -> None:
            candidates: list[AgentResult] = []
            saw_success = False
            for pane in self.agents.values():
                result = pane.result
                if not isinstance(result, dict) or result.get("status") != "success":
                    continue
                saw_success = True
                if pane.rejected or pane.stale_synthesis:
                    continue
                try:
                    candidates.append(AgentResult(**result))
                except (TypeError, ValueError):
                    continue
            recommendation = select_best_result(
                candidates,
                self._pending_recommend_by(),
                warn_missing_tokens=False,
            )
            if recommendation is not None:
                self.recommended_agent = recommendation.idx
            elif saw_success:
                self.recommended_agent = None
            elif fallback is not None:
                pane = self.agents.get(fallback)
                self.recommended_agent = (
                    None
                    if pane is not None
                    and (pane.rejected or pane.stale_synthesis)
                    else fallback
                )
            else:
                self.recommended_agent = None

        def _completed_status(self) -> str:
            if self.recommended_agent is not None:
                return f"Done: agent_{self.recommended_agent:03d}"
            if any(
                isinstance(pane.result, dict) and pane.result.get("status") == "success"
                for pane in self.agents.values()
            ):
                return "Done: all successful agents are rejected"
            return "No successful agent"

        def _select_recommended_agent_after_completion(self) -> bool:
            idx = self.recommended_agent
            if idx is None:
                return False
            pane = self.agents.get(idx)
            if (
                pane is None
                or pane.rejected
                or pane.stale_synthesis
                or self._successful_agent_result(idx) is None
            ):
                return False
            self.selected_agent = idx
            return True

        def _notify_completion_if_background(self) -> None:
            if self.completion_notification_sent:
                return
            self.completion_notification_sent = True
            if self.app_in_foreground:
                return
            workspace = self._pending_workspace_path()
            workspace_name = workspace.name or str(workspace)
            with contextlib.suppress(Exception):
                self.notify(
                    workspace_name,
                    title="parallel-codex-runner",
                    timeout=5,
                    markup=False,
                )
            with contextlib.suppress(Exception):
                self.bell()

        def _can_extend_current_run(self) -> bool:
            return bool(
                self.pending_prompt
                and self.pending_execution_args is not None
                and (self.running or self._has_pending_run())
            )

        def _configured_synthesis_agents(self) -> int:
            args = self.pending_execution_args or self.args
            return max(0, int(getattr(args, "synthesis_agents", 0) or 0))

        def _mark_synthesis_results_stale(
            self,
            indices: set[int] | None = None,
        ) -> None:
            changed = False
            for idx, pane in self.agents.items():
                if (
                    pane.role != AGENT_ROLE_SYNTHESIS
                    or (indices is not None and idx not in indices)
                ):
                    continue
                if not pane.stale_synthesis:
                    pane.stale_synthesis = True
                    changed = True
                    self._mark_detail_dirty(pane)
            if changed:
                self._recompute_recommendation()

        def _record_candidate_change(self, *, live: bool) -> None:
            self.candidate_revision += 1
            if live or self._configured_synthesis_agents() <= 0:
                return
            self.synthesis_refresh_needed = True
            self._mark_synthesis_results_stale()
            self._cancel_stale_synthesis_agents()

        def _cancel_stale_synthesis_agents(self) -> None:
            for idx in self.active_batch_indices:
                pane = self.agents.get(idx)
                if pane is None or pane.role != AGENT_ROLE_SYNTHESIS:
                    continue
                cancel_event = self.agent_cancel_events.get(idx)
                if cancel_event is not None:
                    cancel_agent_including_queued(cancel_event)

        def _successful_candidate_results(self) -> list[AgentResult]:
            results: list[AgentResult] = []
            for pane in self.agents.values():
                if pane.role != AGENT_ROLE_CANDIDATE:
                    continue
                result = pane.result
                if not isinstance(result, dict) or result.get("status") != "success":
                    continue
                try:
                    results.append(AgentResult(**result))
                except (TypeError, ValueError):
                    continue
            return sorted(results, key=lambda item: item.idx)

        def _configured_synthesis_indices(self) -> list[int]:
            args = self.pending_execution_args or self.args
            candidate = max(
                0,
                int(getattr(args, "num_agents", self.num_agents) or 0),
            ) + 1
            count = self._configured_synthesis_agents()
            existing = sorted(
                idx
                for idx, pane in self.agents.items()
                if pane.role == AGENT_ROLE_SYNTHESIS
            )
            indices = existing[:count]
            occupied = {
                idx
                for idx, pane in self.agents.items()
                if pane.role == AGENT_ROLE_CANDIDATE
            }
            while len(indices) < count:
                if candidate not in occupied and candidate not in indices:
                    indices.append(candidate)
                candidate += 1
            return indices

        def _discard_queued_synthesis_batches(self) -> None:
            retained: list[CandidateBatch] = []
            for batch in self.candidate_batches:
                if normalize_agent_role(batch.role) != AGENT_ROLE_SYNTHESIS:
                    retained.append(batch)
                    continue
                for idx in batch.indices:
                    pane = self.agents.get(idx)
                    if pane is None or not isinstance(pane.result, dict):
                        continue
                    pane.status = str(pane.result.get("status") or "finished")
                    pane.stale_synthesis = True
                    self._mark_detail_dirty(pane)
            self.candidate_batches = retained

        def _launch_synthesis_refresh(self) -> bool:
            count = self._configured_synthesis_agents()
            if (
                self.running
                or not self.synthesis_refresh_needed
                or count <= 0
                or self.pending_run_root is None
                or not self.pending_prompt
                or any(
                    normalize_agent_role(batch.role) == AGENT_ROLE_CANDIDATE
                    for batch in self.candidate_batches
                )
            ):
                return False

            source_results = self._successful_candidate_results()
            if not source_results:
                self.synthesis_refresh_needed = False
                return False
            try:
                _context_path, instructions = create_synthesis_context(
                    self.pending_run_root,
                    self.pending_prompt,
                    source_results,
                )
            except Exception as exc:  # noqa: BLE001
                self.synthesis_refresh_needed = False
                self.status = f"Cannot refresh synthesis: {exc}"
                return False

            self._discard_queued_synthesis_batches()
            indices = self._configured_synthesis_indices()
            revision = self.candidate_revision
            retries: set[int] = set()
            for idx in indices:
                pane = self.agents.get(idx)
                if pane is None:
                    pane = AgentPane(idx=idx)
                    self.agents[idx] = pane
                else:
                    retries.add(idx)
                pane.role = AGENT_ROLE_SYNTHESIS
                pane.status = "queued"
                pane.input_text = self.pending_prompt
                pane.execution_prompt = self.pending_prompt
                pane.developer_instructions = instructions
                self.agent_cancel_events[idx] = threading.Event()
            self.synthesis_refresh_needed = False
            self.candidate_batches.append(
                CandidateBatch(
                    indices=indices,
                    retry_indices=retries,
                    role=AGENT_ROLE_SYNTHESIS,
                    prompt=self.pending_prompt,
                    developer_instructions=instructions,
                    synthesis_revision=revision,
                    synthesis_source_indices=[
                        result.idx for result in source_results
                    ],
                )
            )
            self.completion_notification_sent = False
            return self._launch_next_candidate_batch()

        def _launch_next_pending_stage(self) -> bool:
            if self.running:
                return False
            for batch_index, batch in enumerate(self.candidate_batches):
                if normalize_agent_role(batch.role) == AGENT_ROLE_CANDIDATE:
                    return self._launch_next_candidate_batch(batch_index)
            if self._launch_synthesis_refresh():
                return True
            return self._launch_next_candidate_batch()

        def _agent_has_pending_batch(self, idx: int) -> bool:
            return (
                idx in self.active_batch_indices
                or idx in self.live_candidate_indices
                or any(
                    idx in batch.indices for batch in self.candidate_batches
                )
            )

        def _active_agent_count(self) -> int:
            return sum(
                pane.status in {"copying", "running", "stopping"}
                for pane in self.agents.values()
            )

        def _current_parallel_limit(self) -> int:
            args = self.pending_execution_args or self.args
            if bool(getattr(args, "serial", False)):
                return 1
            configured = getattr(args, "max_parallel", None)
            if configured is not None:
                return max(1, int(configured))
            return max(1, int(getattr(args, "num_agents", self.num_agents)))

        def _discard_queued_candidate_batches(self) -> None:
            for batch in self.candidate_batches:
                for idx in batch.indices:
                    if idx in batch.retry_indices:
                        pane = self.agents.get(idx)
                        if pane is not None and isinstance(pane.result, dict):
                            pane.status = str(pane.result.get("status") or "finished")
                            self._mark_detail_dirty(pane)
                    else:
                        self.agents.pop(idx, None)
                        self.agent_cancel_events.pop(idx, None)
            self.candidate_batches.clear()
            if self.selected_agent not in self.agents and self.agents:
                self.selected_agent = min(
                    self.agents,
                    key=lambda idx: (abs(idx - self.selected_agent), idx),
                )

        def _prepare_retry_pane(self, pane: AgentPane) -> None:
            pane.attempt_history.extend(self._current_attempt_blocks(pane))
            pane.attempt_history.append(("↻", "Retry", "yellow"))
            pane.status = "queued"
            pane.rejected = False
            pane.stale_synthesis = False
            pane.reasoning_tokens = None
            pane.reasoning_token_counts.clear()
            pane.input_text = self.pending_prompt
            pane.final_text = ""
            pane.result = None
            pane.detail_events.clear()
            pane.lines.clear()
            pane.thought_lines.clear()
            pane.output_lines.clear()
            pane.show_diff = False
            pane.diff_loading = False
            pane.diff_text = ""
            pane.diff_error = ""
            pane.diff_request += 1
            self._mark_detail_dirty(pane)

        def _launch_next_candidate_batch(self, batch_index: int = 0) -> bool:
            if self.running or not self.candidate_batches:
                return False
            if (
                self.pending_execution_args is None
                or self.pending_run_root is None
                or not self.pending_prompt
            ):
                self.status = "Cannot start additional candidates: run metadata is unavailable"
                self._discard_queued_candidate_batches()
                self._sync()
                return False

            batch = self.candidate_batches.pop(batch_index)
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            batch.role = normalize_agent_role(batch.role)
            execution_prompt = batch.prompt or self.pending_prompt
            for idx in batch.indices:
                pane = self.agents[idx]
                pane.role = batch.role
                pane.execution_prompt = execution_prompt
                pane.developer_instructions = batch.developer_instructions
                if idx in batch.retry_indices:
                    self._prepare_retry_pane(pane)
                else:
                    pane.status = "queued"
                    pane.result = None
                    self._mark_detail_dirty(pane)
                self.agent_cancel_events[idx] = threading.Event()

            run_args = argparse.Namespace(**vars(self.pending_execution_args))
            run_args.num_agents = len(batch.indices)
            if run_args.max_parallel is not None:
                run_args.max_parallel = min(run_args.max_parallel, len(batch.indices))
            cancel_event = threading.Event()
            run_args.cancel_event = cancel_event
            run_args.agent_cancel_events = {
                idx: self.agent_cancel_events[idx] for idx in batch.indices
            }
            self.cancel_event = cancel_event
            self.active_batch_indices = set(batch.indices)
            self.running = True
            self.started_at = time.monotonic()
            if batch.role == AGENT_ROLE_SYNTHESIS:
                self.status = (
                    f"Running {len(batch.indices)} refreshed synthesis agents"
                )
            elif batch.retry_indices and len(batch.indices) == 1:
                self.status = f"Retrying AGENT-{batch.indices[0]:03d}"
            else:
                self.status = f"Running {len(batch.indices)} additional candidates"
            self._sync()
            recommendation_excluded_indices = {
                idx
                for idx, pane in self.agents.items()
                if pane.rejected or pane.stale_synthesis
            }
            run_root = self.pending_run_root
            workspace_path = self._pending_workspace_path()
            original_prompt = self.pending_prompt
            recommend_by = self._pending_recommend_by()

            def target() -> None:
                previous_disable = logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        run_additional_agents(
                            args=run_args,
                            prompt=execution_prompt,
                            agent_indices=batch.indices,
                            run_root=run_root,
                            workspace=workspace_path,
                            resume_session_id=getattr(
                                run_args,
                                "resume_session_id",
                                None,
                            ),
                            retry_indices=batch.retry_indices,
                            progress_callback=self._post_progress,
                            cancel_event=cancel_event,
                            agent_cancel_events=run_args.agent_cancel_events,
                            agent_role=batch.role,
                            developer_instructions=(
                                batch.developer_instructions or None
                            ),
                            recommendation_excluded_indices=(
                                recommendation_excluded_indices
                            ),
                            synthesis_source_agents=(
                                batch.synthesis_source_indices or None
                            ),
                        )
                    self._post_progress(
                        {
                            "type": "candidate_batch_finished",
                            "cancelled": cancel_event.is_set(),
                            "indices": list(batch.indices),
                            "role": batch.role,
                            "synthesis_revision": batch.synthesis_revision,
                        }
                    )
                except BaseException as exc:  # noqa: BLE001
                    if (
                        batch.role == AGENT_ROLE_SYNTHESIS
                        and batch.synthesis_source_indices
                    ):
                        with contextlib.suppress(Exception):
                            refresh_run_result_files(
                                run_root=run_root,
                                workspace=workspace_path,
                                prompt=original_prompt,
                                new_results=[],
                                recommend_by=recommend_by,
                                recommendation_excluded_indices=(
                                    recommendation_excluded_indices
                                    | set(batch.indices)
                                ),
                                synthesis_source_agents=(
                                    batch.synthesis_source_indices
                                ),
                                synthesis_status="failed",
                                synthesis_error=str(exc),
                                remove_result_indices=set(batch.indices),
                            )
                    self._post_progress(
                        {
                            "type": "candidate_batch_failed",
                            "message": str(exc),
                            "indices": list(batch.indices),
                            "role": batch.role,
                            "synthesis_revision": batch.synthesis_revision,
                        }
                    )
                finally:
                    logging.disable(previous_disable)

            self.runner_thread = threading.Thread(
                target=target,
                name="pcr-tui-candidates",
                daemon=False,
            )
            self.runner_thread.start()
            return True

        def _finish_cancelled_runner(self) -> None:
            if self.pending_accept_agent is not None:
                idx = self.pending_accept_agent
                self.pending_accept_agent = None
                if self._finalize_agent(idx, archive_detail=True):
                    if self.follow_up_queue and not self.exit_after_run:
                        self._rebind_follow_up_queue()
                        self.follow_up_source_finalized = True
                        self._dispatch_follow_up()
                    else:
                        self.status = f"Accepted AGENT-{idx:03d}"
                return
            if self.queued_prompt and self.queued_agent is not None:
                self._continue_queued_prompt()
                return
            self.status = "Cancelled"
            if self._cleanup_after_pending_run():
                self._clear_pending_run()

        def _start_run(
            self,
            prompt: str,
            record_history: bool = False,
            *,
            recorded_history_context: tuple[str, str] | None = None,
        ) -> bool:
            if self.running:
                return self._handle_prompt_while_running(
                    prompt,
                    record_history=record_history,
                    recorded_history_context=recorded_history_context,
                )
            if not self._commit_runner_inputs():
                self._sync()
                return False
            if self._effort_model_is_known():
                try:
                    effective_effort = self.model_registry.resolve_effort(
                        self._model_for_effort(),
                        getattr(self.args, "effort", None),
                    )
                except ValueError as exc:
                    self.status = str(exc)
                    self._sync()
                    return False
            else:
                effective_effort = getattr(self.args, "effort", None)
            if self._has_pending_run() and (
                self._pending_sync_disabled()
                or (
                    self.recommended_agent is None
                    and not any(
                        isinstance(pane.result, dict)
                        and pane.result.get("status") == "success"
                        for pane in self.agents.values()
                    )
                )
            ):
                if not self._discard_pending_run():
                    self._sync()
                    return False
            if self._has_pending_run() and not self._finalize_agent(
                self.selected_agent,
                archive_detail=True,
            ):
                self._sync()
                return False
            self._archive_command_history()
            self.running = True
            self.exit_after_run = False
            self.pending_accept_agent = None
            self.candidate_batches.clear()
            self.active_batch_indices.clear()
            self.live_candidate_indices.clear()
            self.live_candidate_coordinator = LiveCandidateCoordinator()
            self.live_candidate_coordinator.activate()
            self.live_synthesis_config_coordinator = (
                LiveSynthesisConfigCoordinator.from_args(self.args)
            )
            self.candidate_revision = 0
            self.synthesis_refresh_needed = False
            self.core_synthesis_revision = None
            self.cancel_event = threading.Event()
            self.recommended_agent = None
            self.started_at = time.monotonic()
            self.completion_notification_sent = False
            self.pending_workspace = self.workspace
            self.pending_no_sync_back = bool(getattr(self.args, "no_sync_back", False))
            self.pending_keep_workspaces = bool(getattr(self.args, "keep_workspaces", False))
            self.pending_prompt = prompt
            self.pending_prompt_records_history = record_history
            self.agents = {idx: AgentPane(idx) for idx in range(1, self.num_agents + 1)}
            self.agent_cancel_events = {
                idx: threading.Event()
                for idx in range(
                    1,
                    self.num_agents + self.synthesis_agents + 1,
                )
            }
            for pane in self.agents.values():
                pane.input_text = prompt
                pane.execution_prompt = prompt
            self.selected_agent = min(self.selected_agent, self.num_agents)
            self._mark_detail_dirty()
            self.run_info_rows = self._base_info_rows()
            self.status = "Preparing agents"
            self._sync()

            run_args = argparse.Namespace(**vars(self.args))
            run_args.prompt = prompt
            run_args.prompt_file = None
            run_args.num_agents = self.num_agents
            run_args.synthesis_agents = self.synthesis_agents
            run_args.max_parallel = getattr(self.args, "max_parallel", None)
            run_args.resume = False
            run_args.resume_session_id = self.resume_session_id or None
            run_args.effort = effective_effort
            # The TUI owns selection, sync-back, and cleanup after run_once returns.
            run_args.workspace_mode = execution_workspace_mode(
                self.num_agents, self.synthesis_agents,
                not self.pending_no_sync_back, self.pending_keep_workspaces,
            )
            run_args.no_sync_back = True
            run_args.keep_workspaces = True
            run_args.cancel_event = self.cancel_event
            run_args.agent_cancel_events = self.agent_cancel_events
            run_args.live_candidate_coordinator = self.live_candidate_coordinator
            run_args.live_synthesis_config_coordinator = (
                self.live_synthesis_config_coordinator
            )
            self.pending_execution_args = argparse.Namespace(**vars(run_args))
            # Keep the configured limit, not the first batch's effective limit.
            # This preserves max-parallel=auto and larger explicit limits for /more.
            self.pending_execution_args.max_parallel = getattr(self.args, "max_parallel", None)

            def target() -> None:
                previous_disable = logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL)
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        run_once(run_args, prompt, progress_callback=self._post_progress, print_output=False)
                except BaseException as exc:  # noqa: BLE001
                    self._post_progress({"type": "run_failed", "message": str(exc)})
                finally:
                    logging.disable(previous_disable)

            self.runner_thread = threading.Thread(target=target, name="pcr-tui-runner", daemon=False)
            self.runner_thread.start()
            if (
                record_history
                and recorded_history_context
                != self._current_prompt_history_context()
            ):
                self._record_started_prompt(prompt)
            return True

        def _successful_agent_result(self, idx: int) -> dict[str, Any] | None:
            pane = self.agents.get(idx)
            result = pane.result if pane is not None else None
            if isinstance(result, dict) and result.get("status") == "success":
                return result
            return None

        def _enqueue_follow_up(
            self,
            prompt: str,
            record_history: bool = False,
            *,
            recorded_history_context: tuple[str, str] | None = None,
        ) -> bool:
            prompt = normalize_prompt_text(prompt).strip()
            if not prompt:
                return False
            if self._pending_sync_disabled():
                self.status = "Cannot queue a follow-up while sync back is disabled"
                self._sync()
                return False
            if record_history and recorded_history_context is None:
                recorded_history_context = self._record_prompt_history(prompt)
            self.follow_up_queue.append(
                QueuedFollowUp(
                    prompt,
                    record_history=record_history,
                    context=self._follow_up_context(),
                    recorded_history_context=recorded_history_context,
                )
            )
            if self.running:
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                suffix = "current agents continue"
            else:
                suffix = "the first queued follow-up keeps its current schedule"
            self.status = f"Queued follow-up #{len(self.follow_up_queue)}; {suffix}"
            self._sync()
            return True

        def _handle_prompt_while_running(
            self,
            prompt: str,
            record_history: bool = False,
            *,
            recorded_history_context: tuple[str, str] | None = None,
        ) -> bool:
            if self._pending_sync_disabled():
                self.status = "Cannot continue a running agent while sync back is disabled"
                self._sync()
                return False
            if (
                self.follow_up_queue
                or self.queued_prompt
                or self.pending_accept_agent is not None
            ):
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                    recorded_history_context=recorded_history_context,
                )
            if self._successful_agent_result(self.selected_agent) is None:
                return self._enqueue_follow_up(
                    prompt,
                    record_history=record_history,
                    recorded_history_context=recorded_history_context,
                )
            if record_history and recorded_history_context is None:
                recorded_history_context = self._record_prompt_history(prompt)
            self.queued_prompt = prompt
            self.queued_agent = self.selected_agent
            self.queued_prompt_records_history = record_history
            self.queued_prompt_recorded_history_context = recorded_history_context
            self.pending_accept_agent = None
            self._discard_queued_candidate_batches()
            if self.cancel_event is not None:
                self.cancel_event.set()
            self.status = f"Stopping remaining agents; continuing from AGENT-{self.selected_agent:03d}"
            self._sync()
            return True

        def _schedule_follow_up_after_completion(self) -> bool:
            if (
                not self.follow_up_queue
                or self.running
                or self.exit_after_run
                or self.storage_preflight_inflight
            ):
                return False
            idx = self.recommended_agent
            if idx is None or self._successful_agent_result(idx) is None:
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                self.status = (
                    "Queued follow-up is waiting for a successful recommended Agent"
                )
                return False
            self.selected_agent = idx
            if self.follow_up_queue_editor_open:
                self.follow_up_queue_editor_resume_after = True
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                self.status = "Completed Agents are waiting for queue edits"
                return True
            self._start_follow_up_countdown()
            return True

        def _follow_up_delay_seconds(self) -> int:
            raw = getattr(
                self.args,
                "follow_up_delay",
                DEFAULT_FOLLOW_UP_DELAY_SECONDS,
            )
            if isinstance(raw, bool):
                return DEFAULT_FOLLOW_UP_DELAY_SECONDS
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return DEFAULT_FOLLOW_UP_DELAY_SECONDS
            return max(0, value)

        def _start_follow_up_countdown(self) -> None:
            delay = self._follow_up_delay_seconds()
            if delay == 0:
                self.follow_up_continue_at = None
                self.follow_up_ready = True
                self._follow_up_countdown_second = None
                self.status = (
                    f"Queued follow-up is ready from AGENT-{self.selected_agent:03d}"
                )
                return
            self.follow_up_continue_at = time.monotonic() + delay
            self.follow_up_ready = False
            self._follow_up_countdown_second = delay
            self._set_follow_up_countdown_status(delay)

        def _set_follow_up_countdown_status(self, remaining: int) -> None:
            self.status = (
                f"Queued follow-up starts from AGENT-{self.selected_agent:03d} "
                f"in {max(0, remaining)}s; ←/→ changes the Agent"
            )

        def _refresh_follow_up_countdown(self, remaining: int) -> None:
            self._set_follow_up_countdown_status(remaining)
            if self._selection_drag_active() or self._screen_selection_active():
                self._follow_up_queue_refresh_deferred = True
                self._sync_deferred_for_selection = True
                return
            self._refresh_follow_up_queue()
            try:
                self.query_one("#runner-conversation", Static).update(self.status)
                self.query_one("#state", Static).update(self._state_text())
            except Exception:
                return

        def _consume_follow_up(self, prompt: str) -> None:
            if self.follow_up_queue:
                if self.follow_up_queue[0].prompt == prompt:
                    self.follow_up_queue.pop(0)
                else:
                    for index, item in enumerate(self.follow_up_queue):
                        if item.prompt == prompt:
                            self.follow_up_queue.pop(index)
                            break
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            if self.running or not self.follow_up_queue:
                self.follow_up_source_finalized = False
            elif self.follow_up_source_finalized:
                self._start_follow_up_countdown()
            self._refresh_follow_up_queue()

        def _dispatch_follow_up(self) -> bool:
            if self.follow_up_queue_editor_open:
                self.follow_up_queue_editor_resume_after = True
                self.follow_up_continue_at = None
                self.follow_up_ready = False
                self._follow_up_countdown_second = None
                self.status = "Queued follow-up is waiting for queue edits"
                self._sync()
                return False
            if (
                not self.follow_up_queue
                or self.running
                or self.storage_preflight_inflight
            ):
                return False
            item = self.follow_up_queue[0]
            if item.context is not None and item.context != self._follow_up_context():
                self._clear_follow_up_queue()
                self.status = (
                    "Queued follow-up discarded because its workspace or resume "
                    "session changed"
                )
                self._sync()
                return False
            self.follow_up_continue_at = None
            self.follow_up_ready = False
            self._follow_up_countdown_second = None
            if not self.follow_up_source_finalized:
                if self._successful_agent_result(self.selected_agent) is None:
                    self.follow_up_ready = True
                    self.status = (
                        f"Queued follow-up is waiting: AGENT-{self.selected_agent:03d} "
                        "is not successful"
                    )
                    self._sync()
                    return False
                if self._pending_sync_disabled():
                    self.follow_up_ready = True
                    self.status = (
                        "Queued follow-up is waiting because sync back is disabled"
                    )
                    self._sync()
                    return False
                if not self._finalize_agent(
                    self.selected_agent,
                    archive_detail=True,
                ):
                    self._sync()
                    return False
                self._rebind_follow_up_queue()
                self.follow_up_source_finalized = True

            self._archive_command_history()
            started = self._request_run_with_storage_check(
                item.prompt,
                record_history=item.record_history,
                from_follow_up_queue=True,
                **_recorded_history_context_kwargs(
                    item.recorded_history_context
                ),
            )
            if not started:
                self._sync()
            return started

        def _continue_queued_prompt(self) -> None:
            prompt = self.queued_prompt
            agent_idx = self.queued_agent
            record_history = self.queued_prompt_records_history
            recorded_history_context = (
                self.queued_prompt_recorded_history_context
            )
            self.queued_prompt = ""
            self.queued_agent = None
            self.queued_prompt_records_history = False
            self.queued_prompt_recorded_history_context = None
            if not prompt or agent_idx is None:
                return
            if not self._finalize_agent(agent_idx, archive_detail=True):
                try:
                    editor = self.query_one("#prompt", PromptEditor)
                    editor.text = prompt
                    self._sync_prompt_height()
                    editor.focus()
                except Exception:
                    pass
                return
            self._rebind_follow_up_queue()
            self._archive_command_history()
            history_kwargs = _recorded_history_context_kwargs(
                recorded_history_context
            )
            if self.follow_up_queue:
                started = self._request_run_with_storage_check(
                    prompt,
                    record_history=record_history,
                    bypass_follow_up_queue=True,
                    **history_kwargs,
                )
            elif record_history:
                started = self._request_run_with_storage_check(
                    prompt,
                    record_history=True,
                    **history_kwargs,
                )
            else:
                started = self._request_run_with_storage_check(prompt)
            if not started:
                try:
                    editor = self.query_one("#prompt", PromptEditor)
                    editor.text = prompt
                    self._sync_prompt_height()
                    editor.focus()
                except Exception:
                    pass

        def _post_progress(self, payload: dict[str, Any]) -> None:
            if payload.get("type") == "run_prepared":
                rows = payload.get("rows")
                if isinstance(rows, list):
                    self._remember_run_paths(
                        [(str(key), str(value)) for key, value in rows if isinstance(key, str)]
                    )
            try:
                self.call_from_thread(self.post_message, RunnerEvent(payload))
            except RuntimeError:
                pass

        def _remember_run_paths(self, rows: list[tuple[str, str]]) -> None:
            data = dict(rows)
            if data.get("RUNS_ROOT"):
                self.pending_run_root = Path(data["RUNS_ROOT"])
            if data.get("WORKSPACE COPIES"):
                self.pending_workspaces_root = Path(data["WORKSPACE COPIES"])

        def _has_pending_run(self) -> bool:
            return self.pending_workspaces_root is not None

        def _clear_pending_run(self) -> None:
            self._discard_queued_candidate_batches()
            self.pending_run_root = None
            self.pending_workspaces_root = None
            self.pending_workspace = None
            self.pending_no_sync_back = None
            self.pending_keep_workspaces = None
            self.pending_prompt = ""
            self.pending_prompt_records_history = False
            self.pending_execution_args = None
            self.active_batch_indices.clear()
            self.live_candidate_indices.clear()
            self.live_candidate_coordinator = None
            self.live_synthesis_config_coordinator = None
            self.candidate_revision = 0
            self.synthesis_refresh_needed = False
            self.core_synthesis_revision = None
            self.pending_accept_agent = None

        def _pending_sync_disabled(self) -> bool:
            if self.pending_no_sync_back is not None:
                return self.pending_no_sync_back
            return bool(getattr(self.args, "no_sync_back", False))

        def _pending_keep_enabled(self) -> bool:
            if self.pending_keep_workspaces is not None:
                return self.pending_keep_workspaces
            return bool(getattr(self.args, "keep_workspaces", False))

        def _pending_workspace_path(self) -> Path:
            return self.pending_workspace or self.workspace

        def _discard_pending_run(self) -> bool:
            if not self._cleanup_after_pending_run():
                return False
            self._clear_pending_run()
            self.recommended_agent = None
            self.run_info_rows = self._base_info_rows()
            return True

        def _cleanup_after_pending_run(self) -> bool:
            if self._pending_keep_enabled():
                return True
            root = self.pending_workspaces_root
            if root is None:
                return True
            workspace = self._pending_workspace_path()
            try:
                if is_relative_to(root, workspace):
                    raise RuntimeError(f"refusing to delete workspace-owned path: {root}")
                cleanup_workspace_copies(workspace, root)
                if self.pending_run_root is not None:
                    remove_agent_codex_homes(self.pending_run_root / "meta")
                return True
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cleanup failed ({root}): {exc}"
                return False

        def _shutdown_runner_and_cleanup(self) -> None:
            """Stop an active runner and remove copies after the UI loop exits."""
            if self._shutdown_cleanup_started:
                return
            self._shutdown_cleanup_started = True
            self.storage_preflight_request += 1
            self.storage_preflight_inflight = False

            if self.cancel_event is not None:
                self.cancel_event.set()
            if self.runner_thread is not None and self.runner_thread.is_alive():
                self.runner_thread.join()
            self.running = False

            if self._has_pending_run():
                if not self._cleanup_after_pending_run():
                    import logging
                    logging.getLogger(__name__).warning(
                        "cleanup failed during shutdown: %s", self.status
                    )
                self._clear_pending_run()

        def _finalize_agent(self, idx: int, require_resume: bool = True, archive_detail: bool = False) -> bool:
            pane = self.agents.get(idx)
            result_data = pane.result if pane is not None else None
            if not result_data:
                self.status = f"AGENT-{idx:03d} has no finished result"
                return False
            result = AgentResult(**result_data)
            if result.status != "success":
                self.status = f"AGENT-{idx:03d} is not successful"
                return False
            workspace = self._pending_workspace_path()
            try:
                if require_resume and not result.codex_thread_id:
                    source_codex_home = Path(result.codex_home).expanduser().resolve() if result.codex_home else get_codex_home()
                    result.codex_thread_id = infer_codex_thread_id_for_result(result, source_codex_home)
                    if not result.codex_thread_id:
                        self.status = f"AGENT-{idx:03d} has no resumable session"
                        return False
                # Keep the display path spelling (notably /var vs /private/var on
                # macOS) so archived Detail links can be rewritten exactly.
                candidate_workspace = Path(result.workspace_dir).expanduser().absolute()
                if not candidate_workspace.is_dir():
                    self.status = f"AGENT-{idx:03d} workspace is unavailable"
                    return False
                workspaces_root = self.pending_workspaces_root
                if (
                    not self._pending_keep_enabled()
                    and workspaces_root is not None
                    and is_relative_to(workspaces_root, workspace)
                ):
                    raise RuntimeError(
                        f"refusing to delete workspace-owned path: {workspaces_root}"
                    )

                def after_sync() -> None:
                    if pane is not None:
                        self._rewrite_finalized_pane_paths(
                            pane,
                            candidate_workspace,
                            workspace,
                        )

                cleanup_codex_homes = None
                if self.pending_run_root is not None:
                    cleanup_codex_homes = lambda: remove_agent_codex_homes(
                        self.pending_run_root / "meta"
                    )
                outcome = finalize_candidate_workspace(
                    result,
                    candidate_workspace=candidate_workspace,
                    workspace=workspace,
                    workspaces_root=workspaces_root,
                    promote_session=promote_best_codex_session_to_workspace,
                    keep_workspaces=self._pending_keep_enabled(),
                    require_resume=require_resume,
                    sync_workspace=sync_best_workspace_back,
                    cleanup_workspaces=cleanup_workspace_copies,
                    cleanup_codex_homes=cleanup_codex_homes,
                    after_sync=after_sync,
                )
                promotion_error = str(
                    (outcome.session_promotion or {}).get("error") or ""
                ).strip()
                if result.codex_thread_id:
                    result_data["codex_thread_id"] = result.codex_thread_id
                    next_context = self.prompt_history_store.context_key(
                        self.workspace,
                        result.codex_thread_id,
                    )
                    if (
                        self.pending_prompt_records_history
                        and next_context != self.prompt_history_context
                    ):
                        self._associate_pending_prompt_with_context(next_context)
                    if require_resume or (
                        outcome.session_promotion is not None and not promotion_error
                    ):
                        self.resume_session_id = result.codex_thread_id
                        self.args.resume_session_id = result.codex_thread_id
                        self._load_prompt_history_context()
                if archive_detail and pane is not None:
                    self._archive_agent_detail(pane)
                    pane.input_text = ""
                    pane.final_text = ""
                    pane.clear_detail()
                    self._mark_detail_dirty(pane)
                self._clear_pending_run()
                self.run_info_rows = self._base_info_rows()
                self._refresh_resume_control()
                self.status = f"Continuing from AGENT-{idx:03d}"
                return True
            except FinalizationStageError as exc:
                if exc.stage == "sync_preflight":
                    self.status = (
                        f"Cannot continue AGENT-{idx:03d}: sync preflight failed "
                        f"(workspace untouched): {exc.cause}"
                    )
                elif exc.stage == "sync":
                    self.status = (
                        f"Cannot continue AGENT-{idx:03d}: sync may be incomplete: {exc.cause}"
                    )
                else:
                    self.status = f"Cannot continue AGENT-{idx:03d}: {exc.cause}"
                return False
            except Exception as exc:  # noqa: BLE001
                self.status = f"Cannot continue AGENT-{idx:03d}: {exc}"
                return False

        def _rewrite_finalized_pane_paths(
            self,
            pane: AgentPane,
            source_workspace: Path,
            workspace: Path,
        ) -> None:
            mapping = WorkspacePathMapping.create(source_workspace, workspace)

            def visible(text: str) -> str:
                return rewrite_workspace_references(text, (mapping,))

            pane.final_text = visible(pane.final_text)
            pane.output_lines = [visible(text) for text in pane.output_lines]
            pane.detail_events = [
                (category, visible(text) if category == "output" else text)
                for category, text in pane.detail_events
            ]
            pane.attempt_history = [
                (prefix, visible(text) if style == "detail-output" else text, style)
                for prefix, text, style in pane.attempt_history
            ]
            self._mark_detail_dirty(pane)

        def _show_text(self, text: str) -> None:
            text = text.strip()
            if text:
                self.command_history.append(("•", text, "yellow"))
            self._mark_detail_dirty()
            self._sync()

        def _read_final_message(self, result: dict[str, Any]) -> str:
            path = result.get("final_message")
            if not isinstance(path, str) or not path:
                return ""
            try:
                text = Path(path).read_text(encoding="utf-8").strip()
            except OSError:
                return ""
            return compact_block(text)



    def run_textual_tui(args: argparse.Namespace) -> int:
        app = PcrTextualApp(args)
        try:
            app.run()
        finally:
            app._persist_workspace_settings()
            app._shutdown_runner_and_cleanup()
        return 0
