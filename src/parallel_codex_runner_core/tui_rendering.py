from __future__ import annotations

import re
import threading
import time
from typing import Any

from ._vendor import activate_textual
from .app import list_resume_sessions
from .codex_cli import format_fast_mode
from .models import AGENT_ROLE_SYNTHESIS, DEFAULT_SUBAGENTS_LIMIT
from .paths import absolute_path_for_display, choose_run_base
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
    MAX_SUGGESTIONS,
    RECOMMEND_BORDER_COLORS,
    RECOMMEND_TITLE_BACKGROUND,
    SYNTHESIS_CONFIG_CONTROL_IDS,
    TIP_ICON,
    TIP_ICON_COLORS,
    TUI_TIPS,
    UNKNOWN_RESUME_MODEL,
    command_detail_display,
    command_suggestions,
    fast_control_value,
    fold_text_by_cells,
    format_reasoning_tokens_title,
    format_seconds,
    resume_select_options,
    same_display_message,
)
from .tui_components import (
    AgentPane,
    DetailScroll,
    PromptEditor,
    RainbowDetailFrame,
    ResumeChoicesLoaded,
)

activate_textual()

from rich.cells import cell_len
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.content import Content
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Select, Static


class PcrTextualRenderingMixin:
    def _tick(self) -> None:
        if self.running and self.started_at is not None:
            self.work_frame += 1
            if self.pending_accept_agent is not None:
                self.status = (
                    f"Stopping remaining agents; accepting "
                    f"AGENT-{self.pending_accept_agent:03d} {self._pulse()}"
                )
            elif self.queued_prompt and self.queued_agent is not None:
                self.status = (
                    f"Stopping remaining agents; continuing from "
                    f"AGENT-{self.queued_agent:03d} {self._pulse()}"
                )
            else:
                self.status = f"Working {int(time.monotonic() - self.started_at)}s {self._pulse()}"
            self._sync()
            return
        if self.follow_up_queue_editor_open:
            return
        if self.follow_up_continue_at is not None:
            remaining = max(
                0,
                int(self.follow_up_continue_at - time.monotonic() + 0.999),
            )
            if remaining <= 0:
                self.follow_up_continue_at = None
                self.follow_up_ready = True
                self._follow_up_countdown_second = None
                self._dispatch_follow_up()
            elif remaining != self._follow_up_countdown_second:
                self._follow_up_countdown_second = remaining
                self._refresh_follow_up_countdown(remaining)
            return
        if (
            self.follow_up_ready
            and self.follow_up_queue
            and self._successful_agent_result(self.selected_agent) is not None
            and (
                self.follow_up_source_finalized
                or not self._pending_sync_disabled()
            )
        ):
            self._dispatch_follow_up()

    @property
    def current_tip(self) -> str:
        return TUI_TIPS[self.tip_index]

    @property
    def current_tip_icon(self) -> str:
        return TIP_ICON

    @property
    def current_tip_icon_color(self) -> str:
        return TIP_ICON_COLORS[self.tip_icon_index]

    def _tip_renderable(self) -> Content:
        return Content.assemble(
            (f"{self.current_tip_icon}  ", f"bold {self.current_tip_icon_color}"),
            self.current_tip,
        )

    def _refresh_tip(self) -> None:
        if self._selection_drag_active():
            self._tip_refresh_deferred_for_selection = True
            return
        try:
            self.query_one("#tips", Static).update(self._tip_renderable(), layout=False)
        except Exception:
            return
        self._tip_refresh_deferred_for_selection = False

    def _advance_tip(self) -> None:
        self.tip_index = (self.tip_index + 1) % len(TUI_TIPS)
        self._refresh_tip()

    def _advance_tip_icon(self) -> None:
        self.tip_icon_index = (self.tip_icon_index + 1) % len(TIP_ICON_COLORS)
        self._refresh_tip()

    def _follow_up_queue_state(self) -> tuple[str, str]:
        if self.follow_up_queue_editor_open:
            return "Editing queue", "#e9c46a"
        if self.running:
            return "Agents running", "#f0c674"
        if self.storage_preflight_inflight:
            return "Checking storage", "#f0c674"
        if self.follow_up_continue_at is not None:
            remaining = max(
                0,
                int(self.follow_up_continue_at - time.monotonic() + 0.999),
            )
            return (
                f"AGENT-{self.selected_agent:03d} · starts in {remaining}s",
                "#7ec8e3",
            )
        if self.follow_up_ready:
            return "Choose a successful Agent", "#8bd49c"
        if self.follow_up_source_finalized:
            return "Ready from finalized Agent", "#8bd49c"
        return "Waiting for recommendation", "#aebed3"

    def _follow_up_queue_renderable(
        self,
        content_width: int | None = None,
    ) -> Text:
        if not self.follow_up_queue:
            return Text()

        count = len(self.follow_up_queue)
        state, state_color = self._follow_up_queue_state()
        content = Text()
        content.append("◆ ", style="bold #5aa9bd")
        content.append("QUEUE", style="bold #c8edf5")
        content.append(" · ", style="#63778a")
        content.append(
            f"{count} {'item' if count == 1 else 'items'}",
            style="bold #e9c46a",
        )
        content.append(" · ", style="#63778a")
        content.append(state, style=state_color)

        number_width = len(str(count))
        for index, item in enumerate(self.follow_up_queue, 1):
            queue_meta = {"pcr_queue_position": index}
            prefix_style = Style(
                color="#6faec0",
                bold=True,
                meta=queue_meta,
            )
            prompt_style = Style(color="#edf5f7", meta=queue_meta)
            continuation_style = Style(color="#536b7a", meta=queue_meta)
            if count == 1:
                prefix = "  › "
                continuation = "    "
            else:
                prefix = f"  {index:>{number_width}} │ "
                continuation = f"  {'':>{number_width}} │ "
            if content_width is None:
                prompt_lines = item.prompt.splitlines() or [""]
            else:
                body_width = max(2, content_width - cell_len(prefix))
                prompt_lines = (
                    fold_text_by_cells(item.prompt, body_width).splitlines()
                    or [""]
                )

            content.append("\n", style=prompt_style)
            content.append(prefix, style=prefix_style)
            content.append(prompt_lines[0], style=prompt_style)
            for line in prompt_lines[1:]:
                content.append("\n", style=prompt_style)
                content.append(continuation, style=continuation_style)
                content.append(line, style=prompt_style)
        return content

    def _follow_up_queue_text(self) -> str:
        return self._follow_up_queue_renderable().plain

    def _refresh_follow_up_queue(self) -> None:
        if self._selection_drag_active() or self._screen_selection_active():
            self._follow_up_queue_refresh_deferred = True
            return
        try:
            frame = self.query_one(
                "#follow-up-queue-frame",
                VerticalScroll,
            )
            widget = self.query_one("#follow-up-queue", Static)
        except Exception:
            return
        content_width = int(getattr(widget.size, "width", 0) or 0)
        if content_width <= 0:
            content_width = max(
                0,
                int(getattr(frame.size, "width", 0) or 0) - 2,
            )
        if content_width <= 0:
            content_width = max(
                0,
                int(getattr(self.size, "width", 0) or 0) - 4,
            )
        content_width = max(8, content_width or 80)
        renderable = self._follow_up_queue_renderable(content_width)
        text = renderable.plain
        frame.display = bool(text)
        if not text:
            self._follow_up_queue_cache = ""
            self._follow_up_queue_items_cache = ()
            return
        visible_lines = sum(
            max(1, (cell_len(line) + content_width - 1) // content_width)
            for line in text.splitlines()
        )
        frame.styles.height = min(4, max(2, visible_lines))
        item_key = tuple(item.prompt for item in self.follow_up_queue)
        items_changed = item_key != self._follow_up_queue_items_cache
        if text != self._follow_up_queue_cache:
            widget.update(renderable)
            self._follow_up_queue_cache = text
            self._follow_up_queue_items_cache = item_key
        if items_changed:
            self.call_after_refresh(frame.scroll_home, animate=False)
        self._follow_up_queue_refresh_deferred = False

    def _selected_agent_is_recommended(self) -> bool:
        pane = self.agents.get(self.selected_agent)
        return bool(
            pane is not None
            and pane.idx == self.recommended_agent
            and self._has_detail_content(pane)
        )

    def _recommend_border_colors(
        self,
        palette_frame: int | None = None,
    ) -> tuple[str, str, str, str]:
        size = len(RECOMMEND_BORDER_COLORS)
        offsets = (0, size // 4, size // 2, (size * 3) // 4)
        palette_frame = (
            self.recommend_border_frame
            if palette_frame is None
            else palette_frame
        )
        return tuple(
            RECOMMEND_BORDER_COLORS[
                (palette_frame + offset) % size
            ]
            for offset in offsets
        )

    def _apply_recommend_border_colors(
        self,
        frame: RainbowDetailFrame,
        edge: str | None = None,
        palette_frame: int | None = None,
    ) -> None:
        top, right, bottom, left = self._recommend_border_colors(palette_frame)
        frame.set_rainbow_colors(top, right, bottom, left, edge=edge)

    def _refresh_recommend_border(
        self,
        edge: str,
        palette_frame: int,
    ) -> None:
        if self._selection_drag_active() or self._screen_selection_active():
            self._recommend_border_deferred_for_selection = True
            return
        if not self._selected_agent_is_recommended():
            self._recommend_border_deferred_for_selection = False
            return
        try:
            frame = self.query_one("#detail-frame", RainbowDetailFrame)
        except Exception:
            return
        self._recommend_border_deferred_for_selection = False
        self._apply_recommend_border_colors(
            frame,
            edge=edge,
            palette_frame=palette_frame,
        )

    def _advance_recommend_border(self) -> None:
        if not self.app_in_foreground or not self._selected_agent_is_recommended():
            return
        if self._selection_drag_active() or self._screen_selection_active():
            self._recommend_border_deferred_for_selection = True
            return
        palette_frame = (
            self.recommend_border_frame + 1
        ) % len(RECOMMEND_BORDER_COLORS)
        edge = RainbowDetailFrame.BORDER_EDGES[
            self.recommend_border_edge_index
        ]
        self.recommend_border_edge_index = (
            self.recommend_border_edge_index + 1
        ) % len(RainbowDetailFrame.BORDER_EDGES)
        self._refresh_recommend_border(edge, palette_frame)
        if self.recommend_border_edge_index == 0:
            self.recommend_border_frame = palette_frame

    def _style_detail_frame(
        self,
        frame: RainbowDetailFrame,
        pane: AgentPane | None,
        detail_visible: bool,
    ) -> None:
        recommended = bool(
            detail_visible
            and pane is not None
            and pane.idx == self.recommended_agent
        )
        if recommended:
            if not frame.rainbow_active:
                self._apply_recommend_border_colors(frame)
                frame.rainbow_active = True
            frame.styles.border_title_background = RECOMMEND_TITLE_BACKGROUND
            return
        frame.rainbow_active = False
        frame.styles.border = ("round", "cyan")
        frame.styles.border_title_color = "#c8edf5"
        frame.styles.border_title_background = "#101216"

    def _sync(self) -> None:
        if self._selection_drag_active():
            self._sync_deferred_for_selection = True
            return
        try:
            self.query_one("#runner-frame", Vertical)
        except Exception:
            return
        self._sync_deferred_for_selection = False
        self._sync_runner_panel()
        self._refresh_follow_up_queue()
        frame = self.query_one("#detail-frame", RainbowDetailFrame)
        pane = self.agents.get(self.selected_agent)
        detail_visible = self._has_detail_content(pane)
        frame.display = detail_visible
        self._style_detail_frame(frame, pane, detail_visible)
        frame.border_title = self._detail_title(pane) if detail_visible and pane is not None else ""

        scroll = self.query_one("#detail-scroll", DetailScroll)
        should_follow_end = scroll.follow_tail
        detail = self.query_one("#detail", Static)
        cache_key_before = self._detail_cache_key
        detail_renderable = self._detail_renderable()
        detail_changed = self._detail_cache_key != cache_key_before
        if detail_changed:
            detail.update(detail_renderable)
        if detail_visible and detail_changed and should_follow_end:
            self.call_after_refresh(
                scroll.follow_end_if_enabled,
                animate=False,
                immediate=True,
            )
        self.query_one("#state", Static).update(self._state_text())

    def _refresh_resume_control(self) -> None:
        if not self.is_running:
            return
        workspace = self.workspace
        if self.resume_choices_inflight is not None:
            _inflight_id, inflight_workspace = self.resume_choices_inflight
            if inflight_workspace == workspace:
                return
        self.resume_choices_request += 1
        request_id = self.resume_choices_request
        self.resume_choices_inflight = (request_id, workspace)
        self.resume_choices_loaded = False
        include_non_interactive = bool(
            getattr(self.args, "resume_include_non_interactive", True)
        )

        def target() -> None:
            entries: list[Any] = []
            error = ""
            try:
                with self._resume_io_lock:
                    entries = self._list_resume_sessions(
                        workspace,
                        include_non_interactive=include_non_interactive,
                    )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            try:
                self.call_from_thread(
                    self.post_message,
                    ResumeChoicesLoaded(request_id, workspace, entries, error),
                )
            except RuntimeError:
                pass

        threading.Thread(target=target, name="pcr-resume-choices", daemon=True).start()

    def _set_select_control(
        self,
        control: Select,
        value: Any,
        options: list[tuple[Any, Any]] | None = None,
        *,
        mark_committed: bool = True,
    ) -> None:
        # Select posts Changed from its reactive watcher. Prevent it at the
        # control so delayed programmatic events cannot look like user input.
        with control.prevent(Select.Changed):
            if options is not None:
                control.set_options(options)
            if control.value != value:
                control.value = value
        if mark_committed:
            self._mark_model_effort_control_committed(control)
            self._mark_fast_control_committed(control)

    def _sync_model_effort_control(
        self,
        control: Select,
        value: Any,
        options: list[tuple[Any, Any]] | None = None,
    ) -> None:
        if self._model_effort_control_has_pending_value(control):
            pending_value = control.value
            self._set_select_control(
                control,
                pending_value,
                options,
                mark_committed=False,
            )
            return
        self._set_select_control(control, value, options)

    def _apply_resume_choices(self, entries: list[Any]) -> None:
        self.resume_entries = entries
        self._refresh_effort_for_context()
        self._coerce_synthesis_effort_for_model()
        choices = resume_select_options(entries, self.resume_session_id)
        options_changed = choices != self.resume_choices
        self.resume_choices = choices
        try:
            control = self.query_one("#config-resume", Select)
        except Exception:
            return
        self._updating_controls = True
        try:
            self._set_select_control(
                control,
                self.resume_session_id,
                self.resume_choices if options_changed else None,
            )
        finally:
            self._updating_controls = False

    def _sync_runner_panel(self) -> None:
        rows = dict(self._tree_rows())
        static_rows = {
            "CONVERSATION": "#runner-conversation",
            "WORKSPACE": "#runner-workspace",
            "RUNS_ROOT": "#runner-runs-root",
            "CODEX_BIN": "#runner-codex-bin",
        }
        for label, selector in static_rows.items():
            self.query_one(selector, Static).update(rows.get(label, ""))

        recommended_key = self.query_one("#runner-recommended-agent-key", Static)
        recommended_value = self.query_one("#runner-recommended-agent", Static)
        recommended_visible = self.recommended_agent is not None
        recommended_key.display = recommended_visible
        recommended_value.display = recommended_visible
        if recommended_visible:
            recommended_value.update(f"agent_{self.recommended_agent:03d}")

        model_select = self.query_one("#config-model", Select)
        effort_select = self.query_one("#config-effort", Select)
        fast_select = self.query_one("#config-fast", Select)
        synthesis_model_select = self.query_one(
            "#config-synthesis-model",
            Select,
        )
        synthesis_effort_select = self.query_one(
            "#config-synthesis-effort",
            Select,
        )
        synthesis_fast_select = self.query_one(
            "#config-synthesis-fast",
            Select,
        )
        current_model = str(getattr(self.args, "model", None) or "")
        display_model = (
            str(model_select.value or "")
            if self._model_effort_control_has_pending_value(model_select)
            else current_model
        )
        model_options = None
        if display_model not in {
            value for _label, value in self.model_choices
        }:
            self.model_choices = self.model_registry.model_options(display_model)
            model_options = self.model_choices
        current_effort = str(getattr(self.args, "effort", None) or "")
        display_effort = (
            str(effort_select.value or "")
            if self._model_effort_control_has_pending_value(effort_select)
            else current_effort
        )
        current_subagents = bool(getattr(self.args, "subagents", False))
        if display_model:
            effort_model = display_model
        elif self.resume_session_id:
            effort_model = (
                self._resume_model_for_effort() or UNKNOWN_RESUME_MODEL
            )
        else:
            effort_model = None
        desired_effort_choices = self.model_registry.effort_options(
            effort_model,
            display_effort,
        )
        effort_options = None
        if desired_effort_choices != self.effort_choices:
            self.effort_choices = desired_effort_choices
            effort_options = self.effort_choices
        current_synthesis_model = self._synthesis_model_control_value()
        desired_synthesis_model_choices = self._synthesis_model_options()
        synthesis_model_options = None
        if desired_synthesis_model_choices != self.synthesis_model_choices:
            self.synthesis_model_choices = desired_synthesis_model_choices
            synthesis_model_options = self.synthesis_model_choices
        current_synthesis_effort = self._synthesis_effort_control_value()
        desired_synthesis_effort_choices = self._synthesis_effort_options()
        synthesis_effort_options = None
        if desired_synthesis_effort_choices != self.synthesis_effort_choices:
            self.synthesis_effort_choices = desired_synthesis_effort_choices
            synthesis_effort_options = self.synthesis_effort_choices
        desired_synthesis_fast_choices = self._synthesis_fast_options()
        synthesis_fast_options = None
        if desired_synthesis_fast_choices != self.synthesis_fast_choices:
            self.synthesis_fast_choices = desired_synthesis_fast_choices
            synthesis_fast_options = self.synthesis_fast_choices
        resume_options = None
        if self.resume_session_id not in {value for _label, value in self.resume_choices}:
            self.resume_choices = resume_select_options(
                self.resume_entries,
                self.resume_session_id,
            )
            resume_options = self.resume_choices

        execution_select = self.query_one("#config-execution", Select)
        subagents_select = self.query_one("#config-subagents", Select)
        recommend_by_select = self.query_one("#config-recommend-by", Select)
        sync_back_select = self.query_one("#config-sync-back", Select)
        keep_workspaces_select = self.query_one("#config-keep-workspaces", Select)
        resume_select = self.query_one("#config-resume", Select)
        controls = [
            self.query_one("#config-agents", Input),
            self.query_one("#config-synthesis-agents", Input),
            self.query_one("#config-max-parallel", Input),
            subagents_select,
            self.query_one("#config-subagents-limit", Input),
            execution_select,
            recommend_by_select,
            model_select,
            effort_select,
            fast_select,
            synthesis_model_select,
            synthesis_effort_select,
            synthesis_fast_select,
            self.query_one("#config-follow-up-delay", Input),
            sync_back_select,
            keep_workspaces_select,
            resume_select,
        ]
        self._updating_controls = True
        try:
            agents_input = self.query_one("#config-agents", Input)
            if self.focused is not agents_input:
                agents_value = str(self.num_agents)
                agents_input.value = agents_value
                self._committed_input_values["config-agents"] = agents_value
            synthesis_input = self.query_one(
                "#config-synthesis-agents",
                Input,
            )
            if self.focused is not synthesis_input:
                synthesis_value = str(self.synthesis_agents)
                synthesis_input.value = synthesis_value
                self._committed_input_values[
                    "config-synthesis-agents"
                ] = synthesis_value
            max_parallel_input = self.query_one("#config-max-parallel", Input)
            if self.focused is not max_parallel_input:
                max_parallel_value = rows.get("MAX_PARALLEL", "")
                max_parallel_input.value = max_parallel_value
                self._committed_input_values["config-max-parallel"] = max_parallel_value
            subagents_limit_input = self.query_one(
                "#config-subagents-limit",
                Input,
            )
            if self.focused is not subagents_limit_input:
                subagents_limit_value = str(
                    getattr(
                        self.args,
                        "subagents_limit",
                        DEFAULT_SUBAGENTS_LIMIT,
                    )
                )
                subagents_limit_input.value = subagents_limit_value
                self._committed_input_values[
                    "config-subagents-limit"
                ] = subagents_limit_value
            follow_up_delay_input = self.query_one(
                "#config-follow-up-delay",
                Input,
            )
            if self.focused is not follow_up_delay_input:
                follow_up_delay_value = str(self._follow_up_delay_seconds())
                follow_up_delay_input.value = follow_up_delay_value
                self._committed_input_values[
                    "config-follow-up-delay"
                ] = follow_up_delay_value
            self._set_select_control(
                execution_select,
                rows.get("EXECUTION", "parallel"),
            )
            self._set_select_control(
                subagents_select,
                current_subagents,
            )
            self._set_select_control(
                recommend_by_select,
                str(
                    getattr(
                        self.args,
                        "recommend_by",
                        "reasoning_tokens",
                    )
                ),
            )
            self._sync_model_effort_control(
                model_select,
                current_model,
                model_options,
            )
            self._sync_model_effort_control(
                effort_select,
                current_effort,
                effort_options,
            )
            if self._fast_control_has_pending_value(fast_select):
                self._set_select_control(
                    fast_select,
                    fast_select.value,
                    mark_committed=False,
                )
            else:
                self._set_select_control(
                    fast_select,
                    fast_control_value(getattr(self.args, "fast", None)),
                )
            self._sync_model_effort_control(
                synthesis_model_select,
                current_synthesis_model,
                synthesis_model_options,
            )
            self._sync_model_effort_control(
                synthesis_effort_select,
                current_synthesis_effort,
                synthesis_effort_options,
            )
            if self._fast_control_has_pending_value(
                synthesis_fast_select
            ):
                self._set_select_control(
                    synthesis_fast_select,
                    synthesis_fast_select.value,
                    synthesis_fast_options,
                    mark_committed=False,
                )
            else:
                self._set_select_control(
                    synthesis_fast_select,
                    self._synthesis_fast_control_value(),
                    synthesis_fast_options,
                )
            self._set_select_control(
                sync_back_select,
                not bool(getattr(self.args, "no_sync_back", False)),
            )
            self._set_select_control(
                keep_workspaces_select,
                bool(getattr(self.args, "keep_workspaces", False)),
            )
            self._set_select_control(
                resume_select,
                self.resume_session_id,
                resume_options,
            )
            synthesis_editable = self._synthesis_config_is_editable()
            for control in controls:
                if self.storage_preflight_inflight:
                    control.disabled = True
                elif not self.running:
                    control.disabled = False
                elif control.id == FOLLOW_UP_CONFIG_CONTROL_ID:
                    control.disabled = False
                elif control.id in SYNTHESIS_CONFIG_CONTROL_IDS:
                    control.disabled = not synthesis_editable
                else:
                    control.disabled = True
        finally:
            self._updating_controls = False

    def _tree_renderable(self) -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        for label, value in self._tree_rows():
            table.add_row(label, value)
        return Panel(table, title=Text("PARALLEL-CODEX-RUNNER", style="bold"), border_style="cyan")

    def _tree_text(self) -> str:
        rows = self._tree_rows()
        label_width = max(len(label) for label, _value in rows)
        return "\n".join(f"{label:<{label_width}}  {value}" for label, value in rows)

    def _tree_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [
            ("CONVERSATION", self.status),
            *self.run_info_rows,
        ]
        if self.recommended_agent is not None:
            rows.append(
                ("RECOMMENDED AGENT", f"agent_{self.recommended_agent:03d}")
            )
        return rows

    def _visible_info_rows(self, rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
        hidden = {"METADATA", "WORKSPACE COPIES"}
        return [(label, value) for label, value in rows if label not in hidden]

    def _base_info_rows(self) -> list[tuple[str, str]]:
        max_parallel = (
            1
            if getattr(self.args, "serial", False)
            else min(getattr(self.args, "max_parallel", None) or self.num_agents, self.num_agents)
        )
        execution = "serial" if max_parallel == 1 else "parallel"
        run_base = choose_run_base(
            self.workspace,
            getattr(self.args, "runs_dir", None),
        )
        return [
            ("CODEX_BIN", str(getattr(self.args, "codex_bin", "codex"))),
            ("WORKSPACE", absolute_path_for_display(self.workspace)),
            ("RUNS_ROOT", f"pending under {absolute_path_for_display(run_base)}"),
            ("AGENTS", str(self.num_agents)),
            ("SYNTHESIS_AGENTS", str(self.synthesis_agents)),
            ("EXECUTION", execution),
            ("MAX_PARALLEL", str(max_parallel)),
            (
                "SUBAGENTS",
                "YES" if getattr(self.args, "subagents", False) else "NO",
            ),
            (
                "SUBAGENTS_LIMIT",
                str(
                    getattr(
                        self.args,
                        "subagents_limit",
                        DEFAULT_SUBAGENTS_LIMIT,
                    )
                ),
            ),
            (
                "RECOMMEND_BY",
                str(
                    getattr(
                        self.args,
                        "recommend_by",
                        "reasoning_tokens",
                    )
                ),
            ),
            (
                "MODEL",
                self.model_registry.model_display(
                    getattr(self.args, "model", None)
                ),
            ),
            (
                "EFFORT",
                self.model_registry.effort_display(
                    self._model_for_effort(),
                    getattr(self.args, "effort", None),
                ),
            ),
            (
                "FAST",
                format_fast_mode(
                    getattr(self.args, "fast", None),
                    self.model_registry.configured_service_tier,
                ),
            ),
            ("SYNTHESIS_MODEL", self._synthesis_model_display()),
            ("SYNTHESIS_EFFORT", self._synthesis_effort_display()),
            ("SYNTHESIS_FAST", self._synthesis_fast_display()),
            ("FOLLOW_UP_DELAY", str(self._follow_up_delay_seconds())),
            ("SYNC_BACK", "NO" if getattr(self.args, "no_sync_back", False) else "YES"),
            ("KEEP_WORKSPACES", "YES" if getattr(self.args, "keep_workspaces", False) else "NO"),
            ("RESUME", self.resume_session_id or "NO"),
        ]

    def _detail_text(self) -> str:
        pane = self.agents.get(self.selected_agent)
        if pane is None:
            return ""
        if pane.show_diff:
            if pane.diff_loading:
                return self._pulse()
            return pane.diff_error or pane.diff_text
        blocks = self._detail_blocks(pane)
        if not blocks:
            return ""
        rendered: list[str] = []
        previous_style = ""
        for prefix, text, style in blocks:
            if rendered:
                separator = (
                    f"\n{self._detail_separator_text()}\n"
                    if self._needs_detail_separator(previous_style, style)
                    else "\n\n"
                )
                rendered.append(separator)
            rendered.append(self._format_prefixed_block(prefix, text))
            previous_style = style
        return "".join(rendered)

    def _detail_renderable(self) -> object:
        pane = self.agents.get(self.selected_agent)
        if pane is None:
            self._detail_cache_key = ("none", self.selected_agent, self.detail_revision)
            self._detail_cache_renderable = ""
            return ""
        key = self._detail_cache_key_for(pane)
        if key == self._detail_cache_key:
            return self._detail_cache_renderable

        if pane.show_diff:
            renderable = self._diff_renderable(pane)
            self._detail_cache_key = key
            self._detail_cache_renderable = renderable
            return renderable

        blocks = self._detail_blocks(pane)
        if not blocks:
            self._detail_cache_key = key
            self._detail_cache_renderable = ""
            return ""
        parts: list[str | tuple[str, str]] = []
        previous_style = ""
        for idx, (prefix, block, style) in enumerate(blocks):
            if idx:
                if self._needs_detail_separator(previous_style, style):
                    parts.append("\n")
                    parts.append((self._detail_separator_text(), DETAIL_DIVIDER_STYLE))
                    parts.append("\n")
                else:
                    parts.append("\n\n")
            formatted = self._format_prefixed_block(prefix, block)
            if style.startswith("command-"):
                self._append_command_renderable(parts, formatted, style.removeprefix("command-"))
            elif style.startswith(DETAIL_STYLE_PREFIX):
                self._append_detail_text_renderable(
                    parts,
                    formatted,
                    prefix,
                    style.removeprefix(DETAIL_STYLE_PREFIX),
                )
            else:
                parts.append((formatted, style))
            previous_style = style
        renderable = Content.assemble(*parts)
        self._detail_cache_key = key
        self._detail_cache_renderable = renderable
        return renderable

    def _append_detail_text_renderable(
        self,
        parts: list[str | tuple[str, str]],
        formatted: str,
        prefix: str,
        category: str,
    ) -> None:
        """Render the quiet gutter and readable body used by Codex CLI."""
        marker_style, body_style = DETAIL_TEXT_STYLES.get(
            category,
            ("white", "white"),
        )
        if prefix and formatted.startswith(prefix):
            parts.append((prefix, marker_style))
            self._append_detail_body_renderable(
                parts,
                formatted[len(prefix) :],
                category,
                body_style,
            )
        else:
            self._append_detail_body_renderable(parts, formatted, category, body_style)

    def _append_detail_body_renderable(
        self,
        parts: list[str | tuple[str, str]],
        body: str,
        category: str,
        body_style: str,
    ) -> None:
        if category not in {"thought", "output"} or not body:
            parts.append((body, body_style))
            return

        in_code_block = False
        lines = body.splitlines(keepends=True) or [body]
        for line in lines:
            content = line.rstrip("\r\n")
            line_ending = line[len(content) :]
            stripped = content.lstrip()
            leading = content[: len(content) - len(stripped)]

            if stripped.startswith("```"):
                parts.append((content + line_ending, DETAIL_CODE_FENCE_STYLE))
                in_code_block = not in_code_block
                continue
            if in_code_block:
                parts.append((content + line_ending, DETAIL_CODE_STYLE))
                continue
            if re.match(r"^#{1,6}\s+", stripped):
                parts.append((content + line_ending, DETAIL_HEADING_STYLE))
                continue
            if re.match(r"^(?:[-*_])(?:\s*[-*_]){2,}$|^[─━┄╌]{3,}$", stripped):
                parts.append((content + line_ending, DETAIL_DIVIDER_STYLE))
                continue
            bullet = re.match(r"((?:[-*+]\s+|\d+[.)]\s+))(.*)$", stripped)
            if bullet:
                bullet_prefix = leading + bullet.group(1)
                parts.append((bullet_prefix, DETAIL_BULLET_STYLE))
                parts.append((bullet.group(2) + line_ending, body_style))
                continue
            parts.append((content + line_ending, body_style))

    def _detail_block_kind(self, style: str) -> str:
        if style.startswith("command-"):
            return "command"
        if style.startswith(DETAIL_STYLE_PREFIX):
            return style.removeprefix(DETAIL_STYLE_PREFIX)
        return style

    def _needs_detail_separator(self, previous_style: str, style: str) -> bool:
        previous = self._detail_block_kind(previous_style)
        current = self._detail_block_kind(style)
        if not previous or not current:
            return False
        if current == "input" and previous != "input":
            return True
        if current == "command" or previous == "command":
            return current != "command" or previous != "command"
        return False

    def _detail_separator_text(self) -> str:
        width = max(12, min(72, self._detail_content_width() - 2))
        return " " + "─" * width

    def _diff_renderable(self, pane: AgentPane) -> object:
        if pane.diff_loading:
            return Content.assemble((self._pulse(), "dim white"))
        text = pane.diff_error or pane.diff_text
        if not text:
            return ""
        parts: list[str | tuple[str, str]] = []
        current_style = ""
        current_lines: list[str] = []

        def flush() -> None:
            if current_lines:
                parts.append(("".join(current_lines), current_style))
                current_lines.clear()

        for line in text.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            if content.startswith("+++") or (content.startswith("+") and not content.startswith("+++")):
                style = "green"
            elif content.startswith("---") or (content.startswith("-") and not content.startswith("---")):
                style = "red"
            elif content.startswith("@@"):
                style = "cyan"
            elif re.match(r"^[AMDT]  ", content):
                style = "yellow"
            else:
                style = "white" if not pane.diff_error else "red"
            if current_lines and style != current_style:
                flush()
            current_style = style
            current_lines.append(line)
        flush()
        return Content.assemble(*parts)

    def _archive_agent_detail(self, pane: AgentPane) -> None:
        self.detail_history.extend(self._pane_detail_blocks(pane))
        self._mark_detail_dirty()

    def _archive_command_history(self) -> None:
        if self.command_history:
            self.detail_history.extend(self.command_history)
            self.command_history.clear()
            self._mark_detail_dirty()

    def _mark_detail_dirty(self, pane: AgentPane | None = None) -> None:
        if pane is None:
            self.detail_revision += 1
            self._detail_cache_key = None
        else:
            pane.revision += 1
            if pane.idx == self.selected_agent:
                self._detail_cache_key = None

    def _detail_cache_key_for(self, pane: AgentPane) -> tuple[Any, ...]:
        pulse = (
            self.work_frame
            if pane.diff_loading
            or (
                pane.status in {"running", "stopping"}
                and (pane.has_active_command() or not pane.has_agent_text())
            )
            else None
        )
        return (
            pane.idx,
            pane.revision,
            self.detail_revision,
            self._detail_content_width(),
            pane.show_diff,
            pulse,
        )

    def _detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
        return [*self.detail_history, *self._pane_detail_blocks(pane), *self.command_history]

    def _has_detail_content(self, pane: AgentPane | None) -> bool:
        return pane is not None and (
            pane.show_diff or bool(self._detail_blocks(pane))
        )

    def _pane_detail_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
        return [*pane.attempt_history, *self._current_attempt_blocks(pane)]

    def _current_attempt_blocks(self, pane: AgentPane) -> list[tuple[str, str, str]]:
        blocks: list[tuple[str, str, str]] = []
        if pane.input_text:
            blocks.append((DETAIL_INPUT_MARKER, pane.input_text, "detail-input"))
        event_blocks: list[tuple[str, str, str]] = []
        display_by_category = {
            "thought": (DETAIL_AGENT_MARKER, "detail-thought"),
            "output": (DETAIL_AGENT_MARKER, "detail-output"),
            "activity": (DETAIL_ACTIVITY_MARKER, "detail-activity"),
        }
        previous_category = ""
        for category, text in pane.ordered_detail_events():
            if category == "output" and pane.final_text and same_display_message(text, pane.final_text):
                continue
            if category == "command":
                command_active = pane.status in {"running", "stopping"}
                text, command_state = command_detail_display(text, active=command_active)
                prefix = self._command_marker() if command_state == "running" else "•"
                style = f"command-{command_state}"
            else:
                prefix, style = display_by_category.get(category, display_by_category["activity"])
            if (
                category != "command"
                and previous_category == category
                and event_blocks
                and event_blocks[-1][0] == prefix
                and event_blocks[-1][2] == style
            ):
                previous_prefix, previous_text, previous_style = event_blocks[-1]
                event_blocks[-1] = (
                    previous_prefix,
                    f"{previous_text}\n{text}",
                    previous_style,
                )
            else:
                event_blocks.append((prefix, text, style))
            previous_category = category
        blocks.extend(event_blocks)
        if pane.status == "running" and not pane.has_agent_text() and not pane.has_active_command():
            blocks.append((self._command_marker(), "", "detail-live"))
        if pane.final_text:
            blocks.append((DETAIL_AGENT_MARKER, pane.final_text, "detail-output"))
        return blocks

    def _detail_content_width(self) -> int:
        try:
            detail = self.query_one("#detail", Static)
            width = int(getattr(detail.content_size, "width", 0) or getattr(detail.size, "width", 0) or 0)
        except Exception:
            width = 0
        if width <= 0:
            try:
                width = int(getattr(self.size, "width", 0) or 0) - 4
            except Exception:
                width = 0
        return max(20, width or 80)

    def _format_prefixed_block(self, prefix: str, block: str) -> str:
        prefix_width = cell_len(prefix) + 1
        content_width = max(8, self._detail_content_width() - prefix_width - 4)
        folded = fold_text_by_cells(block, content_width)
        indent = " " * prefix_width
        return f"{prefix} {folded.replace(chr(10), chr(10) + indent)}"

    def _append_command_renderable(
        self,
        parts: list[str | tuple[str, str]],
        formatted: str,
        state: str,
    ) -> None:
        marker_style = {
            "running": "bold white",
            "success": "bold white",
            "failed": "bold red",
            "cancelled": "bold yellow",
        }.get(state, "bold white")
        status_style = {
            "success": "dim white",
            "failed": "red",
            "cancelled": "yellow",
        }.get(state, "dim white")

        for index, line in enumerate(formatted.splitlines(keepends=True)):
            if index == 0:
                marker, remainder = line[0], line[1:]
                parts.append((marker, marker_style))
                match = re.match(r"(\s+)(Running|Ran)(\s+)(.*)", remainder.rstrip("\n"))
                if match is None:
                    parts.append((remainder, "white"))
                    continue
                leading, verb, spacing, command = match.groups()
                parts.append(leading)
                parts.append((verb, "bold white"))
                parts.append(spacing)
                parts.append((command, "white"))
                if line.endswith("\n"):
                    parts.append("\n")
                continue

            stripped = line.lstrip()
            leading = line[: len(line) - len(stripped)]
            if stripped.startswith(("│", "└")):
                marker, remainder = stripped[0], stripped[1:]
                parts.append((leading + marker, "dim white"))
                parts.append((remainder, status_style if marker == "└" else "white"))
            else:
                parts.append((line, "white"))

    def _detail_title(self, pane: AgentPane) -> str:
        recommended = pane.idx == self.recommended_agent
        title = f"{'★ ' if recommended else ''}AGENT-{pane.idx:03d}"
        parts = []
        if pane.role == AGENT_ROLE_SYNTHESIS:
            parts.append("synthesis")
        if pane.status in {"stopping", "killed"}:
            parts.append(pane.status)
        if pane.rejected:
            parts.append("rejected")
        if pane.show_diff:
            parts.append("diff")
        if isinstance(pane.result, dict):
            seconds = format_seconds(pane.result.get("seconds"))
            if seconds:
                parts.append(f"seconds={seconds}")
        if pane.reasoning_tokens is not None:
            parts.append(
                format_reasoning_tokens_title(
                    pane.reasoning_tokens,
                    pane.reasoning_token_counts,
                    completed=isinstance(pane.result, dict),
                )
            )
        parts.append("←/→ switch")
        return f"{title}, {', '.join(parts)}"

    def _state_text(self) -> str:
        parts = [self.status]
        if summary := self._agent_summary():
            parts.append(summary)
        parts.extend(["Ctrl-C copy/clear/exit", "←/→ switch"])
        return " | ".join(parts)

    def _update_suggestions(self, value: str) -> None:
        suggestions = command_suggestions(value)
        self.suggestion_line_count = len(suggestions)
        try:
            widget = self.query_one("#suggestions", Static)
        except Exception:
            return
        widget.update("\n".join(suggestions))
        widget.styles.height = min(self.suggestion_line_count, MAX_SUGGESTIONS)

    def _prompt_content_width(self) -> int:
        try:
            prompt = self.query_one("#prompt", PromptEditor)
            width = int(getattr(prompt, "wrap_width", 0) or 0)
            if width > 0:
                return width
            width = int(getattr(prompt.content_size, "width", 0) or getattr(prompt.size, "width", 0) or 0)
        except Exception:
            width = 0
        return max(20, width or 80)

    def _sync_prompt_height(self) -> None:
        try:
            text = self.query_one("#prompt", PromptEditor).text
            width = self._prompt_content_width()
            visible_lines = sum(max(1, (cell_len(line) + width - 1) // width) for line in (text or "").split("\n"))
            self.prompt_height = max(3, min(20, visible_lines + 2))
            self.query_one("#prompt", PromptEditor).styles.height = self.prompt_height
        except Exception:
            return

    def _agent_summary(self) -> str:
        counts: dict[str, int] = {}
        for pane in self.agents.values():
            if pane.status == "idle":
                continue
            counts[pane.status] = counts.get(pane.status, 0) + 1
        return " · ".join(f"{count} {status}" for status, count in sorted(counts.items()))

    def _pulse(self) -> str:
        pulses = ("▰▱▱", "▰▰▱", "▰▰▰", "▱▰▰", "▱▱▰", "▱▱▱")
        return pulses[self.work_frame % len(pulses)]

    def _command_marker(self) -> str:
        return COMMAND_SPINNER_FRAMES[self.work_frame % len(COMMAND_SPINNER_FRAMES)]
