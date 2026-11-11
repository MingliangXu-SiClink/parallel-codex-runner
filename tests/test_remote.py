import base64
import asyncio
import concurrent.futures
import contextlib
import io
import json
import logging
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import httpx

from parallel_codex_runner_core.remote.controller import (
    RemoteController,
    RemoteControllerError,
)
from parallel_codex_runner_core.remote.crypto import (
    DeviceKeyPair,
    RemoteCryptoError,
    create_pairing_secret,
    pairing_commitment,
    pairing_proof,
)
from parallel_codex_runner_core.remote.filesystem import (
    FileSystemBrowser,
    RemoteFileSystemError,
)
from parallel_codex_runner_core.remote.devices import DeviceRegistry, DeviceRegistryError
from parallel_codex_runner_core.codex_accounts import default_pcr_codex_home, pcr_codex_home
from parallel_codex_runner_core.remote.daemon import (
    _configure_daemon_logging,
    _default_account_source,
    _pair_device,
)
from parallel_codex_runner_core.remote.ipc import PcrdClient, PcrdServer
from parallel_codex_runner_core.remote.journal import (
    CommandJournal,
    CommandJournalError,
)
from parallel_codex_runner_core.remote import notifications as remote_notifications
from parallel_codex_runner_core.remote.notifications import (
    MacOSRunNotifier,
    RelayRunCompletionMonitor,
    RunCompletionMonitor,
)
from parallel_codex_runner_core.remote.relay_bridge import RelayBridge, RelayLinkStatus
from parallel_codex_runner_core.remote.relay_config import (
    RelayConfigurationStore,
    environment_enrollment_token,
)
from parallel_codex_runner_core.remote.relay_http import (
    RelayHttpClient,
    RelayHttpError,
)
from parallel_codex_runner_core.remote.relay_state import (
    RelayState,
    RelayStateError,
    RelayStateStore,
)
from parallel_codex_runner_core.remote.protocol import (
    MAX_RESPONSE_BYTES,
    MINIMUM_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    ProtocolError,
    RemoteRequest,
    decode_message,
    encode_message,
)
from parallel_codex_runner_core.remote.service import (
    LAUNCH_AGENT_LABEL,
    SYSTEMD_USER_UNIT,
    install_launch_agent,
    install_systemd_user_service,
    launch_agent_payload,
    restart_service,
    service_logs,
    service_status,
    systemd_user_unit_payload,
)
from parallel_codex_runner_core.remote.transcript import (
    TRANSCRIPT_SCHEMA_VERSION,
    transcript_page,
)
from parallel_codex_runner_core.remote.workspace_registry import (
    WorkspaceRegistry,
    WorkspaceRegistryError,
)
from parallel_codex_runner_core.prompt_history import PromptHistoryStore
from parallel_codex_runner_core.plugin_runtime import DEFAULT_RUN_LIST_PAGE_BYTES


def _remote_follow_up_item(item_id: str, prompt: str) -> dict[str, object]:
    return {
        "id": item_id,
        "prompt": prompt,
        "settings": {"sync_back": True},
        "attempt": 0,
    }


class RemoteDaemonLoggingTests(unittest.TestCase):
    def test_daemon_suppresses_routine_http_client_request_logs(self) -> None:
        previous = {
            name: logging.getLogger(name).level
            for name in ("httpx", "httpcore")
        }
        try:
            with mock.patch.dict(
                os.environ,
                {
                    "PCRD_LOG_LEVEL": "INFO",
                    "PCRD_DEPENDENCY_LOG_LEVEL": "WARNING",
                },
            ):
                _configure_daemon_logging()
            self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
            self.assertEqual(logging.getLogger("httpcore").level, logging.WARNING)
        finally:
            for name, level in previous.items():
                logging.getLogger(name).setLevel(level)


class RemoteTranscriptTests(unittest.TestCase):
    def test_assistant_phase_survives_the_display_protocol(self) -> None:
        for phase, expected in (("commentary", "assistant_progress"), ("final_answer", "assistant_final")):
            page = transcript_page({"events": [{"type": "agent_line", "event_id": 1,
                "text": json.dumps({"type": "item.updated", "item": {
                    "type": "agent_message", "text": "hello", "phase": phase}})}]})
            self.assertEqual(page["items"][0]["activity_type"], expected)

    def test_transcript_page_returns_typed_items_in_event_order(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 4,
                "has_more": False,
                "total": 4,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "agent_message",
                                "message": "I will inspect the project.",
                            }
                        ),
                    },
                    {
                        "event_id": 2,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.started",
                                "item": {
                                    "id": "item_0",
                                    "type": "command_execution",
                                    "command": "/bin/zsh -lc 'rg TODO'",
                                    "status": "in_progress",
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 3,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_0",
                                    "type": "command_execution",
                                    "command": "/bin/zsh -lc 'rg TODO'",
                                    "aggregated_output": "large\ncommand\noutput",
                                    "exit_code": 0,
                                    "status": "completed",
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 4,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_1",
                                    "type": "reasoning",
                                    "text": "**Checking the result**",
                                },
                            }
                        ),
                    },
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["schema_version"], 1)
        self.assertEqual(
            [item["kind"] for item in page["items"]],
            ["output", "command", "command", "thought"],
        )
        self.assertEqual(page["items"][1]["operation_id"], "item_0")
        self.assertEqual(page["items"][1]["status"], "running")
        self.assertEqual(page["items"][2]["status"], "completed")
        self.assertEqual(page["items"][2]["text"], "rg TODO")
        self.assertNotIn("large", page["items"][2]["text"])
        self.assertEqual(page["items"][3]["text"], "Checking the result")

    def test_transcript_page_advances_cursor_when_page_has_only_noise(self) -> None:
        page = transcript_page(
            {
                "cursor": 10,
                "next_cursor": 12,
                "has_more": True,
                "total": 20,
                "events": [
                    {"event_id": 11, "type": "agent_tokens", "reasoning_tokens": 5},
                    {"event_id": 12, "type": "agent_status", "status": "running"},
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["next_cursor"], 12)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["items"], [])

    def test_transcript_page_hides_models_manager_runtime_diagnostic(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 1,
                "has_more": False,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": (
                            "ERROR codex_models_manager::manager: failed to refresh "
                            "available models: timeout waiting for child process to exit"
                        ),
                    }
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["next_cursor"], 1)
        self.assertEqual(page["items"], [])

    def test_transcript_page_formats_plain_runtime_log_as_diagnostic(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 2,
                "has_more": False,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": (
                            "2026-09-02T04:54:41.381240Z ERROR "
                            "codex_core::tools::router: error=exec_command failed: "
                            "CreateProcess { message: No such file or directory (os error 2) }"
                        ),
                    },
                    {
                        "event_id": 2,
                        "type": "agent_line",
                        "text": "ERROR codex_core::example: error=Something failed",
                    },
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["items"][0]["kind"], "warning")
        self.assertEqual(
            page["items"][0]["activity_type"],
            "missing_execution_path",
        )
        self.assertNotIn("codex_core", page["items"][0]["text"])
        self.assertEqual(page["items"][1]["kind"], "error")
        self.assertEqual(page["items"][1]["text"], "Something failed")
        self.assertEqual(
            page["items"][1]["activity_type"],
            "runtime_diagnostic",
        )

    def test_transcript_page_treats_resume_model_mismatch_as_warning(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 1,
                "has_more": False,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_0",
                                    "type": "error",
                                    "message": (
                                        "This session was recorded with model `luna` "
                                        "but is resuming with `sol`. Consider switching "
                                        "back to `luna` as it may affect Codex performance."
                                    ),
                                },
                            }
                        ),
                    }
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["items"][0]["kind"], "warning")
        self.assertIn("resuming with", page["items"][0]["text"])

    def test_transcript_page_treats_transport_fallback_as_warning(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 1,
                "has_more": False,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item_0",
                                    "type": "error",
                                    "message": (
                                        "Falling back from WebSockets to HTTPS transport. "
                                        "stream disconnected before completion"
                                    ),
                                },
                            }
                        ),
                    }
                ],
            }
        )

        assert page is not None
        self.assertEqual(page["items"][0]["kind"], "warning")

    def test_transcript_page_formats_and_tracks_codex_tool_activity(self) -> None:
        page = transcript_page(
            {
                "cursor": 0,
                "next_cursor": 7,
                "has_more": False,
                "events": [
                    {
                        "event_id": 1,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.started",
                                "item": {
                                    "id": "search-1",
                                    "type": "web_search",
                                    "query": "",
                                    "action": {"type": "other"},
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 2,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "search-1",
                                    "type": "web_search",
                                    "query": "ignored summary",
                                    "action": {
                                        "type": "search",
                                        "queries": ["first query", "second query"],
                                    },
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 3,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "change-1",
                                    "type": "file_change",
                                    "changes": [
                                        {
                                            "path": "/tmp/run/workspaces/agent_001/src/app.py",
                                            "kind": "update",
                                        },
                                        {"path": "/tmp/new.py", "kind": "add"},
                                    ],
                                    "status": "completed",
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 4,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.updated",
                                "item": {
                                    "id": "todo-1",
                                    "type": "todo_list",
                                    "items": [
                                        {"text": "Inspect", "completed": True},
                                        {"text": "Verify", "completed": False},
                                    ],
                                },
                            }
                        ),
                    },
                    {
                        "event_id": 5,
                        "type": "agent_line",
                        "text": json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "error-1",
                                    "type": "error",
                                    "message": "Tool failed",
                                },
                            }
                        ),
                    },
                ],
            }
        )

        assert page is not None
        self.assertEqual(
            [item["kind"] for item in page["items"]],
            ["activity", "activity", "activity", "activity", "error"],
        )
        self.assertEqual(page["items"][0]["status"], "running")
        self.assertEqual(page["items"][0]["text"], "...")
        self.assertEqual(page["items"][1]["status"], "completed")
        self.assertEqual(page["items"][1]["activity_type"], "web_search")
        self.assertEqual(page["items"][1]["operation_id"], "search-1")
        self.assertEqual(page["items"][1]["text"], "first query\nsecond query")
        self.assertEqual(page["items"][2]["activity_type"], "file_change")
        self.assertEqual(page["items"][2]["text"], "~ src/app.py\n+ /tmp/new.py")
        self.assertEqual(page["items"][3]["text"], "[x] Inspect\n[ ] Verify")
        self.assertEqual(page["items"][4]["text"], "Tool failed")


class FakeRunManager:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.continued: list[dict] = []
        self.agent_requests: list[dict] = []
        self.history_requests: list[dict] = []
        self.synthesis_updates: list[dict] = []
        self.workspace = ""
        self.session_id = "session-next"
        self.source_prompt = ""
        self.visible_text_prefix = ""
        self.closed = False

    def health(self) -> dict:
        return {"ok": True, "active_runs": []}

    def start_run(self, **kwargs) -> dict:
        self.started.append(kwargs)
        return {"status": "started", "run_id": "pcr-test-run"}

    def list_runs(
        self,
        limit: int = 20,
        cursor: str = "",
        max_bytes: int = DEFAULT_RUN_LIST_PAGE_BYTES,
        workspace: str = "",
    ) -> dict:
        del workspace
        return {
            "runs": [],
            "total": 0,
            "limit": limit,
            "cursor": cursor,
            "next_cursor": "",
            "has_more": False,
            "serialized_bytes": 2,
            "byte_limit": max_bytes,
        }

    def get_run_summaries(self, run_ids) -> dict:
        wanted = set(run_ids)
        listed = self.list_runs(limit=max(1, len(wanted)))
        return {
            **listed,
            "runs": [
                run for run in listed.get("runs", [])
                if run.get("run_id") in wanted
            ],
        }

    def get_run_overviews(self, run_ids) -> dict:
        summaries = self.get_run_summaries(run_ids)
        overviews = []
        for run in summaries.get("runs", []):
            statuses = [
                str(agent.get("status") or "").strip().lower()
                for agent in run.get("agents", [])
            ]
            terminal = {
                "success", "failed", "error", "killed", "stopped",
                "cancelled", "canceled", "interrupted", "discarded",
            }
            overviews.append({
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "worker_active": run.get("worker_active", False),
                "agents_total": len(statuses),
                "agents_finished": sum(status in terminal for status in statuses),
                "agents_failed": sum(
                    status in terminal and status != "success" for status in statuses
                ),
            })
        return {"runs": overviews, "requested": len(set(run_ids))}

    def get_agent(self, run_id, agent, **kwargs) -> dict:
        self.agent_requests.append({"run_id": run_id, "agent": agent, **kwargs})
        result = {
            "run_id": run_id,
            "agent": agent,
            "event_page": {
                "run_id": run_id,
                "cursor": kwargs.get("cursor", 0),
                "next_cursor": kwargs.get("cursor", 0) + 1,
                "has_more": False,
                "total": 1,
                "events": [
                    {
                        "event_id": kwargs.get("cursor", 0) + 1,
                        "type": "agent_line",
                        "text": "agent_message:hello",
                    }
                ],
            },
        }
        if kwargs.get("include_final_message_page"):
            cursor = kwargs.get("final_message_cursor", 0)
            result["final_message"] = ""
            result["final_message_page"] = {
                "cursor": cursor,
                "next_cursor": cursor + 5,
                "has_more": False,
                "total_bytes": cursor + 5,
                "message_id": "message-1",
                "text": "final",
            }
        return result

    def run_history(self, run_id, **kwargs) -> dict:
        self.history_requests.append({"run_id": run_id, **kwargs})
        return {
            "run_id": run_id,
            "cursor": kwargs.get("cursor", 0),
            "next_cursor": kwargs.get("cursor", 0) + 1,
            "has_more": False,
            "entries": [{"category": "user", "text": "earlier question"}],
        }

    def rewrite_agent_visible_text(self, run_id, agent, text) -> str:
        del run_id, agent
        return self.visible_text_prefix + text

    def rewrite_agent_visible_texts(self, run_id, agent, texts) -> list[str]:
        del run_id, agent
        return [self.visible_text_prefix + text for text in texts]

    def continue_from_agent(self, run_id, agent, prompt, **kwargs) -> dict:
        self.continued.append(
            {
                "run_id": run_id,
                "agent": agent,
                "prompt": prompt,
                **kwargs,
            }
        )
        result = {"status": "started", "run_id": "pcr-next-run"}
        if self.workspace:
            result.update(
                {
                    "run": {
                        "run_id": "pcr-next-run",
                        "workspace": self.workspace,
                    },
                    "continued_from": {
                        "session_id": self.session_id,
                        "prompt": self.source_prompt,
                    },
                }
            )
        return result

    def update_synthesis_settings(self, run_id, agents, **kwargs) -> dict:
        self.synthesis_updates.append(
            {"run_id": run_id, "agents": agents, **kwargs}
        )
        return {"run_id": run_id, "status": "running"}

    def close(self) -> None:
        self.closed = True


class RemoteProtocolTests(unittest.TestCase):
    def test_request_round_trip_preserves_unicode_and_revision(self) -> None:
        request = RemoteRequest(
            action="filesystem.list",
            body={"path": "/tmp/中文目录"},
            client_id="android-1",
            expected_revision=4,
        )
        encoded = encode_message(request.to_mapping())
        decoded = RemoteRequest.from_mapping(decode_message(encoded.rstrip()))

        self.assertEqual(decoded.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(decoded.action, "filesystem.list")
        self.assertEqual(decoded.body["path"], "/tmp/中文目录")
        self.assertEqual(decoded.expected_revision, 4)

    def test_rejects_unknown_protocol_version(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "not supported"):
            RemoteRequest.from_mapping(
                {
                    "protocol_version": PROTOCOL_VERSION + 1,
                    "action": "system.health",
                }
            )


class RunCompletionMonitorTests(unittest.TestCase):
    class SnapshotSource:
        def __init__(self, records: list[dict]) -> None:
            self.records = records

        def completion_snapshot(self) -> list[dict]:
            return [dict(record) for record in self.records]

    @staticmethod
    def record(
        run_id: str,
        *,
        working: bool,
        terminal: bool,
        status: str = "running",
    ) -> dict:
        return {
            "run_id": run_id,
            "workspace_name": "EyeTheia",
            "status": status,
            "working": working,
            "all_agents_terminal": terminal,
        }

    def test_adoption_stop_and_discard_never_deliver_completion(self) -> None:
        for status in ("finalized", "finalizing", "discarded", "stopped", "cancelled",
                       "interrupted", "expired", "cleanup_failed", "sync_ambiguous"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                source = self.SnapshotSource([self.record("run", working=True, terminal=False)])
                delivered = []
                path = Path(tmp) / "notifications.json"
                monitor = RunCompletionMonitor(source, path,
                    notifier=lambda _workspace, run: not delivered.append(run))
                monitor.poll_once()
                source.records = [self.record("run", working=False, terminal=True, status=status)]
                monitor.poll_once()
                RunCompletionMonitor(source, path,
                    notifier=lambda _workspace, run: not delivered.append(run)).poll_once()
                self.assertEqual(delivered, [])

    def test_ignores_old_completed_runs_and_notifies_new_terminal_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = self.SnapshotSource(
                [self.record("old", working=False, terminal=True, status="completed")]
            )
            delivered: list[tuple[str, str]] = []
            monitor = RunCompletionMonitor(
                source,
                Path(tmp) / "notifications.json",
                notifier=lambda workspace, run_id: not delivered.append(
                    (workspace, run_id)
                ),
            )

            monitor.poll_once()
            self.assertEqual(delivered, [])
            source.records.append(
                self.record("new", working=False, terminal=True, status="completed")
            )
            monitor.poll_once()
            monitor.poll_once()

            self.assertEqual(delivered, [("EyeTheia", "new")])

    def test_failed_adoption_does_not_rearm_an_undelivered_execution_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = self.SnapshotSource([self.record("run", working=True, terminal=False)])
            path = Path(tmp) / "notifications.json"
            attempts = []
            monitor = RunCompletionMonitor(source, path, notifier=lambda _workspace, run: bool(attempts.append(run)))
            monitor.poll_once()
            source.records = [self.record("run", working=False, terminal=True, status="completed")]
            monitor.poll_once()
            self.assertEqual(attempts, ["run"])
            source.records[0]["completion_reason"] = "finalization"
            restarted = RunCompletionMonitor(source, path, clock=lambda: 10**12,
                notifier=lambda _workspace, run: bool(attempts.append(run)))
            restarted.poll_once()
            self.assertEqual(attempts, ["run"])

    def test_restart_preserves_running_transition_and_allows_failed_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "notifications.json"
            source = self.SnapshotSource(
                [self.record("run-1", working=True, terminal=False)]
            )
            first = RunCompletionMonitor(
                source,
                state_path,
                notifier=lambda _workspace, _run_id: True,
            )
            first.poll_once()

            source.records = [
                self.record(
                    "run-1",
                    working=False,
                    terminal=True,
                    status="completed",
                )
            ]
            delivered: list[str] = []
            restarted = RunCompletionMonitor(
                source,
                state_path,
                notifier=lambda _workspace, run_id: not delivered.append(run_id),
            )
            restarted.poll_once()

            self.assertEqual(delivered, ["run-1"])

    def test_failed_delivery_is_retried_without_duplicate_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = self.SnapshotSource(
                [self.record("run-1", working=True, terminal=False)]
            )
            attempts = 0
            now = 1_000.0

            def notify(_workspace: str, _run_id: str) -> bool:
                nonlocal attempts
                attempts += 1
                return attempts > 1

            monitor = RunCompletionMonitor(
                source,
                Path(tmp) / "notifications.json",
                notifier=notify,
                clock=lambda: now,
                retry_base_seconds=30,
            )
            monitor.poll_once()
            source.records = [
                self.record(
                    "run-1",
                    working=False,
                    terminal=True,
                    status="completed",
                )
            ]

            monitor.poll_once()
            monitor.poll_once()
            self.assertEqual(attempts, 1)
            now += 30
            monitor.poll_once()

            self.assertEqual(attempts, 2)

    def test_notification_retry_backoff_survives_monitor_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "notifications.json"
            source = self.SnapshotSource(
                [self.record("run-1", working=True, terminal=False)]
            )
            now = 1_000.0
            attempts = 0

            def notify(_workspace: str, _run_id: str) -> bool:
                nonlocal attempts
                attempts += 1
                return False

            monitor = RunCompletionMonitor(
                source,
                state_path,
                notifier=notify,
                clock=lambda: now,
                retry_base_seconds=60,
            )
            monitor.poll_once()
            source.records = [
                self.record(
                    "run-1",
                    working=False,
                    terminal=True,
                    status="completed",
                )
            ]
            monitor.poll_once()

            restarted = RunCompletionMonitor(
                source,
                state_path,
                notifier=notify,
                clock=lambda: now,
                retry_base_seconds=60,
            )
            restarted.poll_once()
            self.assertEqual(attempts, 1)

            now += 60
            restarted.poll_once()
            self.assertEqual(attempts, 2)

    def test_macos_notifier_passes_workspace_as_an_argument(self) -> None:
        workspace = 'A "quoted" workspace'
        completed = subprocess.CompletedProcess([], 0)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"PCR_MAC_NOTIFICATION_APP": tmp}),
            mock.patch.object(remote_notifications.sys, "platform", "darwin"),
            mock.patch.object(
                remote_notifications.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            app = Path(tmp)
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/MacOS/Parallel Codex").touch()
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"PCRNativeCompletionNotifications": True}))
            delivered = MacOSRunNotifier()(workspace, "run-1")

        self.assertTrue(delivered)
        self.assertEqual(run.call_args.args[0][-1], workspace)
        self.assertEqual(run.call_args.args[0][-3:-1], ["--pcr-notify-run-completed", "run-1"])
        self.assertNotIn("osascript", " ".join(run.call_args.args[0]))

    def test_finalization_never_rearms_a_delivered_notification_even_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notifications.json"
            source = self.SnapshotSource([self.record("run", working=True, terminal=False)])
            delivered = []
            notify = lambda _workspace, run: not delivered.append(run)
            monitor = RunCompletionMonitor(source, path, notifier=notify)
            monitor.poll_once()
            source.records = [self.record("run", working=False, terminal=True, status="completed")]
            monitor.poll_once()
            monitor = RunCompletionMonitor(source, path, notifier=notify)
            for status, working in [("finalizing", True), ("cleanup_failed", False), ("finalized", False)]:
                source.records = [self.record("run", working=working, terminal=True, status=status)]
                monitor.poll_once()
            self.assertEqual(delivered, ["run"])

    def test_old_client_is_not_launched_as_a_notification_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(remote_notifications.sys, "platform", "darwin"), \
             mock.patch.dict(os.environ, {"PCR_MAC_NOTIFICATION_APP": tmp}), \
             mock.patch.object(remote_notifications.subprocess, "run") as run:
            app = Path(tmp)
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/MacOS/Parallel Codex").touch()
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({}))
            self.assertFalse(MacOSRunNotifier()("Project", "run"))
            run.assert_not_called()


class RelayRunCompletionMonitorTests(unittest.TestCase):
    def test_no_agents_and_recovered_finalization_are_not_execution_completion(self) -> None:
        empty = self.response()["body"]
        empty["status"] = "failed"
        self.assertFalse(RelayRunCompletionMonitor._all_agents_terminal(empty))
        for metadata in ({"completion_reason": "finalization"}, {"worker_operation": "finalize"},
                         {"finalized_agent": 1}, {"finalizing_agent": 1}):
            run = self.response("success", "failed")["body"] | metadata
            self.assertFalse(RelayRunCompletionMonitor._all_agents_terminal(run))

    def test_adoption_or_stop_retires_watch_without_notification_even_after_restart(self) -> None:
        for status in ("finalized", "discarded", "stopped", "cancelled", "cleanup_failed", "sync_ambiguous"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                response = self.response("success", "killed")
                response["body"]["status"] = status
                path = Path(tmp) / "notifications.json"
                delivered = []
                values = dict(machine_id="mac", machine_public_key="key", run_id="run-1", workspace_name="Project")
                notify = lambda _workspace, run: not delivered.append(run)
                monitor = RelayRunCompletionMonitor(self.Proxy([response]), path, notifier=notify)
                monitor.watch(**values)
                monitor.poll_once()
                self.assertEqual(delivered, [])
                self.assertEqual(monitor.status()["watch_count"], 0)
                restarted = RelayRunCompletionMonitor(self.Proxy([response]), path, notifier=notify)
                restarted.watch(**values)
                restarted.poll_once()
                self.assertEqual(delivered, [])
                self.assertEqual(restarted.status()["watch_count"], 0)

    def test_reregistering_during_delivery_does_not_rearm_the_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            values = dict(machine_id="mac", machine_public_key="key", run_id="run-1", workspace_name="Project")
            delivered = []

            def notify(_workspace, run):
                delivered.append(run)
                monitor.watch(**{**values, "workspace_name": "Renamed Project"})
                return True

            monitor = RelayRunCompletionMonitor(
                self.Proxy([self.response("success"), self.response("success")]),
                Path(tmp) / "remote-notifications.json", notifier=notify,
            )
            monitor.watch(**values)
            monitor.poll_once()
            monitor.poll_once()
            self.assertEqual(delivered, ["run-1"])
            self.assertEqual(monitor.status()["watch_count"], 0)
            self.assertTrue(monitor.watch(**values)["completed"])

    def test_completed_watch_cannot_be_reregistered_by_another_window_or_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "remote-notifications.json"
            delivered = []
            notify = lambda _workspace, run: not delivered.append(run)
            monitor = RelayRunCompletionMonitor(self.Proxy([self.response("success")]), path, notifier=notify)
            values = dict(machine_id="mac", machine_public_key="key", run_id="run-1", workspace_name="Project")
            monitor.watch(**values)
            monitor.poll_once()
            restarted = RelayRunCompletionMonitor(self.Proxy([self.response("success")]), path, notifier=notify)
            restarted.watch(**values)
            restarted.poll_once()
            self.assertEqual(delivered, ["run-1"])
            self.assertEqual(restarted.status()["watch_count"], 0)

    def test_completion_waits_for_pending_synthesis(self) -> None:
        value = self.response("success")["body"]
        value["status"] = "running"
        value["worker_active"] = True
        self.assertFalse(RelayRunCompletionMonitor._all_agents_terminal(value))

    class Proxy:
        def __init__(self, responses: list[dict]) -> None:
            self.responses = responses
            self.calls: list[dict] = []

        def call(self, **kwargs) -> dict:
            self.calls.append(kwargs)
            response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
            if isinstance(response, Exception):
                raise response
            return response

    @staticmethod
    def response(*statuses: str) -> dict:
        return {
            "ok": True,
            "body": {
                "run_id": "run-1",
                "status": "completed" if statuses else "running",
                "agents": [
                    {"agent": index + 1, "status": status}
                    for index, status in enumerate(statuses)
                ],
            },
        }

    def test_remote_watch_survives_restart_and_notifies_after_all_agents_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "remote-notifications.json"
            now = 1_000.0
            running = {
                "ok": True,
                "body": {
                    "run_id": "run-1",
                    "status": "running",
                    "agents": [
                        {"agent": 1, "status": "success"},
                        {"agent": 2, "status": "running"},
                    ],
                },
            }
            first_proxy = self.Proxy([running])
            first = RelayRunCompletionMonitor(
                first_proxy,
                state_path,
                notifier=lambda _workspace, _run_id: True,
                poll_interval=15,
                clock=lambda: now,
            )
            first.watch(
                machine_id="mac-remote",
                machine_public_key="public-key",
                run_id="run-1",
                workspace_name="EyeTheia",
            )
            first.poll_once()
            self.assertEqual(first.status()["watch_count"], 1)

            now += 15
            delivered: list[tuple[str, str]] = []
            restarted_proxy = self.Proxy([self.response("success", "failed", "killed")])
            restarted = RelayRunCompletionMonitor(
                restarted_proxy,
                state_path,
                notifier=lambda workspace, run_id: not delivered.append(
                    (workspace, run_id)
                ),
                poll_interval=15,
                clock=lambda: now,
            )
            restarted.poll_once()
            restarted.poll_once()

            self.assertEqual(delivered, [("EyeTheia", "run-1")])
            self.assertEqual(restarted.status()["watch_count"], 0)
            self.assertEqual(restarted_proxy.calls[0]["action"], "turns.get")

    def test_remote_watch_retries_transient_relay_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = 2_000.0
            delivered: list[str] = []
            proxy = self.Proxy(
                [RuntimeError("offline"), self.response("success")]
            )
            monitor = RelayRunCompletionMonitor(
                proxy,
                Path(tmp) / "remote-notifications.json",
                notifier=lambda _workspace, run_id: not delivered.append(run_id),
                clock=lambda: now,
                retry_base_seconds=30,
            )
            monitor.watch(
                machine_id="mac-remote",
                machine_public_key="public-key",
                run_id="run-1",
                workspace_name="Project",
            )

            monitor.poll_once()
            monitor.poll_once()
            self.assertEqual(len(proxy.calls), 1)
            now += 30
            monitor.poll_once()

            self.assertEqual(delivered, ["run-1"])
            self.assertEqual(monitor.status()["watch_count"], 0)


class RemoteCryptoTests(unittest.TestCase):
    def test_pairing_proof_binds_both_devices_and_the_pairing(self) -> None:
        secret = create_pairing_secret()
        values = {
            "pairing_id": "pair-1",
            "mac_device_id": "mac-1",
            "mac_public_key": DeviceKeyPair.generate().public_key,
            "android_device_id": "android-1",
            "android_public_key": DeviceKeyPair.generate().public_key,
        }

        proof = pairing_proof(secret, **values)

        self.assertEqual(len(base64.b64decode(pairing_commitment(secret))), 32)
        self.assertEqual(proof, pairing_proof(secret, **values))
        self.assertNotEqual(
            proof,
            pairing_proof(secret, **{**values, "android_device_id": "android-2"}),
        )

    def test_encrypts_for_only_the_paired_device_and_authenticates_metadata(self) -> None:
        sender = DeviceKeyPair.generate()
        receiver = DeviceKeyPair.generate()
        payload = {"type": "request", "request": {"prompt": "修复中文测试"}}
        metadata = {
            "message_id": "message-1",
            "sender_device_id": "android-1",
            "target_device_id": "mac-1",
            "sequence": 7,
        }

        ciphertext = sender.encrypt(receiver.public_key, payload, **metadata)
        decoded = receiver.decrypt(sender.public_key, ciphertext, **metadata)

        self.assertEqual(decoded, payload)
        with self.assertRaisesRegex(RemoteCryptoError, "authentication failed"):
            receiver.decrypt(
                sender.public_key,
                ciphertext,
                **{**metadata, "sequence": 8},
            )

    def test_device_key_is_stable_and_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device.key"
            first = DeviceKeyPair.load_or_create(path)
            second = DeviceKeyPair.load_or_create(path)

            self.assertEqual(first.public_key, second.public_key)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class RelayHttpTests(unittest.TestCase):
    def test_relay_status_distinguishes_phone_controllers(self) -> None:
        status = RelayState(
            base_url="https://relay.test",
            mac_device_id="mac-1",
            device_name="Computer",
            access_token="access",
            refresh_token="refresh",
            user_id="user-1",
            trusted_device_keys={
                "macos-controller": "macos-key",
                "android-phone": "android-key",
            },
        ).public_mapping()

        self.assertTrue(status["paired"])
        self.assertEqual(status["trusted_devices"], 2)
        self.assertEqual(status["trusted_android_devices"], 1)
        self.assertEqual(
            status["trusted_controllers"],
            [
                {"device_id": "android-phone", "public_key": "android-key"},
                {"device_id": "macos-controller", "public_key": "macos-key"},
            ],
        )

    def test_pairing_approval_keeps_existing_trusted_controllers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelayStateStore(Path(tmp) / "relay.json")
            store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Computer",
                    pairing_id="pair-2",
                    trusted_device_keys={"android-1": "key-1"},
                )
            )

            store.trust_device_and_update_tokens(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "user_id": "user-1",
                    "device_id": "mac-1",
                    "expires_in": 3_600,
                },
                device_id="android-2",
                public_key="key-2",
                expected_pairing_id="pair-2",
            )
            reloaded = RelayStateStore(Path(tmp) / "relay.json").load()

            assert reloaded is not None
            self.assertEqual(
                reloaded.trusted_device_keys,
                {"android-1": "key-1", "android-2": "key-2"},
            )

    def test_stale_pairing_approval_cannot_clear_a_new_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelayStateStore(Path(tmp) / "relay.json")
            store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Computer",
                    pairing_id="pair-new",
                    poll_token="poll-new",
                    trusted_device_keys={"android-1": "key-1"},
                )
            )

            with self.assertRaisesRegex(RelayStateError, "newer pairing request"):
                store.trust_device_and_update_tokens(
                    {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "user_id": "user-1",
                        "device_id": "mac-1",
                        "expires_in": 3_600,
                    },
                    device_id="android-stale",
                    public_key="stale-key",
                    expected_pairing_id="pair-old",
                )

            saved = store.load()
            assert saved is not None
            self.assertEqual(saved.pairing_id, "pair-new")
            self.assertEqual(saved.poll_token, "poll-new")
            self.assertEqual(saved.trusted_device_keys, {"android-1": "key-1"})

    def test_revoked_peer_is_pruned_without_removing_a_concurrent_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelayStateStore(Path(tmp) / "relay.json")
            state = RelayState(
                base_url="https://relay.test",
                mac_device_id="mac-1",
                device_name="Computer",
                trusted_device_keys={"android-old": "old-key"},
            )
            store.save(state)
            expected = dict(state.trusted_device_keys)
            current = store.load()
            assert current is not None
            current.trusted_device_keys["android-new"] = "new-key"
            store.save(current)

            updated = store.remove_unavailable_trusted_devices(
                {},
                expected=expected,
            )

            assert updated is not None
            self.assertNotIn("android-old", updated.trusted_device_keys)
            self.assertEqual(
                updated.trusted_device_keys["android-new"],
                "new-key",
            )

    def test_concurrent_access_refresh_uses_one_rotating_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refresh_requests = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal refresh_requests
                self.assertEqual(request.url.path, "/v1/auth/refresh")
                refresh_requests += 1
                time.sleep(0.05)
                return httpx.Response(
                    200,
                    json={
                        "access_token": "new-access-token",
                        "refresh_token": "new-refresh-token",
                        "expires_in": 300,
                        "user_id": "user-1",
                        "device_id": "mac-1",
                    },
                )

            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(handler),
            )
            relay.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="expired-access-token",
                    refresh_token="old-refresh-token",
                    access_expires_at="2000-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                states = list(executor.map(lambda _: relay.ensure_access_token(), range(2)))

            self.assertEqual(refresh_requests, 1)
            self.assertEqual(
                [state.access_token for state in states],
                ["new-access-token", "new-access-token"],
            )

    def test_reports_http_status_when_error_page_is_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(503, text="upstream unavailable")
                ),
            )

            with self.assertRaisesRegex(RelayHttpError, "HTTP 503"):
                relay._request("GET", "https://relay.test/health")

    def test_refresh_rejection_keeps_structured_auth_failure_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        401,
                        json={"detail": "Refresh token is invalid or expired"},
                    )
                ),
            )

            with self.assertRaises(RelayHttpError) as raised:
                relay._request("POST", "https://relay.test/v1/auth/refresh")

            self.assertEqual(raised.exception.status_code, 401)
            self.assertEqual(raised.exception.request_path, "/v1/auth/refresh")
            self.assertTrue(raised.exception.refresh_token_rejected)

    def test_rejects_non_json_success_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, text="not json")
                ),
            )

            with self.assertRaisesRegex(RelayHttpError, "invalid JSON"):
                relay._request("GET", "https://relay.test/health")

    def test_pairing_secret_stays_local_and_verified_device_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            android = DeviceKeyPair.generate()
            observed_enrollment: dict = {}
            relay: RelayHttpClient

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/v1/mac/enroll":
                    observed_enrollment.update(json.loads(request.content))
                    return httpx.Response(
                        200,
                        json={
                            "mac_device_id": "mac-1",
                            "pairing_id": "pair-1",
                            "poll_token": "poll-token",
                            "expires_at": "2999-01-01T00:00:00+00:00",
                        },
                    )
                state = relay.store.load()
                assert state is not None
                return httpx.Response(
                    200,
                    json={
                        "status": "approved",
                        "tokens": {
                            "access_token": "access-token",
                            "refresh_token": "refresh-token",
                            "expires_in": 300,
                            "user_id": "user-1",
                            "device_id": "mac-1",
                        },
                        "android_device_id": "android-1",
                        "android_public_key": android.public_key,
                        "pairing_proof": pairing_proof(
                            state.pairing_secret,
                            pairing_id=state.pairing_id,
                            mac_device_id=state.mac_device_id,
                            mac_public_key=relay.keys.public_key,
                            android_device_id="android-1",
                            android_public_key=android.public_key,
                        ),
                    },
                )

            relay = RelayHttpClient(
                state_dir,
                transport=httpx.MockTransport(handler),
            )
            enrolled = relay.enroll(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
                device_name="Mac",
            )
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(enrolled["pairing_uri"]).query
            )
            state = relay.store.load()
            assert state is not None

            self.assertNotIn("pairing_secret", observed_enrollment)
            self.assertNotIn("approval_token", observed_enrollment)
            self.assertEqual(
                observed_enrollment["pairing_commitment"],
                pairing_commitment(query["secret"][0]),
            )
            self.assertEqual(
                observed_enrollment["approval_commitment"],
                pairing_commitment(query["auth"][0]),
            )
            self.assertEqual(query["mac_key"], [relay.keys.public_key])

            paired = relay.poll_pairing()
            saved = relay.store.load()

            self.assertTrue(paired["paired"])
            assert saved is not None
            self.assertEqual(saved.pairing_secret, "")
            self.assertEqual(saved.pairing_uri, "")
            self.assertNotIn(query["secret"][0], (state_dir / "relay.json").read_text())
            self.assertNotIn(query["auth"][0], (state_dir / "relay.json").read_text())
            self.assertEqual(
                saved.trusted_device_keys,
                {"android-1": android.public_key},
            )

    def test_rejects_pairing_proof_that_does_not_match_android_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            android = DeviceKeyPair.generate()

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/v1/mac/enroll":
                    return httpx.Response(
                        200,
                        json={
                            "mac_device_id": "mac-1",
                            "pairing_id": "pair-1",
                            "poll_token": "poll-token",
                            "expires_at": "2999-01-01T00:00:00+00:00",
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "approved",
                        "tokens": {},
                        "android_device_id": "android-1",
                        "android_public_key": android.public_key,
                        "pairing_proof": base64.b64encode(b"x" * 32).decode(),
                    },
                )

            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(handler),
            )
            relay.enroll(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
            )

            with self.assertRaisesRegex(RelayHttpError, "proof is invalid"):
                relay.poll_pairing()

            saved = relay.store.load()
            assert saved is not None
            self.assertFalse(saved.paired)
            self.assertEqual(saved.trusted_device_keys, {})

    def test_approved_pairing_without_credentials_is_not_reported_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={"status": "approved"},
                    )
                ),
            )
            relay.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    pairing_id="pair-1",
                    poll_token="poll-token",
                    pairing_secret="secret",
                )
            )

            with self.assertRaisesRegex(RelayHttpError, "approval is incomplete"):
                relay.poll_pairing(expected_pairing_id="pair-1")

            saved = relay.store.load()
            assert saved is not None
            self.assertEqual(saved.pairing_id, "pair-1")
            self.assertFalse(saved.paired)

    def test_forget_clears_local_state_when_relay_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def handler(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("offline", request=request)

            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(handler),
            )
            relay.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access-token",
                    refresh_token="refresh-token",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )

            self.assertTrue(relay.forget())
            self.assertIsNone(relay.store.load())

    def test_pairing_creation_reuses_a_valid_code_and_keeps_existing_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requests = 0

            def handler(_request: httpx.Request) -> httpx.Response:
                nonlocal requests
                requests += 1
                return httpx.Response(
                    200,
                    json={
                        "mac_device_id": "mac-1",
                        "pairing_id": f"pair-{requests}",
                        "poll_token": f"poll-{requests}",
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    },
                )

            relay = RelayHttpClient(
                Path(tmp),
                transport=httpx.MockTransport(handler),
            )
            relay.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access-token",
                    refresh_token="refresh-token",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                    trusted_device_keys={"android-1": "public-key"},
                    sequence=9,
                )
            )

            first = relay.create_pairing(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
            )
            reused = relay.create_pairing(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
            )
            replaced = relay.create_pairing(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
                force=True,
            )

            self.assertEqual(requests, 2)
            self.assertEqual(first["pairing_id"], reused["pairing_id"])
            self.assertNotEqual(first["pairing_id"], replaced["pairing_id"])
            self.assertTrue(replaced["paired"])
            saved = relay.store.load()
            assert saved is not None
            self.assertEqual(saved.access_token, "access-token")
            self.assertEqual(saved.trusted_device_keys, {"android-1": "public-key"})
            self.assertEqual(saved.sequence, 9)


class RelayConfigurationTests(unittest.TestCase):
    def test_configuration_is_owner_only_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.json"
            store = RelayConfigurationStore(path)
            saved = store.configure(
                base_url="https://relay.test/",
                enrollment_token="e" * 32,
                device_name="Studio Mac",
            )

            self.assertEqual(saved.base_url, "https://relay.test")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = store.load()
            assert loaded is not None
            self.assertEqual(loaded.enrollment_token, "e" * 32)

    def test_machine_enrollment_environment_supersedes_legacy_name(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PCR_RELAY_MACHINE_ENROLLMENT_TOKEN": "m" * 32,
                "PCR_RELAY_MAC_ENROLLMENT_TOKEN": "l" * 32,
            },
            clear=False,
        ):
            self.assertEqual(environment_enrollment_token(), "m" * 32)

    def test_pair_device_prints_qr_and_waits_for_mutual_approval(self) -> None:
        class Relay:
            def create_pairing(self, **_kwargs: object) -> dict[str, object]:
                return {
                    "pairing_id": "pair-one",
                    "pairing_uri": "pcr://pair?pairing=one",
                }

            def poll_pairing(
                self,
                *,
                expected_pairing_id: str | None = None,
            ) -> dict[str, object]:
                return {
                    "paired": True,
                    "pairing_id": expected_pairing_id,
                    "pairing_status": "approved",
                }

        with tempfile.TemporaryDirectory() as tmp:
            configuration = RelayConfigurationStore(Path(tmp) / "server.json")
            configuration.configure(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
                device_name="Linux Workstation",
            )
            output = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                mock.patch(
                    "parallel_codex_runner_core.remote.daemon._print_pairing_qr"
                ) as qr,
            ):
                result = _pair_device(
                    Relay(),  # type: ignore[arg-type]
                    configuration,
                    wait=True,
                    timeout=30,
                )

        self.assertEqual(result, 0)
        qr.assert_called_once_with("pcr://pair?pairing=one")
        self.assertIn("Paired successfully", output.getvalue())

    def test_pair_device_ignores_existing_access_until_new_code_is_approved(self) -> None:
        class Relay:
            polls = 0

            def create_pairing(self, **_kwargs: object) -> dict[str, object]:
                return {
                    "pairing_id": "pair-new",
                    "pairing_uri": "pcr://pair?pairing=new",
                    "paired": True,
                }

            def poll_pairing(
                self,
                *,
                expected_pairing_id: str | None = None,
            ) -> dict[str, object]:
                self.polls += 1
                return {
                    "paired": True,
                    "pairing_id": expected_pairing_id,
                    "pairing_status": "pending" if self.polls == 1 else "expired",
                }

        with tempfile.TemporaryDirectory() as tmp:
            configuration = RelayConfigurationStore(Path(tmp) / "server.json")
            configuration.configure(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
                device_name="Linux Workstation",
            )
            with (
                mock.patch(
                    "parallel_codex_runner_core.remote.daemon._print_pairing_qr"
                ),
                mock.patch("parallel_codex_runner_core.remote.daemon.time.sleep"),
                self.assertRaisesRegex(RelayHttpError, "expired"),
            ):
                _pair_device(
                    Relay(),  # type: ignore[arg-type]
                    configuration,
                    wait=True,
                    timeout=30,
                )


class RelayBridgeTests(unittest.TestCase):
    def test_link_status_remains_readable_during_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            path = state_dir / "relay-link.json"
            path.write_text(
                json.dumps({"state": "connected", "since": "before"}),
                encoding="utf-8",
            )
            writer = RelayLinkStatus(state_dir)
            reader = RelayLinkStatus(state_dir)
            write_started = threading.Event()
            allow_write = threading.Event()
            original_write_text = Path.write_text

            def slow_write_text(
                target: Path,
                data: str,
                *args: object,
                **kwargs: object,
            ) -> int:
                original_write_text(target, "{", encoding="utf-8")
                write_started.set()
                self.assertTrue(allow_write.wait(timeout=1.0))
                return original_write_text(target, data, *args, **kwargs)

            with mock.patch.object(Path, "write_text", slow_write_text):
                thread = threading.Thread(
                    target=writer.update,
                    args=("connecting", "offline"),
                )
                thread.start()
                self.assertTrue(write_started.wait(timeout=1.0))
                self.assertEqual(reader.load()["state"], "connected")
                allow_write.set()
                thread.join(timeout=1.0)
                self.assertFalse(thread.is_alive())

            self.assertEqual(reader.load()["state"], "connecting")

    def test_first_request_completes_approved_pending_pairing(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        class PairingBridge(RelayBridge):
            async def _refresh_device_keys(self) -> None:
                state = self.http.store.load()
                self._device_keys = (
                    dict(state.trusted_device_keys) if state is not None else {}
                )

        with tempfile.TemporaryDirectory() as tmp:
            bridge = PairingBridge(Path(tmp), Executor())
            android_key = DeviceKeyPair.generate().public_key
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    pairing_id="pair-1",
                    poll_token="poll-1",
                    pairing_secret="secret",
                )
            )
            polls: list[str | None] = []

            def complete_pairing(*, expected_pairing_id: str | None = None) -> dict:
                polls.append(expected_pairing_id)
                state = bridge.http.store.load()
                assert state is not None
                state.trusted_device_keys["android-new"] = android_key
                state.pairing_id = ""
                state.poll_token = ""
                bridge.http.store.save(state)
                return {"pairing_status": "approved"}

            bridge.http.poll_pairing = complete_pairing  # type: ignore[method-assign]

            resolved = asyncio.run(bridge._peer_key("android-new"))

            self.assertEqual(resolved, android_key)
            self.assertEqual(polls, ["pair-1"])

    def test_connected_bridge_periodically_prunes_revoked_devices(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        class RefreshingBridge(RelayBridge):
            DEVICE_KEY_REFRESH_INTERVAL_SECONDS = 0.01

            def __init__(self, path: Path) -> None:
                super().__init__(path, Executor())
                self.refreshes = 0
                self.refreshed = asyncio.Event()

            async def _refresh_device_keys(self) -> None:
                self.refreshes += 1
                self.refreshed.set()

        class WebSocket:
            async def recv(self) -> str:
                await asyncio.sleep(10)
                return ""

            async def close(self, **_kwargs: object) -> None:
                return None

        async def exercise(path: Path) -> None:
            bridge = RefreshingBridge(path)
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access",
                    refresh_token="refresh",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )
            loop = asyncio.create_task(bridge._connected_loop(WebSocket()))
            await asyncio.wait_for(bridge.refreshed.wait(), timeout=1)
            bridge._stop.set()
            await asyncio.wait_for(loop, timeout=2)
            self.assertGreaterEqual(bridge.refreshes, 1)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_invalid_refresh_wait_is_released_by_repairing_state(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            bridge = RelayBridge(Path(tmp), Executor())
            state = bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access",
                    refresh_token="refresh",
                    access_expires_at="2000-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )
            previous_updated_at = state.updated_at
            state.access_token = "new-access"
            bridge.http.store.save(state)

            started = time.monotonic()
            bridge._wait_for_relay_state_change(
                previous_updated_at,
                timeout=5,
                poll_interval=0.01,
            )

            self.assertLess(time.monotonic() - started, 0.5)

    def test_mobile_can_manage_workspace_and_live_synthesis_settings(self) -> None:
        self.assertTrue(
            {
                "workspaces.settings.get",
                "workspaces.settings.update",
                "settings.defaults.get",
                "settings.defaults.update",
                "runs.update_synthesis",
                "runs.recover_finalization",
                "followups.select_agent",
                "conversations.rename",
                "prompt_history.list",
                "prompt_history.append",
                "relay.status",
                "relay.pairing_create",
                "relay.poll",
            }.issubset(RelayBridge.REMOTE_ACTIONS)
        )

    def test_remote_controller_cannot_clear_or_reenroll_a_machine(self) -> None:
        self.assertNotIn("relay.clear", RelayBridge.REMOTE_ACTIONS)
        self.assertNotIn("relay.enroll", RelayBridge.REMOTE_ACTIONS)

    def test_remote_capabilities_expose_only_relay_actions(self) -> None:
        response = RelayBridge._remote_capabilities(
            {
                "ok": True,
                "body": {
                    "transport": "local",
                    "actions": [
                        "relay.clear",
                        "system.health",
                        "conversations.list",
                        "turns.get",
                        "turns.start",
                    ],
                    "mutating_actions": [
                        "relay.clear",
                        "turns.start",
                    ],
                },
            }
        )

        body = response["body"]
        self.assertEqual(body["transport"], "relay")
        self.assertNotIn("relay.clear", body["actions"])
        self.assertIn("system.capabilities", body["actions"])
        self.assertIn("system.health", body["actions"])
        self.assertIn("conversations.list", body["actions"])
        self.assertIn("turns.get", body["actions"])
        self.assertIn("turns.start", body["actions"])
        self.assertEqual(body["mutating_actions"], ["turns.start"])

    def test_response_waits_for_relay_acceptance_before_finishing(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        class WebSocket:
            def __init__(self) -> None:
                self.sent: list[dict] = []
                self.sent_event = asyncio.Event()

            async def send(self, value: str) -> None:
                self.sent.append(json.loads(value))
                self.sent_event.set()

        async def exercise(bridge: RelayBridge, socket: WebSocket) -> None:
            pending: dict[str, asyncio.Future[None]] = {}
            task = asyncio.create_task(
                bridge._send_response(
                    socket,
                    target_device_id="android-1",
                    peer_public_key=DeviceKeyPair.generate().public_key,
                    response={"ok": True},
                    pending_submissions=pending,
                )
            )
            await socket.sent_event.wait()
            message_id = socket.sent[0]["message_id"]
            self.assertFalse(task.done())
            pending[message_id].set_result(None)
            await task
            self.assertEqual(pending, {})

        with tempfile.TemporaryDirectory() as tmp:
            bridge = RelayBridge(Path(tmp), Executor())
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                )
            )
            asyncio.run(exercise(bridge, WebSocket()))

    def test_decrypts_remote_request_and_encrypts_correlated_response(self) -> None:
        class Executor:
            def __init__(self) -> None:
                self.requests: list[dict] = []

            def execute(self, payload: dict) -> dict:
                self.requests.append(dict(payload))
                return {"ok": True, "request_id": payload["request_id"]}

        class WebSocket:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            async def send(self, value: str) -> None:
                self.sent.append(json.loads(value))

        with tempfile.TemporaryDirectory() as tmp:
            executor = Executor()
            bridge = RelayBridge(Path(tmp), executor)
            android = DeviceKeyPair.generate()
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access",
                    refresh_token="refresh",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )
            bridge._device_keys = {"android-1": android.public_key}
            metadata = {
                "message_id": "incoming-1",
                "sender_device_id": "android-1",
                "target_device_id": "mac-1",
                "sequence": 1,
            }
            ciphertext = android.encrypt(
                bridge.http.keys.public_key,
                {
                    "type": "request",
                    "request": {
                        "protocol_version": 1,
                        "request_id": "request-1",
                        "command_id": "command-1",
                        "client_id": "forged-client",
                        "action": "system.health",
                        "body": {},
                    },
                },
                **metadata,
            )
            socket = WebSocket()

            asyncio.run(
                bridge._handle_envelope(
                    socket,
                    {"type": "envelope", "ciphertext": ciphertext, **metadata},
                )
            )

            self.assertEqual(executor.requests[0]["client_id"], "android-1")
            response_wire = socket.sent[0]
            response = android.decrypt(
                bridge.http.keys.public_key,
                response_wire["ciphertext"],
                message_id=response_wire["message_id"],
                sender_device_id="mac-1",
                target_device_id="android-1",
                sequence=response_wire["sequence"],
            )
            self.assertEqual(response["response"]["request_id"], "request-1")
            self.assertEqual(socket.sent[1], {"type": "ack", "message_id": "incoming-1"})

            forbidden_metadata = {
                **metadata,
                "message_id": "incoming-2",
                "sequence": 2,
            }
            forbidden = android.encrypt(
                bridge.http.keys.public_key,
                {
                    "type": "request",
                    "request": {
                        "protocol_version": 1,
                        "request_id": "request-2",
                        "command_id": "command-2",
                        "action": "relay.clear",
                        "body": {},
                    },
                },
                **forbidden_metadata,
            )
            asyncio.run(
                bridge._handle_envelope(
                    socket,
                    {
                        "type": "envelope",
                        "ciphertext": forbidden,
                        **forbidden_metadata,
                    },
                )
            )
            denied_wire = socket.sent[2]
            denied = android.decrypt(
                bridge.http.keys.public_key,
                denied_wire["ciphertext"],
                message_id=denied_wire["message_id"],
                sender_device_id="mac-1",
                target_device_id="android-1",
                sequence=denied_wire["sequence"],
            )
            self.assertFalse(denied["response"]["ok"])
            self.assertEqual(
                denied["response"]["error"]["code"],
                "remote_action_forbidden",
            )
            self.assertEqual(len(executor.requests), 1)

    def test_malformed_or_wrong_target_envelope_is_acknowledged(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                raise AssertionError("malformed messages must not reach the executor")

        class WebSocket:
            def __init__(self) -> None:
                self.sent: list[dict] = []

            async def send(self, value: str) -> None:
                self.sent.append(json.loads(value))

        async def exercise(bridge: RelayBridge, socket: WebSocket) -> None:
            await bridge._handle_envelope(
                socket,
                {
                    "type": "envelope",
                    "message_id": "malformed-sequence",
                    "sender_device_id": "android-1",
                    "target_device_id": "mac-1",
                    "sequence": "not-a-number",
                    "ciphertext": "invalid",
                },
            )
            await bridge._handle_envelope(
                socket,
                {
                    "type": "envelope",
                    "message_id": "wrong-target",
                    "sender_device_id": "android-1",
                    "target_device_id": "mac-2",
                    "sequence": 1,
                    "ciphertext": "invalid",
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            bridge = RelayBridge(Path(tmp), Executor())
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                )
            )
            socket = WebSocket()

            asyncio.run(exercise(bridge, socket))

            self.assertEqual(
                socket.sent,
                [
                    {"type": "ack", "message_id": "malformed-sequence"},
                    {"type": "ack", "message_id": "wrong-target"},
                ],
            )

    def test_slow_remote_operation_does_not_block_later_requests(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        class ConcurrentBridge(RelayBridge):
            def __init__(self, path: Path) -> None:
                super().__init__(path, Executor())
                self.slow_started = asyncio.Event()
                self.release_slow = asyncio.Event()
                self.fast_finished = asyncio.Event()

            async def _handle_envelope(
                self,
                _websocket: object,
                message: dict,
                *,
                send_lock: asyncio.Lock | None = None,
                pending_submissions: dict[str, asyncio.Future[None]] | None = None,
            ) -> None:
                del send_lock, pending_submissions
                if message["request"] == "slow":
                    self.slow_started.set()
                    await self.release_slow.wait()
                else:
                    self.fast_finished.set()

        class WebSocket:
            def __init__(self) -> None:
                self.messages = [
                    json.dumps({"type": "envelope", "request": "slow"}),
                    json.dumps({"type": "envelope", "request": "fast"}),
                ]

            async def recv(self) -> str:
                if self.messages:
                    return self.messages.pop(0)
                await asyncio.sleep(10)
                return ""

            async def close(self, **_kwargs: object) -> None:
                return None

        async def exercise(path: Path) -> None:
            bridge = ConcurrentBridge(path)
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access",
                    refresh_token="refresh",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )
            loop = asyncio.create_task(bridge._connected_loop(WebSocket()))
            await asyncio.wait_for(bridge.slow_started.wait(), timeout=1)
            await asyncio.wait_for(bridge.fast_finished.wait(), timeout=1)
            self.assertFalse(bridge.release_slow.is_set())
            bridge.release_slow.set()
            bridge._stop.set()
            await asyncio.wait_for(loop, timeout=2)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_acceptance_messages_are_processed_at_the_inflight_limit(self) -> None:
        class Executor:
            def execute(self, _payload: dict) -> dict:
                return {}

        class SaturatedBridge(RelayBridge):
            def __init__(self, path: Path) -> None:
                super().__init__(path, Executor())
                self.started = 0
                self.finished = 0
                self.all_started = asyncio.Event()
                self.all_finished = asyncio.Event()

            async def _handle_envelope(
                self,
                websocket: object,
                _message: dict,
                *,
                send_lock: asyncio.Lock | None = None,
                pending_submissions: dict[str, asyncio.Future[None]] | None = None,
            ) -> None:
                del send_lock
                assert pending_submissions is not None
                self.started += 1
                if self.started == self.MAX_INFLIGHT_REQUESTS:
                    self.all_started.set()
                await self.all_started.wait()
                message_id = f"response-{self.started}-{id(asyncio.current_task())}"
                accepted = asyncio.get_running_loop().create_future()
                pending_submissions[message_id] = accepted
                await websocket.send(  # type: ignore[attr-defined]
                    json.dumps({"type": "response", "message_id": message_id})
                )
                await accepted
                self.finished += 1
                if self.finished == self.MAX_INFLIGHT_REQUESTS:
                    self.all_finished.set()

        class WebSocket:
            def __init__(self, count: int) -> None:
                self.incoming: asyncio.Queue[str] = asyncio.Queue()
                for index in range(count):
                    self.incoming.put_nowait(
                        json.dumps({"type": "envelope", "index": index})
                    )

            async def recv(self) -> str:
                return await self.incoming.get()

            async def send(self, value: str) -> None:
                message_id = json.loads(value)["message_id"]
                await self.incoming.put(
                    json.dumps({"type": "accepted", "message_id": message_id})
                )

            async def close(self, **_kwargs: object) -> None:
                return None

        async def exercise(path: Path) -> None:
            bridge = SaturatedBridge(path)
            bridge.http.store.save(
                RelayState(
                    base_url="https://relay.test",
                    mac_device_id="mac-1",
                    device_name="Mac",
                    access_token="access",
                    refresh_token="refresh",
                    access_expires_at="2999-01-01T00:00:00+00:00",
                    user_id="user-1",
                )
            )
            loop = asyncio.create_task(
                bridge._connected_loop(WebSocket(bridge.MAX_INFLIGHT_REQUESTS))
            )
            await asyncio.wait_for(bridge.all_finished.wait(), timeout=2)
            bridge._stop.set()
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))


class FileSystemBrowserTests(unittest.TestCase):
    def test_volume_enumeration_timeout_does_not_wait_for_blocked_worker(self) -> None:
        blocked = threading.Event()
        release = threading.Timer(0.4, blocked.set)

        def blocked_enumeration(_parent: Path) -> list[Path]:
            blocked.wait()
            return []

        release.start()
        started = time.monotonic()
        try:
            with mock.patch(
                "parallel_codex_runner_core.remote.filesystem.VOLUME_ENUM_TIMEOUT_SECONDS",
                0.01,
            ), mock.patch(
                "parallel_codex_runner_core.remote.filesystem._list_subdirectories",
                side_effect=blocked_enumeration,
            ):
                self.assertEqual(
                    FileSystemBrowser._enumerate_volumes_with_timeout(Path("/")),
                    [],
                )
        finally:
            blocked.set()
            release.cancel()
            release.join()

        self.assertLess(time.monotonic() - started, 0.2)

    def test_root_label_matches_the_machine_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = FileSystemBrowser(home=Path(tmp))
            for system, expected in (
                ("Darwin", "Macintosh HD"),
                ("Linux", "File System"),
            ):
                with self.subTest(system=system), mock.patch(
                    "parallel_codex_runner_core.remote.filesystem.platform.system",
                    return_value=system,
                ):
                    root = next(
                        item for item in browser.roots()["roots"]
                        if item["path"] == "/"
                    )
                    self.assertEqual(root["name"], expected)

    def test_browses_outside_home_and_pages_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            outside = root / "projects"
            home.mkdir()
            outside.mkdir()
            (outside / "beta").mkdir()
            (outside / "alpha").mkdir()
            (outside / "notes.txt").write_text("hello", encoding="utf-8")
            (outside / ".private").mkdir()
            browser = FileSystemBrowser(home=home)

            first = browser.list_directory(outside, limit=2)
            second = browser.list_directory(
                outside,
                cursor=first["next_cursor"],
                limit=2,
            )

            self.assertEqual(first["path"], str(outside.resolve()))
            self.assertEqual([item["name"] for item in first["entries"]], ["alpha", "beta"])
            self.assertEqual([item["name"] for item in second["entries"]], ["notes.txt"])
            self.assertNotIn(".private", [item["name"] for item in first["entries"]])

            visible = browser.list_directory(outside, include_hidden=True)
            self.assertIn(".private", [item["name"] for item in visible["entries"]])

    def test_sorts_by_modification_time_before_paging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            projects = root / "projects"
            home.mkdir()
            projects.mkdir()
            oldest = projects / "alpha"
            newest = projects / "zeta"
            middle = projects / "middle"
            for directory in (oldest, newest, middle):
                directory.mkdir()
            os.utime(oldest, (100, 100))
            os.utime(middle, (200, 200))
            os.utime(newest, (300, 300))
            browser = FileSystemBrowser(home=home)

            first = browser.list_directory(
                projects,
                directories_only=True,
                sort="modified_desc",
                limit=2,
            )
            second = browser.list_directory(
                projects,
                directories_only=True,
                sort="modified_desc",
                cursor=first["next_cursor"],
                limit=2,
            )

            self.assertEqual(first["sort"], "modified_desc")
            self.assertEqual(
                [item["name"] for item in first["entries"]],
                ["zeta", "middle"],
            )
            self.assertEqual(
                [item["name"] for item in second["entries"]],
                ["alpha"],
            )

    def test_rejects_unknown_directory_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            browser = FileSystemBrowser(home=root)

            with self.assertRaisesRegex(RemoteFileSystemError, "Unsupported"):
                browser.list_directory(root, sort="recent-ish")

    def test_creates_one_folder_in_the_current_remote_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "projects"
            parent.mkdir()
            browser = FileSystemBrowser(home=root)

            created = browser.create_directory(parent, "New Project")

            self.assertTrue((parent / "New Project").is_dir())
            self.assertEqual(created["path"], str((parent / "New Project").resolve()))
            self.assertEqual(created["parent"], str(parent.resolve()))
            self.assertTrue(created["created"])

    def test_rejects_unsafe_or_existing_folder_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing").mkdir()
            browser = FileSystemBrowser(home=root)

            for name in ("", ".", "..", "nested/name", "existing"):
                with self.subTest(name=name):
                    with self.assertRaises(RemoteFileSystemError):
                        browser.create_directory(root, name)


class WorkspaceRegistryTests(unittest.TestCase):
    def test_manages_multiple_workspaces_without_an_access_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project_a = root / "disk-a" / "project-a"
            project_b = root / "disk-b" / "project-b"
            home.mkdir()
            project_a.mkdir(parents=True)
            project_b.mkdir(parents=True)
            browser = FileSystemBrowser(home=home)
            registry = WorkspaceRegistry(root / "state" / "workspaces.json", browser)

            first = registry.register(str(project_a), name="Project A")
            second = registry.register(
                str(project_b),
                name="Project B",
                expected_revision=first["revision"],
            )
            listed = registry.list()

            self.assertEqual(len(listed["workspaces"]), 2)
            self.assertEqual(
                listed["selected_workspace_id"],
                second["workspace"]["workspace_id"],
            )
            self.assertEqual(
                {item["path"] for item in listed["workspaces"]},
                {str(project_a.resolve()), str(project_b.resolve())},
            )

            with self.assertRaisesRegex(WorkspaceRegistryError, "expected revision"):
                registry.remove(
                    first["workspace"]["workspace_id"],
                    expected_revision=0,
                )

    def test_selecting_workspace_does_not_reorder_the_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = root / "zulu"
            second_path = root / "alpha"
            first_path.mkdir()
            second_path.mkdir()
            registry = WorkspaceRegistry(root / "workspaces.json")

            first = registry.register(str(first_path), name="Zulu")
            registry.register(str(second_path), name="Alpha")
            before = registry.list()
            before_ids = [item["workspace_id"] for item in before["workspaces"]]

            selected = registry.select(
                first["workspace"]["workspace_id"],
                expected_revision=before["revision"],
            )
            after = registry.list()

            self.assertEqual(
                selected["workspace"]["workspace_id"],
                first["workspace"]["workspace_id"],
            )
            self.assertEqual(
                after["selected_workspace_id"],
                first["workspace"]["workspace_id"],
            )
            self.assertEqual(
                [item["workspace_id"] for item in after["workspaces"]],
                before_ids,
            )

    def test_context_update_is_idempotent_and_can_require_the_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry = WorkspaceRegistry(root / "workspaces.json")
            registered = registry.register(str(workspace))
            workspace_id = registered["workspace"]["workspace_id"]

            updated = registry.update_context(
                workspace_id,
                run_id="run-1",
                resume_session_id="session-1",
            )
            repeated = registry.update_context(
                workspace_id,
                run_id="run-1",
                resume_session_id="session-1",
            )
            stale = registry.update_context(
                workspace_id,
                run_id="run-old",
                resume_session_id="session-old",
                expected_run_id="run-old",
            )

            record = registry.require(workspace_id)
            self.assertEqual(repeated, updated)
            self.assertIsNone(stale)
            self.assertEqual(record.last_run_id, "run-1")
            self.assertEqual(record.last_resume_session_id, "session-1")


class CommandJournalTests(unittest.TestCase):
    def test_replays_completed_mutation_without_running_it_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = CommandJournal(Path(tmp))
            request = RemoteRequest(
                action="workspaces.add",
                body={"path": "/tmp"},
                client_id="phone",
                command_id="command-1",
            )
            calls = 0

            def execute() -> dict:
                nonlocal calls
                calls += 1
                return {"revision": 1}

            first, replayed_first = journal.execute(request, execute)
            second, replayed_second = journal.execute(request, execute)

            self.assertEqual(first, second)
            self.assertFalse(replayed_first)
            self.assertTrue(replayed_second)
            self.assertEqual(calls, 1)

            changed = RemoteRequest(
                action="workspaces.add",
                body={"path": "/var"},
                client_id="phone",
                command_id="command-1",
            )
            with self.assertRaisesRegex(CommandJournalError, "different command"):
                journal.execute(changed, execute)

    def test_retryable_failure_can_reuse_the_same_command_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = CommandJournal(Path(tmp))
            request = RemoteRequest(
                action="runs.continue",
                body={"run_id": "run-1"},
                client_id="phone",
                command_id="continue-1",
            )
            calls = 0

            def execute() -> dict:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RemoteControllerError(
                        "pcr_operation_pending",
                        "Finalization is still running",
                        retryable=True,
                    )
                return {"run_id": "run-2"}

            with self.assertRaises(RemoteControllerError):
                journal.execute(request, execute)
            result, replayed = journal.execute(request, execute)

            self.assertEqual(result, {"run_id": "run-2"})
            self.assertFalse(replayed)
            self.assertEqual(calls, 2)

    def test_prunes_only_old_terminal_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            journal = CommandJournal(directory, retention_seconds=24 * 60 * 60)
            old = time.time() - (2 * 24 * 60 * 60)
            for name, status in (
                ("completed", "completed"),
                ("failed", "failed"),
                ("started", "started"),
            ):
                path = directory / f"{name}.json"
                path.write_text(json.dumps({"status": status}), encoding="utf-8")
                path.with_suffix(".lock").touch()
                os.utime(path, (old, old))

            removed = journal.prune(now=time.time())

            self.assertEqual(removed, 2)
            self.assertFalse((directory / "completed.json").exists())
            # Keep lock inodes stable for concurrent or cross-process replays.
            self.assertTrue((directory / "completed.lock").exists())
            self.assertFalse((directory / "failed.json").exists())
            self.assertTrue((directory / "started.json").exists())
            self.assertTrue((directory / "started.lock").exists())

    def test_long_running_journal_prunes_during_later_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            journal = CommandJournal(
                directory,
                retention_seconds=24 * 60 * 60,
                prune_interval_seconds=1,
            )
            expired = directory / "expired.json"
            expired.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            old = time.time() - (2 * 24 * 60 * 60)
            os.utime(expired, (old, old))
            journal._last_prune_monotonic = 0

            journal.execute(
                RemoteRequest(
                    action="workspaces.add",
                    body={"path": "/tmp"},
                    client_id="phone",
                    command_id="command-after-prune-interval",
                ),
                lambda: {"revision": 1},
            )

            self.assertFalse(expired.exists())


class DeviceRegistryTests(unittest.TestCase):
    def test_pairing_is_one_time_and_devices_can_be_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = DeviceRegistry(Path(tmp) / "devices.json")
            initial_mac_id = registry.list()["mac_endpoint_id"]
            pairing = registry.create_pairing()
            public_key = base64.b64encode(b"k" * 32).decode("ascii")

            completed = registry.complete_pairing(
                pairing["pairing_id"],
                pairing["secret"],
                user_id="wechat-union-id",
                device_name="Pixel",
                public_key=public_key,
            )

            self.assertEqual(registry.list()["mac_endpoint_id"], initial_mac_id)
            self.assertTrue(completed["device"]["active"])
            self.assertEqual(registry.list()["pending_pairings"], 0)
            with self.assertRaisesRegex(DeviceRegistryError, "missing or expired"):
                registry.complete_pairing(
                    pairing["pairing_id"],
                    pairing["secret"],
                    user_id="wechat-union-id",
                    device_name="Pixel",
                    public_key=public_key,
                )

            revoked = registry.revoke(completed["device"]["device_id"])
            self.assertFalse(revoked["device"]["active"])


class RemoteControllerTests(unittest.TestCase):
    def test_conversation_rename_resolves_legacy_run_without_modifying_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            run = {"run_id": "run-title", "workspace": str(workspace), "prompt": "Original prompt",
                   "status": "running", "conversation_id": "canonical-chat", "turn_id": "turn-title",
                   "config": {}, "agents": [], "created_at": "2026-09-19T00:00:00Z"}
            manager.get_run = mock.Mock(return_value=run)
            controller = RemoteController(state_dir=root / "state", manager=manager,
                                          browser=FileSystemBrowser(home=root))
            try:
                registered = controller.dispatch(RemoteRequest(action="workspaces.add", body={"path": str(workspace)}))
                workspace_id = registered["workspace"]["workspace_id"]
                response = controller.dispatch(RemoteRequest(action="conversations.rename", body={
                    "workspace_id": workspace_id, "conversation_id": "legacy-navigation-id",
                    "run_id": "run-title", "title": "PCR development",
                }))
                record = response["conversation"]
                self.assertEqual(record["conversation_id"], "canonical-chat")
                self.assertEqual(record["custom_title"], "PCR development")
                self.assertEqual(record["title_revision"], 1)
                self.assertEqual(run["prompt"], "Original prompt")
                refreshed = controller.conversations.synchronize_run(run, **controller._conversation_context(workspace_id))
                self.assertEqual(refreshed["conversation_snapshot"]["conversation"]["title"], "PCR development")
            finally:
                controller.close()

    def test_conversation_rename_validates_imported_session_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.resume_history = mock.Mock(return_value={"items": []})
            controller = RemoteController(state_dir=root / "state", manager=manager,
                                          browser=FileSystemBrowser(home=root))
            try:
                registered = controller.dispatch(RemoteRequest(action="workspaces.add", body={"path": str(workspace)}))
                workspace_id = registered["workspace"]["workspace_id"]
                response = controller.dispatch(RemoteRequest(action="conversations.rename", body={
                    "workspace_id": workspace_id, "conversation_id": "resume:session",
                    "resume_session_id": "session", "title": "Imported conversation",
                }))
                self.assertEqual(response["conversation"]["resume_session_id"], "session")
                manager.resume_history.assert_called_once_with(str(workspace.resolve()), "session", limit=1)
                manager.resume_history.side_effect = ValueError("Session belongs to another workspace")
                with self.assertRaisesRegex(RemoteControllerError, "another workspace"):
                    controller.dispatch(RemoteRequest(action="conversations.rename", body={
                        "workspace_id": workspace_id, "conversation_id": "resume:other",
                        "resume_session_id": "other", "title": "Must not be stored",
                    }))
                self.assertIsNone(controller.conversations.state.conversation("resume:other"))
            finally:
                controller.close()

    def test_workspace_delay_edit_updates_the_persisted_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            controller = RemoteController(
                state_dir=root / "state", manager=FakeRunManager(),
                browser=FileSystemBrowser(home=root),
            )
            try:
                registered = controller.dispatch(RemoteRequest(
                    action="workspaces.add", body={"path": str(workspace)},
                ))
                workspace_id = registered["workspace"]["workspace_id"]
                controller.follow_up_queues.replace(
                    workspace_id=workspace_id, run_id="run-1",
                    items=[_remote_follow_up_item("queued", "keep this prompt")],
                    selected_agent=1, reset_countdown=False,
                )
                controller.dispatch(RemoteRequest(
                    action="workspaces.settings.update",
                    body={"workspace_id": workspace_id, "follow_up_delay": 7},
                ))
                queue = controller.follow_up_queues.get_internal(workspace_id)
                self.assertEqual(queue["items"][0]["settings"]["follow_up_delay"], 7)
                self.assertEqual(queue["items"][0]["prompt"], "keep this prompt")
                generation = queue["generation"]
                controller.dispatch(RemoteRequest(
                    action="workspaces.settings.update",
                    body={"workspace_id": workspace_id, "agents": 3},
                ))
                self.assertEqual(controller.follow_up_queues.get_internal(workspace_id)["generation"], generation)
            finally:
                controller.close()

    def test_conversation_list_synchronizes_every_run_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            old_run = {
                "run_id": "run-old",
                "workspace": str(workspace),
                "prompt": "Older conversation",
                "status": "completed",
                "turn_id": "turn-old",
                "conversation_id": "conversation-old",
                "turn_index": 0,
                "created_at": "2026-08-01T00:00:00+00:00",
                "updated_at": "2026-08-01T00:00:00+00:00",
                "config": {},
                "agents": [],
            }
            manager.list_runs = mock.Mock(  # type: ignore[method-assign]
                side_effect=[
                    {
                        "runs": [],
                        "total": 101,
                        "next_cursor": "page-2",
                        "has_more": True,
                    },
                    {
                        "runs": [old_run],
                        "total": 101,
                        "next_cursor": "",
                        "has_more": False,
                    },
                ]
            )
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.dispatch(
                RemoteRequest(action="workspaces.add", body={"path": str(workspace)})
            )

            result = controller.dispatch(
                RemoteRequest(
                    action="conversations.list",
                    body={
                        "workspace_id": registered["workspace"]["workspace_id"],
                        "limit": 25,
                    },
                )
            )

            self.assertEqual(
                [item["conversation_id"] for item in result["conversations"]],
                ["conversation-old"],
            )
            self.assertEqual(manager.list_runs.call_count, 2)
            self.assertEqual(
                manager.list_runs.call_args_list[1].kwargs["cursor"],
                "page-2",
            )

    def test_workspace_file_action_reads_only_the_selected_agent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            file = candidate / "answer.md"
            file.write_text("mobile document", encoding="utf-8")
            manager = FakeRunManager()
            manager.get_run = mock.Mock(  # type: ignore[method-assign]
                return_value={
                    "run_id": "run-1",
                    "workspace": str(workspace),
                    "run_root": str(root / "run"),
                    "finalized_agent": None,
                    "synced_back": False,
                }
            )
            manager.get_agent = mock.Mock(  # type: ignore[method-assign]
                return_value={"workspace_dir": str(candidate)}
            )
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.dispatch(
                RemoteRequest(action="workspaces.add", body={"path": str(workspace)})
            )

            page = controller.dispatch(
                RemoteRequest(
                    action="workspace.file.read",
                    body={
                        "workspace_id": registered["workspace"]["workspace_id"],
                        "run_id": "run-1",
                        "agent": 2,
                        "reference": str(file),
                    },
                )
            )

            self.assertEqual(base64.b64decode(page["content_base64"]), b"mobile document")
            self.assertEqual(page["relative_path"], "answer.md")

    def test_source_links_do_not_load_runs_and_source_mode_reads_work_for_older_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            folder = workspace / "outputs" / "videos_60fps"
            folder.mkdir(parents=True)
            (workspace / "answer.md").write_text("source result", encoding="utf-8")
            manager = FakeRunManager()
            manager.get_run = mock.Mock(side_effect=RuntimeError("run has been removed"))
            manager.get_agent = mock.Mock(side_effect=RuntimeError("agent has been removed"))
            controller = RemoteController(state_dir=root / "state", manager=manager,
                                          browser=FileSystemBrowser(home=root))
            registered = controller.dispatch(RemoteRequest(action="workspaces.add", body={"path": str(workspace)}))
            identity = {"workspace_id": registered["workspace"]["workspace_id"], "run_id": "old-run", "agent": 1}
            item = controller.dispatch(RemoteRequest(action="workspace.item.resolve",
                body={**identity, "reference": str(folder)}))
            self.assertEqual(item["kind"], "directory")
            self.assertEqual(item["path"], str(folder.resolve()))
            page = controller.dispatch(RemoteRequest(action="workspace.file.read",
                body={**identity, "reference": str(workspace / "answer.md"), "literal_paths": True}))
            self.assertEqual(base64.b64decode(page["content_base64"]), b"source result")
            manager.get_run.assert_not_called()
            manager.get_agent.assert_not_called()
            manager.get_run = mock.Mock(return_value={"workspace": str(workspace),
                "run_root": str(root / "run"), "config": {"workspace_mode": "source"}})
            manager.get_agent = mock.Mock(return_value={"workspace_dir": str(workspace)})
            legacy = controller.dispatch(RemoteRequest(action="workspace.file.read",
                body={**identity, "reference": "answer.md"}))
            self.assertEqual(base64.b64decode(legacy["content_base64"]), b"source result")
            item = controller.dispatch(RemoteRequest(action="workspace.item.resolve",
                body={**identity, "reference": "outputs/videos_60fps"}))
            self.assertEqual(item["path"], str(folder.resolve()))

    def test_candidate_item_uses_its_origin_and_never_substitutes_a_synced_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "old-run" / "workspaces" / "agent_002"
            workspace.mkdir()
            candidate.mkdir(parents=True)
            (workspace / "answer.md").write_text("new source", encoding="utf-8")
            (candidate / "answer.md").write_text("old candidate", encoding="utf-8")
            manager = FakeRunManager()
            manager.get_run = mock.Mock(return_value={"workspace": str(workspace),
                "run_root": str(root / "old-run"), "status": "failed",
                "finalized_agent": 2, "synced_back": True})
            manager.get_agent = mock.Mock(return_value={"workspace_dir": str(candidate)})
            controller = RemoteController(state_dir=root / "state", manager=manager,
                                          browser=FileSystemBrowser(home=root))
            registered = controller.dispatch(RemoteRequest(action="workspaces.add", body={"path": str(workspace)}))
            body = {"workspace_id": registered["workspace"]["workspace_id"], "run_id": "old-run",
                    "agent": 2, "reference": "answer.md"}
            result = controller.dispatch(RemoteRequest(action="workspace.item.resolve", body=body))
            self.assertEqual(result["path"], str((candidate / "answer.md").resolve()))
            manager.get_run.assert_called_with("old-run", include_events=False)
            (candidate / "answer.md").unlink()
            candidate.rmdir()
            with self.assertRaisesRegex(RemoteControllerError, "no longer available"):
                controller.dispatch(RemoteRequest(action="workspace.item.resolve", body=body))
            other = root / "other"
            other.mkdir()
            manager.get_run = mock.Mock(return_value={"workspace": str(other)})
            with self.assertRaisesRegex(RemoteControllerError, "selected workspace"):
                controller.dispatch(RemoteRequest(action="workspace.item.resolve", body=body))

    def test_run_list_forwards_stable_cursor_and_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.list_runs = mock.Mock(  # type: ignore[method-assign]
                return_value={"runs": [], "total": 0, "has_more": False}
            )
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            result = controller.dispatch(
                RemoteRequest(
                    action="runs.list",
                    body={
                        "limit": 37,
                        "cursor": "opaque-cursor",
                        "max_bytes": 123_456,
                    },
                )
            )

            self.assertFalse(result["has_more"])
            manager.list_runs.assert_called_once_with(
                limit=37,
                cursor="opaque-cursor",
                max_bytes=123_456,
            )

    def test_run_list_forwards_workspace_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.list_runs = mock.Mock(  # type: ignore[method-assign]
                return_value={"runs": [], "total": 0, "has_more": False}
            )
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            controller.dispatch(
                RemoteRequest(
                    action="runs.list",
                    body={"workspace": "/tmp/project"},
                )
            )

            manager.list_runs.assert_called_once_with(
                limit=20,
                cursor="",
                max_bytes=DEFAULT_RUN_LIST_PAGE_BYTES,
                workspace="/tmp/project",
            )

    def test_feedback_is_scoped_per_remote_device_but_local_admin_sees_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            first = controller.dispatch(
                RemoteRequest(
                    action="feedback.submit",
                    client_id="phone-a",
                    body={
                        "client_feedback_id": "client-a",
                        "kind": "bug",
                        "description": "Phone A feedback",
                    },
                )
            )["feedback"]
            controller.dispatch(
                RemoteRequest(
                    action="feedback.submit",
                    client_id="phone-b",
                    body={
                        "client_feedback_id": "client-b",
                        "kind": "feature",
                        "description": "Phone B feedback",
                    },
                )
            )

            phone_items = controller.dispatch(
                RemoteRequest(action="feedback.list", client_id="phone-a")
            )
            admin_items = controller.dispatch(
                RemoteRequest(action="feedback.list", client_id="local-cli")
            )

            self.assertEqual(phone_items["total"], 1)
            self.assertEqual(phone_items["items"][0]["feedback_id"], first["feedback_id"])
            self.assertEqual(admin_items["total"], 2)
            with self.assertRaisesRegex(RemoteControllerError, "not found"):
                controller.dispatch(
                    RemoteRequest(
                        action="feedback.get",
                        client_id="phone-b",
                        body={"feedback_id": first["feedback_id"]},
                    )
                )

            fixed = controller.dispatch(
                RemoteRequest(
                    action="feedback.admin_update",
                    client_id="local-cli",
                    body={
                        "feedback_id": first["feedback_id"],
                        "status": "fixed",
                        "fixed_version": "0.1.92",
                    },
                )
            )["feedback"]
            self.assertEqual(fixed["status"], "fixed")
            with self.assertRaisesRegex(RemoteControllerError, "only on this machine"):
                controller.dispatch(
                    RemoteRequest(
                        action="feedback.admin_update",
                        client_id="phone-a",
                        body={
                            "feedback_id": first["feedback_id"],
                            "status": "confirmed",
                        },
                    )
                )

    def test_capabilities_describe_protocol_schemas_and_action_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )

            result = controller.dispatch(
                RemoteRequest(action="system.capabilities")
            )

            self.assertEqual(result["daemon_name"], "pcrd")
            self.assertTrue(result["daemon_version"])
            self.assertEqual(result["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(
                result["minimum_client_protocol_version"],
                MINIMUM_CLIENT_PROTOCOL_VERSION,
            )
            self.assertEqual(
                result["supported_protocol_versions"],
                list(SUPPORTED_PROTOCOL_VERSIONS),
            )
            self.assertEqual(
                result["transcript_schema_version"],
                TRANSCRIPT_SCHEMA_VERSION,
            )
            self.assertIn("runs.start", result["mutating_actions"])
            self.assertIn("followups.enqueue", result["mutating_actions"])
            self.assertIn("followups.select_agent", result["mutating_actions"])
            self.assertNotIn("system.health", result["mutating_actions"])
            self.assertTrue(result["platform"])
            self.assertTrue(result["platform_release"])
            self.assertEqual(result["filesystem_scope"], "user")
            self.assertTrue(result["supports_run_list_cursor"])
            self.assertTrue(result["supports_byte_bounded_pages"])
            self.assertEqual(result["maximum_agent_count"], 4_096)

    def test_follow_up_enqueue_appends_atomically_through_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "project"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.get_run = mock.Mock(  # type: ignore[attr-defined]
                return_value={"run_id": "run-1", "workspace": str(workspace)}
            )
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.dispatch(
                RemoteRequest(action="workspaces.add", body={"path": str(workspace)})
            )
            workspace_id = registered["workspace"]["workspace_id"]
            first = _remote_follow_up_item("message-1", "first")
            second = _remote_follow_up_item("message-2", "second")

            controller.dispatch(
                RemoteRequest(
                    action="followups.enqueue",
                    body={
                        "workspace_id": workspace_id,
                        "run_id": "run-1",
                        "item": first,
                        "selected_agent": 1,
                    },
                )
            )
            result = controller.dispatch(
                RemoteRequest(
                    action="followups.enqueue",
                    body={
                        "workspace_id": workspace_id,
                        "run_id": "run-1",
                        "item": second,
                        "selected_agent": 2,
                    },
                )
            )

            self.assertEqual(
                [item["id"] for item in result["queue"]["items"]],
                ["message-1", "message-2"],
            )
            self.assertIsNone(result["queue"]["selected_agent"])
            controller.close()

    def test_agent_total_guard_allows_large_batches_without_binding_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )

            controller.dispatch(
                RemoteRequest(
                    action="runs.start",
                    body={
                        "workspace": str(workspace),
                        "prompt": "large candidate batch",
                        "num_agents": 64,
                        "max_parallel": 4,
                        "synthesis_agents": 0,
                    },
                )
            )
            self.assertEqual(manager.started[-1]["num_agents"], 64)
            self.assertEqual(manager.started[-1]["max_parallel"], 4)

            with self.assertRaises(RemoteControllerError) as raised:
                controller.dispatch(
                    RemoteRequest(
                        action="runs.start",
                        body={
                            "workspace": str(workspace),
                            "prompt": "too many candidates",
                            "num_agents": 4_097,
                            "synthesis_agents": 0,
                        },
                    )
                )
            self.assertIn("must not exceed 4096", raised.exception.message)

    def test_remote_clients_use_disk_capacity_instead_of_plugin_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(state_dir=Path(tmp) / "remote")
            try:
                self.assertFalse(controller.manager.enforce_storage_quota)
                self.assertTrue(controller.manager.retains_history)
            finally:
                controller.close()

    def test_removes_explicit_workspace_without_a_registry_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.registry.register(str(workspace))
            workspace_id = str(registered["workspace"]["workspace_id"])
            controller.registry.select(workspace_id)

            result = controller.dispatch(
                RemoteRequest(
                    action="workspaces.remove",
                    body={"workspace_id": workspace_id},
                )
            )

            self.assertEqual(result["removed_workspace_id"], workspace_id)

    def test_workspace_does_not_advertise_an_expired_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root / "remote", manager=manager)
            try:
                registered = controller.registry.register(str(workspace))
                workspace_id = registered["workspace"]["workspace_id"]
                controller.registry.update_context(
                    workspace_id, run_id="expired-run", resume_session_id="durable-session"
                )
                result = controller._workspace_registry_payload()["workspaces"][0]
                self.assertEqual(result["last_run_id"], "")
                self.assertEqual(result["last_resume_session_id"], "durable-session")
                self.assertEqual(controller.registry.require(workspace_id).last_run_id, "expired-run")
                with mock.patch.object(manager, "get_run_overviews", side_effect=RuntimeError("Unreadable state")):
                    with self.assertRaisesRegex(RuntimeError, "Unreadable state"):
                        controller._workspace_registry_payload()
            finally:
                controller.close()

    def test_workspace_list_includes_each_last_run_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.list_runs = lambda limit=20: {  # type: ignore[method-assign]
                "runs": [
                    {
                        "run_id": "run-1",
                        "status": "running",
                        "worker_active": True,
                        "agents": [
                            {"agent": 1, "status": "success"},
                            {"agent": 2, "status": "running"},
                            {"agent": 3, "status": "failed"},
                        ],
                    }
                ],
                "total": 1,
                "limit": limit,
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.registry.register(str(workspace))
            controller.registry.update_context(
                registered["workspace"]["workspace_id"],
                run_id="run-1",
            )

            result = controller.dispatch(RemoteRequest(action="workspaces.list"))

            item = result["workspaces"][0]
            self.assertEqual(item["run_status"], "running")
            self.assertTrue(item["run_worker_active"])
            self.assertEqual(item["run_agents_total"], 3)
            self.assertEqual(item["run_agents_finished"], 2)
            self.assertEqual(item["run_agents_failed"], 1)

    def test_agent_detail_includes_stable_transcript_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.visible_text_prefix = "visible:"
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            result = controller.dispatch(
                RemoteRequest(
                    action="runs.agent",
                    body={
                        "run_id": "run-1",
                        "agent": 2,
                        "cursor": 7,
                        "transcript_items_only": True,
                        "conversation_items_only": True,
                    },
                )
            )

            self.assertNotIn("event_page", result)
            self.assertEqual(
                manager.agent_requests[-1]["transcript_kinds"],
                {"output", "warning", "error"},
            )
            self.assertEqual(result["final_message"], "")
            self.assertEqual(result["final_message_page"]["text"], "final")
            self.assertEqual(result["transcript_page"]["next_cursor"], 8)
            self.assertEqual(
                result["transcript_page"]["items"],
                [
                    {
                        "id": "event-8",
                        "event_id": 8,
                        "kind": "output",
                        "text": "visible:hello",
                    }
                ],
            )

    def test_run_transcript_page_reads_once_and_keeps_physical_agent_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            events = [{"event_id": index, "type": "agent_line", "idx": agent,
                       "text": f"agent_message:answer-{agent}-{index}"}
                      for index, agent in enumerate([2, 1, 2, 8])]
            events.append({"event_id": 4, "type": "agent_tokens", "idx": 1, "reasoning_tokens": 10})
            manager.get_events = mock.Mock(return_value={"run_id": "run-1", "cursor": 0,
                "next_cursor": 5, "total": 5, "has_more": False, "events": events})
            manager.get_agent = mock.Mock(side_effect=AssertionError("Transcript reads must not load Agent metadata"))
            manager.rewrite_agent_visible_texts = mock.Mock(side_effect=lambda run, agent, texts:
                [f"agent-{agent}:{text}" for text in texts])
            controller = RemoteController(state_dir=Path(tmp), manager=manager)
            result = controller.dispatch(RemoteRequest(action="runs.events",
                body={"run_id": "run-1", "transcript_items_only": True}))
            manager.get_events.assert_called_once()
            manager.get_agent.assert_not_called()
            self.assertNotIn("events", result)
            self.assertEqual(result["next_cursor"], 5)
            self.assertFalse(result["has_more"])
            self.assertEqual({branch["agent"]: [item["text"] for item in branch["items"]]
                for branch in result["agents"]}, {2: ["agent-2:answer-2-0", "agent-2:answer-2-2"],
                                                1: ["agent-1:answer-1-1"], 8: ["agent-8:answer-8-3"]})
            raw = controller.dispatch(RemoteRequest(action="runs.events", body={"run_id": "run-1"}))
            self.assertEqual(raw["events"], events)

    def test_run_transcript_noise_page_still_advances_every_agents_shared_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.get_events = mock.Mock(return_value={"run_id": "run-1", "cursor": 5,
                "next_cursor": 9, "total": 12, "has_more": True,
                "events": [{"event_id": 5, "type": "agent_tokens", "idx": 1}]})
            controller = RemoteController(state_dir=Path(tmp), manager=manager)
            result = controller.dispatch(RemoteRequest(action="runs.events",
                body={"run_id": "run-1", "cursor": 5, "transcript_items_only": True}))
            self.assertEqual(result["agents"], [])
            self.assertEqual(result["next_cursor"], 9)
            self.assertTrue(result["has_more"])

    def test_run_conversation_page_uses_canonical_ordinals_without_agent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.get_events = mock.Mock(return_value={"run_id": "run-1", "cursor": 12,
                "next_cursor": 23, "total": 23, "has_more": False,
                "events": [{"event_id": 22, "type": "agent_line", "idx": 2,
                            "text": "agent_message:new visible answer"}]})
            manager.get_agent = mock.Mock(side_effect=AssertionError("Slow metadata must remain independent"))
            controller = RemoteController(state_dir=Path(tmp), manager=manager)
            result = controller.dispatch(RemoteRequest(action="runs.events", body={
                "run_id": "run-1", "agent": 2, "cursor": 12,
                "transcript_items_only": True, "conversation_items_only": True}))
            self.assertEqual(manager.get_events.call_args.kwargs["transcript_kinds"],
                             {"output", "warning", "error"})
            self.assertEqual(manager.get_events.call_args.kwargs["agent"], 2)
            self.assertEqual(result["next_cursor"], 23)
            self.assertEqual(result["agents"][0]["items"][0]["event_id"], 22)
            manager.get_agent.assert_not_called()

    def test_agent_page_resolves_visible_paths_once_not_for_every_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            manager.visible_text_prefix = "visible:"
            manager.get_agent = mock.Mock(return_value={
                "run_id": "run-1", "agent": 2,
                "event_page": {"cursor": 0, "next_cursor": 256, "has_more": False, "total": 256,
                    "events": [{"event_id": index, "type": "agent_line", "idx": 2,
                                "text": f"agent_message:output-{index}"} for index in range(256)]},
            })
            manager.rewrite_agent_visible_text = mock.Mock(wraps=manager.rewrite_agent_visible_text)
            manager.rewrite_agent_visible_texts = mock.Mock(wraps=manager.rewrite_agent_visible_texts)
            controller = RemoteController(state_dir=Path(tmp), manager=manager)
            result = controller.dispatch(RemoteRequest(action="runs.agent",
                body={"run_id": "run-1", "agent": 2, "transcript_items_only": True}))
            self.assertEqual([item["text"] for item in result["transcript_page"]["items"]],
                             [f"visible:output-{index}" for index in range(256)])
            manager.rewrite_agent_visible_texts.assert_called_once_with(
                "run-1", 2, [f"output-{index}" for index in range(256)])
            manager.rewrite_agent_visible_text.assert_not_called()

    def test_run_history_is_exposed_as_a_read_only_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            result = controller.dispatch(
                RemoteRequest(
                    action="runs.history",
                    body={
                        "run_id": "run-1",
                        "cursor": 7,
                        "limit": 25,
                        "conversation_items_only": True,
                    },
                )
            )

            self.assertEqual(result["run_id"], "run-1")
            self.assertEqual(result["cursor"], 7)
            self.assertEqual(result["entries"][0]["text"], "earlier question")
            self.assertTrue(
                manager.history_requests[-1]["conversation_items_only"]
            )

    def test_remote_client_can_create_a_workspace_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "projects"
            parent.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )

            result = controller.dispatch(
                RemoteRequest(
                    action="filesystem.mkdir",
                    body={"parent": str(parent), "name": "New Project"},
                )
            )

            self.assertTrue((parent / "New Project").is_dir())
            self.assertEqual(result["name"], "New Project")

    def test_workspace_settings_are_shared_and_partially_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.dispatch(
                RemoteRequest(
                    action="workspaces.add",
                    body={"path": str(workspace)},
                )
            )
            workspace_id = registered["workspace"]["workspace_id"]

            controller.dispatch(
                RemoteRequest(
                    action="workspaces.settings.update",
                    body={
                        "workspace_id": workspace_id,
                        "agents": 7,
                        "model": "gpt-test",
                        "fast_mode": "on",
                        "resume_include_non_interactive": True,
                        "runs_dir": "/Volumes/External/.codex_parallel_runs",
                        "codex_bin": "/opt/homebrew/bin/codex",
                    },
                )
            )
            updated = controller.dispatch(
                RemoteRequest(
                    action="workspaces.settings.update",
                    body={
                        "workspace_id": workspace_id,
                        "synthesis_agents": 3,
                    },
                )
            )
            listed = controller.dispatch(
                RemoteRequest(action="workspaces.list")
            )

            settings = updated["settings"]
            self.assertEqual(settings["agents"], 7)
            self.assertEqual(settings["synthesis_agents"], 3)
            self.assertEqual(settings["model"], "gpt-test")
            self.assertEqual(settings["fast_mode"], "on")
            self.assertTrue(settings["resume_include_non_interactive"])
            self.assertEqual(
                settings["runs_dir"],
                "/Volumes/External/.codex_parallel_runs",
            )
            self.assertEqual(settings["codex_bin"], "/opt/homebrew/bin/codex")
            self.assertEqual(
                listed["workspaces"][0]["settings"],
                settings,
            )

    def test_workspace_settings_preserve_an_explicit_new_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.dispatch(
                RemoteRequest(
                    action="workspaces.add",
                    body={"path": str(workspace)},
                )
            )
            workspace_id = registered["workspace"]["workspace_id"]

            updated = controller.dispatch(
                RemoteRequest(
                    action="workspaces.settings.update",
                    body={
                        "workspace_id": workspace_id,
                        "resume_session_id": "NO",
                    },
                )
            )
            loaded = controller.dispatch(
                RemoteRequest(
                    action="workspaces.settings.get",
                    body={"workspace_id": workspace_id},
                )
            )

            self.assertEqual(updated["settings"]["resume_session_id"], "NO")
            self.assertEqual(loaded["settings"]["resume_session_id"], "NO")

    def test_default_settings_are_copied_into_new_workspaces_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )

            saved = controller.dispatch(
                RemoteRequest(
                    action="settings.defaults.update",
                    body={
                        "agents": 6,
                        "synthesis_agents": 3,
                        "model": "gpt-default",
                        "keep_workspaces": True,
                        "resume_session_id": "must-be-ignored",
                    },
                )
            )
            first_result = controller.dispatch(
                RemoteRequest(action="workspaces.add", body={"path": str(first)})
            )
            first_id = first_result["workspace"]["workspace_id"]

            controller.dispatch(
                RemoteRequest(
                    action="workspaces.settings.update",
                    body={"workspace_id": first_id, "agents": 9},
                )
            )
            controller.dispatch(
                RemoteRequest(
                    action="settings.defaults.update",
                    body={"agents": 2},
                )
            )
            controller.dispatch(
                RemoteRequest(action="workspaces.add", body={"path": str(second)})
            )
            listed = controller.dispatch(RemoteRequest(action="workspaces.list"))
            settings_by_name = {
                workspace["name"]: workspace["settings"]
                for workspace in listed["workspaces"]
            }

            self.assertEqual(saved["settings"]["agents"], 6)
            self.assertEqual(saved["settings"]["synthesis_agents"], 3)
            self.assertEqual(saved["settings"]["model"], "gpt-default")
            self.assertTrue(saved["settings"]["keep_workspaces"])
            self.assertEqual(saved["settings"]["resume_session_id"], "")
            self.assertEqual(settings_by_name["first"]["agents"], 9)
            self.assertEqual(settings_by_name["second"]["agents"], 2)
            self.assertEqual(
                settings_by_name["second"]["synthesis_agents"],
                3,
            )
            self.assertEqual(
                settings_by_name["second"]["resume_session_id"],
                "NO",
            )

    def test_default_settings_are_owned_by_each_mac_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            browser = FileSystemBrowser(home=root)
            mac_a = RemoteController(
                state_dir=root / "mac-a" / "remote",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=browser,
            )
            mac_b = RemoteController(
                state_dir=root / "mac-b" / "remote",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=browser,
            )

            mac_a.dispatch(
                RemoteRequest(
                    action="settings.defaults.update",
                    body={"agents": 3, "model": "model-a"},
                )
            )
            mac_b.dispatch(
                RemoteRequest(
                    action="settings.defaults.update",
                    body={"agents": 8, "model": "model-b"},
                )
            )

            defaults_a = mac_a.dispatch(
                RemoteRequest(action="settings.defaults.get")
            )["settings"]
            defaults_b = mac_b.dispatch(
                RemoteRequest(action="settings.defaults.get")
            )["settings"]
            self.assertEqual(
                (defaults_a["agents"], defaults_a["model"]),
                (3, "model-a"),
            )
            self.assertEqual(
                (defaults_b["agents"], defaults_b["model"]),
                (8, "model-b"),
            )

    def test_phone_pairing_uses_private_installation_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            observed_headers: dict[str, str] = {}

            def handler(request: httpx.Request) -> httpx.Response:
                observed_headers.update(request.headers)
                return httpx.Response(
                    200,
                    json={
                        "mac_device_id": "mac-1",
                        "pairing_id": "pair-1",
                        "poll_token": "poll-token",
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    },
                )

            controller = RemoteController(
                state_dir=state_dir,
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            controller.relay_configuration.configure(
                base_url="https://relay.test",
                enrollment_token="e" * 32,
                device_name="Studio Mac",
            )
            controller.relay.transport = httpx.MockTransport(handler)

            result = controller.dispatch(
                RemoteRequest(action="relay.pairing_create", body={})
            )

            self.assertEqual(result["pairing_id"], "pair-1")
            self.assertTrue(str(result["pairing_uri"]).startswith("pcr://pair?"))
            self.assertEqual(observed_headers["x-pcr-enrollment"], "e" * 32)
            self.assertNotIn("enrollment_token", result)

            status = controller.dispatch(
                RemoteRequest(action="relay.status", body={})
            )
            health = controller.dispatch(
                RemoteRequest(action="system.health", body={})
            )
            self.assertNotIn("pairing_uri", status)
            self.assertNotIn("pairing_uri", health["relay"])

    def test_phone_pairing_reports_an_unprovisioned_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )

            with self.assertRaises(RemoteControllerError) as raised:
                controller.dispatch(
                    RemoteRequest(action="relay.pairing_create", body={})
                )

            self.assertEqual(raised.exception.code, "server_not_configured")
            self.assertNotIn("token", raised.exception.message.lower())

    def test_start_accepts_an_arbitrary_browsed_path_and_registers_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "external-volume" / "project"
            home.mkdir()
            project.mkdir(parents=True)
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=home),
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.start",
                    body={
                        "workspace": str(project),
                        "prompt": "修复测试",
                        "num_agents": 3,
                    },
                )
            )

            self.assertEqual(response["run_id"], "pcr-test-run")
            self.assertTrue(response["workspace_id"].startswith("ws-"))
            self.assertEqual(manager.started[0]["workspace"], str(project.resolve()))
            self.assertEqual(manager.started[0]["num_agents"], 3)
            self.assertEqual(manager.started[0]["synthesis_agents"], 2)
            self.assertEqual(manager.started[0]["follow_up_delay"], 60)
            self.assertEqual(len(controller.registry.list()["workspaces"]), 1)
            self.assertEqual(
                response["workspace_revision"],
                controller.registry.list()["revision"],
            )

    def test_start_forwards_the_complete_native_workbench_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )

            controller.dispatch(
                RemoteRequest(
                    action="runs.start",
                    body={
                        "workspace": str(workspace),
                        "prompt": "run every configured feature",
                        "num_agents": 6,
                        "max_parallel": 3,
                        "serial": False,
                        "recommend_by": "duration",
                        "model": "gpt-next",
                        "effort": "high",
                        "fast": True,
                        "synthesis_agents": 2,
                        "synthesis_model": "gpt-review",
                        "synthesis_effort": "medium",
                        "synthesis_fast": False,
                        "follow_up_delay": 15,
                        "subagents": True,
                        "subagents_limit": 12,
                        "resume_session_id": "session-1",
                        "runs_dir": str(root / "runs"),
                        "codex_bin": "/usr/local/bin/codex",
                        "sync_back": False,
                        "keep_workspaces": True,
                        "confirm_large_run": True,
                    },
                )
            )

            started = manager.started[0]
            self.assertEqual(started["num_agents"], 6)
            self.assertEqual(started["max_parallel"], 3)
            self.assertFalse(started["serial"])
            self.assertEqual(started["recommend_by"], "duration")
            self.assertEqual(started["model"], "gpt-next")
            self.assertEqual(started["effort"], "high")
            self.assertTrue(started["fast"])
            self.assertEqual(started["synthesis_agents"], 2)
            self.assertEqual(started["synthesis_model"], "gpt-review")
            self.assertEqual(started["synthesis_effort"], "medium")
            self.assertFalse(started["synthesis_fast"])
            self.assertEqual(started["follow_up_delay"], 15)
            self.assertTrue(started["subagents"])
            self.assertEqual(started["subagents_limit"], 12)
            self.assertEqual(started["resume_session_id"], "session-1")
            self.assertEqual(started["runs_dir"], str(root / "runs"))
            self.assertEqual(started["codex_bin"], "/usr/local/bin/codex")
            self.assertFalse(started["sync_back"])
            self.assertTrue(started["keep_workspaces"])
            self.assertTrue(started["confirm_large_run"])

    def test_unknown_action_returns_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            with self.assertRaisesRegex(RemoteControllerError, "Unknown remote action"):
                controller.dispatch(RemoteRequest(action="unknown.action"))

    def test_continue_applies_native_workbench_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.continue",
                    body={
                        "run_id": "pcr-old-run",
                        "agent": 2,
                        "prompt": "continue from here",
                        "num_agents": 6,
                        "automatic_max_parallel": True,
                        "serial": False,
                        "recommend_by": "duration",
                        "model": "gpt-next",
                        "effort": "ultra",
                        "fast_mode": "on",
                        "synthesis_agents": 3,
                        "synthesis_model": "gpt-review",
                        "synthesis_effort": "high",
                        "synthesis_fast_mode": "automatic",
                        "sync_back": True,
                        "keep_workspaces": True,
                        "subagents": True,
                        "subagents_limit": 12,
                        "follow_up_delay": 15,
                        "runs_dir": "/tmp/pcr-runs",
                        "codex_bin": "/usr/local/bin/codex",
                    },
                )
            )

            self.assertEqual(response["run_id"], "pcr-next-run")
            continued = manager.continued[0]
            self.assertEqual(continued["num_agents"], 6)
            self.assertIsNone(continued["max_parallel"])
            self.assertEqual(
                continued["config_overrides"],
                {
                    "supports_agent_questions": False,
                    "max_parallel": None,
                    "serial": False,
                    "recommend_by": "duration",
                    "model": "gpt-next",
                    "effort": "ultra",
                    "fast": True,
                    "synthesis_agents": 3,
                    "synthesis_model": "gpt-review",
                    "synthesis_effort": "high",
                    "synthesis_fast": None,
                    "sync_back": True,
                    "keep_workspaces": True,
                    "subagents": True,
                    "subagents_limit": 12,
                    "follow_up_delay": 15,
                    "runs_dir": "/tmp/pcr-runs",
                    "codex_bin": "/usr/local/bin/codex",
                },
            )

    def test_prompt_history_is_shared_through_the_remote_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
                prompt_history=PromptHistoryStore(root / "prompt-history.json"),
            )

            appended = controller.dispatch(
                RemoteRequest(
                    action="prompt_history.append",
                    body={
                        "workspace": str(workspace),
                        "session_id": "session-1",
                        "prompt": "继续修复中文测试",
                    },
                )
            )
            listed = controller.dispatch(
                RemoteRequest(
                    action="prompt_history.list",
                    body={
                        "workspace": str(workspace),
                        "session_id": "session-1",
                    },
                )
            )

            self.assertEqual(appended["entries"], ["继续修复中文测试"])
            self.assertEqual(listed["entries"], appended["entries"])

    def test_running_synthesis_configuration_is_forwarded_to_the_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = FakeRunManager()
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=manager,  # type: ignore[arg-type]
            )

            controller.dispatch(
                RemoteRequest(
                    action="runs.update_synthesis",
                    body={
                        "run_id": "pcr-running",
                        "synthesis_agents": 3,
                        "synthesis_model": "review-model",
                        "synthesis_effort": "high",
                        "synthesis_fast": True,
                    },
                )
            )

            self.assertEqual(
                manager.synthesis_updates,
                [
                    {
                        "run_id": "pcr-running",
                        "agents": 3,
                        "model": "review-model",
                        "effort": "high",
                        "fast": True,
                    }
                ],
            )

    def test_continue_records_the_new_run_and_promoted_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.workspace = str(workspace.resolve())
            manager.source_prompt = "first question"
            prompt_history = PromptHistoryStore(root / "prompt-history.json")
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                prompt_history=prompt_history,
            )
            registered = controller.registry.register(str(workspace))
            workspace_id = registered["workspace"]["workspace_id"]
            controller.registry.update_context(
                workspace_id,
                run_id="pcr-old-run",
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.continue",
                    body={
                        "run_id": "pcr-old-run",
                        "agent": 1,
                        "prompt": "continue",
                    },
                )
            )

            record = controller.registry.require(workspace_id)
            self.assertEqual(response["workspace_id"], workspace_id)
            self.assertEqual(response["run_id"], "pcr-next-run")
            self.assertEqual(record.last_run_id, "pcr-next-run")
            self.assertEqual(record.last_resume_session_id, "session-next")
            self.assertEqual(
                controller.workspace_config.load(workspace).resume_session_id,
                "session-next",
            )
            self.assertEqual(
                response["workspace_revision"],
                controller.registry.list()["revision"],
            )
            self.assertEqual(
                prompt_history.entries(workspace, "session-next"),
                ["first question"],
            )

    def test_older_conversation_waits_for_current_reviewable_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.get_run = lambda run_id: {  # type: ignore[attr-defined]
                "run_id": run_id,
                "workspace": str(workspace.resolve()),
                "status": "completed" if run_id == "run-current" else "finalized",
                "worker_active": False,
                "finalized_agent": None if run_id == "run-current" else 1,
                "agents": (
                    [{"agent": 1, "can_accept": True}]
                    if run_id == "run-current"
                    else [{"agent": 1, "can_accept": False}]
                ),
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
            )
            registered = controller.registry.register(str(workspace))
            controller.registry.update_context(
                registered["workspace"]["workspace_id"],
                run_id="run-current",
            )

            with self.assertRaises(RemoteControllerError) as raised:
                controller.dispatch(
                    RemoteRequest(
                        action="runs.continue",
                        body={
                            "run_id": "run-older",
                            "agent": 1,
                            "prompt": "continue older conversation",
                        },
                    )
                )

            self.assertEqual(raised.exception.code, "workspace_busy")
            self.assertEqual(manager.continued, [])

    def test_older_conversation_can_continue_after_current_run_is_settled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.workspace = str(workspace.resolve())
            manager.get_run = lambda run_id: {  # type: ignore[attr-defined]
                "run_id": run_id,
                "workspace": str(workspace.resolve()),
                "status": (
                    "finalized" if run_id == "run-current" else "completed"
                ),
                "worker_active": False,
                "finalized_agent": 1 if run_id == "run-current" else None,
                "agents": [
                    {
                        "agent": 1,
                        "can_accept": run_id == "run-older",
                    }
                ],
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
            )
            registered = controller.registry.register(str(workspace))
            controller.registry.update_context(
                registered["workspace"]["workspace_id"],
                run_id="run-current",
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.continue",
                    body={
                        "run_id": "run-older",
                        "agent": 1,
                        "prompt": "continue older conversation",
                    },
                )
            )

            self.assertEqual(response["run_id"], "pcr-next-run")
            self.assertEqual(manager.continued[0]["run_id"], "run-older")

    def test_accept_records_the_promoted_session_for_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            accepted: list[dict] = []

            def accept_agent(run_id, agent, **kwargs):
                accepted.append({"run_id": run_id, "agent": agent, **kwargs})
                return {
                    "run_id": "pcr-finished-run",
                    "workspace": str(workspace.resolve()),
                    "prompt": "finished question",
                    "status": "finalized",
                    "session_promotion": {"session_id": "session-finished"},
                }

            manager.accept_agent = accept_agent  # type: ignore[attr-defined]
            prompt_history = PromptHistoryStore(root / "prompt-history.json")
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                prompt_history=prompt_history,
            )
            registered = controller.registry.register(str(workspace))
            workspace_id = registered["workspace"]["workspace_id"]

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.accept_agent",
                    body={
                        "run_id": "pcr-finished-run",
                        "agent": 1,
                        "keep_workspaces": True,
                        "sync_back": False,
                    },
                )
            )

            record = controller.registry.require(workspace_id)
            self.assertEqual(response["workspace_id"], workspace_id)
            self.assertEqual(record.last_run_id, "pcr-finished-run")
            self.assertEqual(
                record.last_resume_session_id,
                "session-finished",
            )
            self.assertEqual(
                controller.workspace_config.load(workspace).resume_session_id,
                "session-finished",
            )
            self.assertEqual(
                accepted,
                [
                    {
                        "run_id": "pcr-finished-run",
                        "agent": 1,
                        "wait_seconds": 45.0,
                        "keep_workspaces": True,
                        "sync_back": False,
                    }
                ],
            )
            self.assertEqual(
                prompt_history.entries(workspace, "session-finished"),
                ["finished question"],
            )

    def test_late_finalize_does_not_replace_a_newer_workspace_turn(self) -> None:
        for newer_run_already_exists in (False, True):
            with self.subTest(newer_run_already_exists=newer_run_already_exists):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    workspace = root / "workspace"
                    workspace.mkdir()
                    manager = FakeRunManager()
                    controller = RemoteController(state_dir=root / "state", manager=manager)
                    workspace_id = controller.registry.register(str(workspace))["workspace"]["workspace_id"]
                    controller.registry.update_context(workspace_id, run_id="old-turn")

                    def advance():
                        controller.registry.update_context(
                            workspace_id, run_id="new-turn", resume_session_id="new-session"
                        )
                        controller.workspace_config.update(workspace, {"RESUME": "new-session"})

                    if newer_run_already_exists:
                        advance()

                    def finalize(*_args, **_kwargs):
                        if not newer_run_already_exists:
                            advance()
                        return {"run_id": "old-turn", "workspace": str(workspace),
                                "status": "finalized", "prompt": "old question",
                                "session_promotion": {"session_id": "old-session"}}

                    manager.accept_agent = finalize
                    result = controller.dispatch(RemoteRequest(
                        action="turns.finalize", body={"run_id": "old-turn", "agent": 1}
                    ))
                    self.assertEqual(result["status"], "finalized")
                    record = controller.registry.require(workspace_id)
                    self.assertEqual(record.last_run_id, "new-turn")
                    self.assertEqual(record.last_resume_session_id, "new-session")
                    self.assertEqual(controller.workspace_config.load(workspace).resume_session_id, "new-session")

    def test_finalize_context_update_checks_current_turn_under_registry_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            controller = RemoteController(state_dir=root / "state", manager=manager)
            workspace_id = controller.registry.register(str(workspace))["workspace"]["workspace_id"]
            controller.registry.update_context(workspace_id, run_id="old-turn")
            manager.accept_agent = lambda *_args, **_kwargs: {
                "run_id": "old-turn", "workspace": str(workspace), "status": "finalized",
                "session_promotion": {"session_id": "old-session"},
            }
            original_update = controller.registry.update_context

            def concurrent_update(*args, **kwargs):
                original_update(workspace_id, run_id="new-turn", resume_session_id="new-session")
                controller.workspace_config.update(workspace, {"RESUME": "new-session"})
                return original_update(*args, **kwargs)

            with mock.patch.object(controller.registry, "update_context", side_effect=concurrent_update):
                controller.dispatch(RemoteRequest(
                    action="turns.finalize", body={"run_id": "old-turn", "agent": 1}
                ))
            self.assertEqual(controller.registry.require(workspace_id).last_run_id, "new-turn")
            self.assertEqual(controller.workspace_config.load(workspace).resume_session_id, "new-session")

    def test_recover_finalization_requires_an_explicit_sync_result(self) -> None:
        manager = FakeRunManager()
        recovered: list[tuple[str, bool]] = []

        def recover_finalization(run_id: str, sync_was_applied: bool) -> dict:
            recovered.append((run_id, sync_was_applied))
            return {
                "run_id": run_id,
                "status": "cleanup_failed" if sync_was_applied else "completed",
            }

        manager.recover_finalization = recover_finalization  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp) / "state",
                manager=manager,  # type: ignore[arg-type]
            )

            with self.assertRaises(RemoteControllerError) as raised:
                controller.dispatch(
                    RemoteRequest(
                        action="runs.recover_finalization",
                        body={"run_id": "pcr-run"},
                    )
                )
            self.assertEqual(raised.exception.code, "invalid_argument")

            response = controller.dispatch(
                RemoteRequest(
                    action="runs.recover_finalization",
                    body={
                        "run_id": "pcr-run",
                        "sync_was_applied": True,
                    },
                )
            )

        self.assertEqual(response["status"], "cleanup_failed")
        self.assertEqual(recovered, [("pcr-run", True)])

    def test_get_turn_finishes_a_delayed_session_context_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            manager.get_run = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
                "run_id": "pcr-finished-run",
                "workspace": str(workspace.resolve()),
                "status": "finalized",
                "prompt": "completed prompt",
                "agents": [],
                "session_promotion": {"session_id": "session-delayed"},
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
            )
            registered = controller.registry.register(str(workspace))
            workspace_id = registered["workspace"]["workspace_id"]
            controller.registry.update_context(
                workspace_id,
                run_id="pcr-finished-run",
            )
            controller.workspace_config.update(
                workspace,
                {"RESUME": "session-before-run"},
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="turns.get",
                    body={"run_id": "pcr-finished-run"},
                )
            )
            repeated = controller.dispatch(
                RemoteRequest(
                    action="turns.get",
                    body={"run_id": "pcr-finished-run"},
                )
            )

            record = controller.registry.require(workspace_id)
            self.assertEqual(
                response["workspace_revision"],
                controller.registry.list()["revision"],
            )
            self.assertEqual(
                repeated["workspace_revision"],
                response["workspace_revision"],
            )
            self.assertEqual(record.last_resume_session_id, "session-delayed")
            self.assertEqual(
                response["conversation_snapshot"]["workspace"]["workspace_id"],
                workspace_id,
            )
            self.assertEqual(
                controller.workspace_config.load(workspace).resume_session_id,
                "session-delayed",
            )

            for explicit_session in ("NO", "session-chosen-on-another-client"):
                with self.subTest(explicit_session=explicit_session):
                    controller.workspace_config.update(
                        workspace,
                        {"RESUME": explicit_session},
                    )
                    revision = controller.registry.list()["revision"]
                    with mock.patch.object(
                        controller.workspace_config,
                        "update",
                        wraps=controller.workspace_config.update,
                    ) as save_settings:
                        polled = controller.dispatch(
                            RemoteRequest(
                                action="turns.get",
                                body={"run_id": "pcr-finished-run"},
                            )
                        )
                    save_settings.assert_not_called()
                    self.assertEqual(polled["workspace_revision"], revision)
                    self.assertEqual(
                        controller.workspace_config.load(workspace).resume_session_id,
                        None if explicit_session == "NO" else explicit_session,
                    )

            controller.registry.update_context(
                workspace_id,
                run_id="pcr-newer-run",
                resume_session_id="session-newer",
            )
            controller.workspace_config.update(
                workspace,
                {"RESUME": "session-explicit-next"},
            )
            stale = controller.dispatch(
                RemoteRequest(
                    action="turns.get",
                    body={"run_id": "pcr-finished-run"},
                )
            )
            current = controller.registry.require(workspace_id)
            self.assertNotIn("workspace_revision", stale)
            self.assertEqual(current.last_run_id, "pcr-newer-run")
            self.assertEqual(current.last_resume_session_id, "session-newer")
            self.assertEqual(
                controller.workspace_config.load(workspace).resume_session_id,
                "session-explicit-next",
            )

    def test_boolean_arguments_do_not_treat_nonempty_strings_as_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp) / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=Path(tmp)),
            )

            with self.assertRaisesRegex(RemoteControllerError, "must be true or false"):
                controller.dispatch(
                    RemoteRequest(
                        action="filesystem.list",
                        body={"path": tmp, "include_hidden": "false"},
                    )
                )

    def test_workspace_context_change_keeps_selected_run_active(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            manager = FakeRunManager()
            manager.get_run = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
                "run_id": "run-active",
                "status": "running",
                "agents": [],
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            registered = controller.registry.register(str(first))
            controller.registry.update_context(
                registered["workspace"]["workspace_id"],
                run_id="run-active",
            )
            added = controller.registry.register(
                str(second),
                select=False,
                expected_revision=controller.registry.list()["revision"],
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="workspaces.select",
                    body={
                        "workspace_id": added["workspace"]["workspace_id"]
                    },
                    expected_revision=controller.registry.list()["revision"],
                )
            )

            self.assertEqual(
                response["workspace"]["workspace_id"],
                added["workspace"]["workspace_id"],
            )
            self.assertEqual(
                controller.registry.require(
                    registered["workspace"]["workspace_id"]
                ).last_run_id,
                "run-active",
            )

    def test_workspace_selection_is_independent_but_removal_stays_protected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            run = {
                "run_id": "run-reviewable",
                "status": "completed",
                "worker_active": False,
                "finalized_agent": None,
                "agents": [{"agent": 1, "can_accept": True}],
            }
            manager = FakeRunManager()
            manager.get_run = lambda *_args, **_kwargs: run  # type: ignore[attr-defined]
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            current = controller.registry.register(str(first))
            controller.registry.update_context(
                current["workspace"]["workspace_id"],
                run_id="run-reviewable",
            )
            target = controller.registry.register(
                str(second),
                select=False,
                expected_revision=controller.registry.list()["revision"],
            )

            response = controller.dispatch(
                RemoteRequest(
                    action="workspaces.select",
                    body={"workspace_id": target["workspace"]["workspace_id"]},
                    expected_revision=controller.registry.list()["revision"],
                )
            )
            self.assertEqual(
                response["workspace"]["workspace_id"],
                target["workspace"]["workspace_id"],
            )

            run["finalized_agent"] = 1
            run["status"] = "sync_ambiguous"
            with self.assertRaises(RemoteControllerError) as blocked_removal:
                controller.dispatch(
                    RemoteRequest(
                        action="workspaces.remove",
                        body={"workspace_id": current["workspace"]["workspace_id"]},
                        expected_revision=controller.registry.list()["revision"],
                    )
                )
            self.assertEqual(blocked_removal.exception.code, "workspace_busy")

            run["status"] = "finalized"
            removed = controller.dispatch(
                RemoteRequest(
                    action="workspaces.remove",
                    body={"workspace_id": current["workspace"]["workspace_id"]},
                    expected_revision=controller.registry.list()["revision"],
                )
            )
            self.assertEqual(
                removed["removed_workspace_id"],
                current["workspace"]["workspace_id"],
            )

    def test_start_allows_another_workspace_but_not_the_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            manager = FakeRunManager()
            manager.get_run = lambda *_args, **_kwargs: {  # type: ignore[attr-defined]
                "run_id": "run-active",
                "status": "running",
                "agents": [],
            }
            controller = RemoteController(
                state_dir=root / "state",
                manager=manager,  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            current = controller.registry.register(str(first))
            controller.registry.update_context(
                current["workspace"]["workspace_id"],
                run_id="run-active",
            )
            target = controller.registry.register(
                str(second),
                select=False,
                expected_revision=controller.registry.list()["revision"],
            )

            started = controller.dispatch(
                RemoteRequest(
                    action="runs.start",
                    body={
                        "workspace_id": target["workspace"]["workspace_id"],
                        "prompt": "start another task",
                    },
                )
            )
            self.assertEqual(started["run_id"], "pcr-test-run")
            self.assertEqual(len(manager.started), 1)

            with self.assertRaises(RemoteControllerError) as raised:
                controller.dispatch(RemoteRequest(action="runs.start", body={
                    "workspace_id": current["workspace"]["workspace_id"],
                    "prompt": "replace the active task",
                }))
            self.assertEqual(raised.exception.code, "pcr_operation_pending")
            self.assertTrue(raised.exception.retryable)
            self.assertEqual(raised.exception.details["delivery_outcome"], "not_applied")
            self.assertEqual(len(manager.started), 1)


    def test_new_conversation_retains_unadopted_results_but_waits_for_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = FakeRunManager()
            previous = {"run_id": "old", "workspace": str(workspace), "status": "completed",
                        "agents": [{"agent": 1, "status": "success", "can_accept": True}]}
            manager.get_run = lambda *_args, **_kwargs: previous
            controller = RemoteController(state_dir=root / "state", manager=manager)
            workspace_id = controller.registry.register(str(workspace))["workspace"]["workspace_id"]
            controller.registry.update_context(workspace_id, run_id="old")
            controller._ensure_run_start_context(str(workspace), workspace_id)
            self.assertEqual(previous["status"], "completed")
            for status in ["running", "finalizing", "sync_ambiguous", "cleanup_failed"]:
                previous["status"] = status
                with self.assertRaises(RemoteControllerError):
                    controller._ensure_run_start_context(str(workspace), workspace_id)
            previous["status"] = "completed"
            manager.list_runs = lambda **_: {"runs": [
                {"run_id": "older", "workspace": str(workspace), "status": "finalizing"}], "has_more": False}
            with self.assertRaises(RemoteControllerError):
                controller._ensure_run_start_context(str(workspace), workspace_id)

class PcrdIpcTests(unittest.TestCase):
    def test_navigation_burst_is_accepted_before_listener_is_rescheduled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(state_dir=Path(tmp), manager=FakeRunManager())
            server = PcrdServer(Path(tmp) / "pcrd.sock", controller)
            connections = []
            thread = None
            try:
                # A workspace starts independent body, history and metadata reads.
                # Hold accept, not the handlers, to reproduce a brief GIL/OS pause.
                for index in range(16):
                    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connections.append(connection)
                    connection.settimeout(1)
                    connection.connect(str(server.socket_path))
                    request = RemoteRequest(action="system.capabilities", request_id=f"burst-{index}").to_mapping()
                    request["auth_token"] = server.auth_token if index else "invalid"
                    connection.sendall(encode_message(request))
                thread = server.start_thread()
                for index, connection in enumerate(connections):
                    with connection.makefile("rb") as stream:
                        response = decode_message(stream.readline())
                    self.assertEqual(response["request_id"], f"burst-{index}")
                    self.assertEqual(response["ok"], bool(index))
                    if not index:
                        self.assertEqual(response["error"]["code"], "unauthorized")
            finally:
                for connection in connections:
                    connection.close()
                if thread is not None:
                    server.shutdown()
                    thread.join(timeout=3)
                server.server_close()
                controller.close()

    def test_server_preserves_small_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            server = PcrdServer(Path(tmp) / "pcrd.sock", controller)
            try:
                request = RemoteRequest(
                    action="system.capabilities",
                    request_id="small-response",
                    command_id="small-command",
                )
                response = server.execute(request.to_mapping())
                self.assertTrue(response["ok"])
                self.assertEqual(response["request_id"], "small-response")
                self.assertEqual(response["command_id"], "small-command")
            finally:
                server.server_close()
                controller.close()

    def test_server_returns_correlated_error_for_oversized_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            server = PcrdServer(Path(tmp) / "pcrd.sock", controller)
            try:
                controller.dispatch = lambda _request: {  # type: ignore[method-assign]
                    "payload": "x" * MAX_RESPONSE_BYTES
                }
                request = RemoteRequest(
                    action="system.health",
                    request_id="large-response",
                    command_id="large-command",
                )
                response = server.execute(request.to_mapping())
                self.assertFalse(response["ok"])
                self.assertEqual(response["request_id"], "large-response")
                self.assertEqual(response["command_id"], "large-command")
                self.assertEqual(response["error"]["code"], "response_too_large")
                self.assertEqual(
                    response["error"]["details"]["maximum_response_bytes"],
                    MAX_RESPONSE_BYTES,
                )
            finally:
                server.server_close()
                controller.close()

    def test_every_remote_action_has_an_explicit_replay_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(
                state_dir=Path(tmp),
                manager=FakeRunManager(),  # type: ignore[arg-type]
            )
            try:
                self.assertEqual(
                    set(controller.actions),
                    controller.READ_ONLY_ACTIONS | controller.MUTATING_ACTIONS,
                )
                self.assertFalse(
                    controller.READ_ONLY_ACTIONS & controller.MUTATING_ACTIONS
                )
            finally:
                controller.close()

    def test_delayed_enqueue_receipt_returns_current_queue_after_multiple_consumptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = RemoteController(state_dir=Path(tmp), manager=FakeRunManager())
            server = PcrdServer(Path(tmp) / "pcrd.sock", controller)
            try:
                controller.dispatch = lambda request: controller.follow_up_queues.enqueue(
                    workspace_id="workspace", run_id="original", conversation_id="conversation",
                    item=request.body["item"], selected_agent=None)
                first = RemoteRequest(action="followups.enqueue", request_id="first", command_id="first",
                    body={"workspace_id": "workspace", "item": {"id": "a", "prompt": "A"}})
                self.assertTrue(server.execute(first.to_mapping())["ok"])
                controller.follow_up_queues.mutate("workspace", lambda queue: (
                    queue.update(items=[], run_id="next", last_completed_item_id="b") or True))
                response = server.execute(first.to_mapping())
                self.assertTrue(response["ok"])
                self.assertTrue(response["body"]["command_replayed"])
                self.assertEqual(response["body"]["queue"]["items"], [])
                self.assertEqual(response["body"]["queue"]["run_id"], "next")
            finally:
                server.server_close()
                controller.close()

    def test_local_ipc_authenticates_and_replays_mutating_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            controller = RemoteController(
                state_dir=root / "state",
                manager=FakeRunManager(),  # type: ignore[arg-type]
                browser=FileSystemBrowser(home=root),
            )
            socket_path = root / "pcrd.sock"
            server = PcrdServer(socket_path, controller)
            thread = server.start_thread()
            try:
                client = PcrdClient(socket_path, timeout=2)
                health = client.request("system.health")
                self.assertEqual(health["status"], "ok")

                first = client.request(
                    "workspaces.add",
                    {"path": str(project)},
                    client_id="android-test",
                    command_id="add-project",
                )
                replay = client.request(
                    "workspaces.add",
                    {"path": str(project)},
                    client_id="android-test",
                    command_id="add-project",
                )
                self.assertEqual(first["revision"], 1)
                self.assertTrue(replay["command_replayed"])
                self.assertEqual(controller.registry.list()["revision"], 1)

                created = client.request(
                    "filesystem.mkdir",
                    {"parent": str(root), "name": "new-folder"},
                    client_id="android-test",
                    command_id="mkdir-project",
                )
                replayed_create = client.request(
                    "filesystem.mkdir",
                    {"parent": str(root), "name": "new-folder"},
                    client_id="android-test",
                    command_id="mkdir-project",
                )
                self.assertTrue(created["created"])
                self.assertTrue(replayed_create["command_replayed"])
                self.assertTrue((root / "new-folder").is_dir())
            finally:
                server.shutdown()
                server.server_close()
                controller.close()
                thread.join(timeout=2)


class LaunchAgentTests(unittest.TestCase):
    def test_service_preserves_the_virtual_environment_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            python = root / "venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.symlink_to(Path(sys.executable).resolve())
            with mock.patch.object(sys, "executable", str(python)):
                payload = launch_agent_payload(root / "state", home=root)
                unit = systemd_user_unit_payload(root / "state", home=root)
            self.assertEqual(payload["ProgramArguments"][0], str(python))
            self.assertEqual(payload["WorkingDirectory"], str(root / "state"))
            self.assertIn(f'ExecStart="{python}"', unit)

    def test_launch_agent_uses_current_python_and_keeps_daemon_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            payload = launch_agent_payload(state_dir, home=root)

            self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
            self.assertIn("parallel_codex_runner_core.remote.daemon", payload["ProgramArguments"])
            self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
            self.assertEqual(payload["ProcessType"], "Interactive")
            service_path = payload["EnvironmentVariables"]["PATH"].split(os.pathsep)
            self.assertIn("/opt/homebrew/bin", service_path)
            self.assertIn("/Applications/ChatGPT.app/Contents/Resources", service_path)
            self.assertIn(str(root.resolve() / ".local" / "bin"), service_path)
            self.assertIn(str(root.resolve() / "anaconda3" / "bin"), service_path)

            destination = install_launch_agent(
                state_dir,
                home=root,
                run_launchctl=False,
            )
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with destination.open("rb") as stream:
                installed = plistlib.load(stream)
            self.assertEqual(installed["ProcessType"], "Interactive")
            self.assertEqual(installed["ProgramArguments"], payload["ProgramArguments"])


class PcrdAccountSourceTests(unittest.TestCase):
    # Regression: only the launchd agent exported PCR_CODEX_AUTH_SOURCE. Under the
    # systemd unit or a manually started pcrd, Agents silently kept the system
    # ~/.codex login although the switcher saved the selection in PCR's store.

    def test_both_service_managers_use_the_store_the_account_switcher_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            store = default_pcr_codex_home(root)
            with mock.patch.dict(os.environ, {"HOME": str(root)}, clear=True):
                self.assertEqual(pcr_codex_home(), store)
            launchd = launch_agent_payload(root / "state", home=root)["EnvironmentVariables"]
            self.assertEqual(launchd["PCR_CODEX_AUTH_SOURCE"], str(store))
            unit = systemd_user_unit_payload(root / "state", home=root)
            self.assertIn(f'Environment="PCR_CODEX_AUTH_SOURCE={store}"', unit)

    def test_pcrd_started_without_the_variable_hands_workers_that_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"HOME": str(root)}, clear=True):
                self.assertEqual(_default_account_source(), default_pcr_codex_home(root))
                # Workers are child processes: they must inherit it.
                child = subprocess.run(
                    [sys.executable, "-c", "import os; print(os.environ['PCR_CODEX_AUTH_SOURCE'])"],
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(child.stdout.strip(), str(default_pcr_codex_home(root)))
            with mock.patch.dict(os.environ, {"PCR_CODEX_AUTH_SOURCE": "/configured/home"}):
                self.assertIsNone(_default_account_source())
                self.assertEqual(os.environ["PCR_CODEX_AUTH_SOURCE"], "/configured/home")


class SystemdUserServiceTests(unittest.TestCase):
    def test_systemd_service_uses_current_python_and_restarts_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state with spaces"
            payload = systemd_user_unit_payload(state_dir, home=root)

            self.assertIn("Description=Parallel Codex machine service", payload)
            self.assertIn("parallel_codex_runner_core.remote.daemon", payload)
            self.assertIn(f'"{state_dir.resolve()}"', payload)
            self.assertIn("Restart=on-failure", payload)
            self.assertIn("UMask=0077", payload)
            self.assertIn("WantedBy=default.target", payload)

    def test_systemd_service_installs_as_owner_only_user_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / "config"
            state_dir = root / "state"
            with (
                mock.patch.object(sys, "platform", "linux"),
                mock.patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(config_home)},
                    clear=False,
                ),
            ):
                destination = install_systemd_user_service(
                    state_dir,
                    home=root,
                    run_systemctl=False,
                )

            self.assertEqual(
                destination,
                config_home.resolve() / "systemd" / "user" / SYSTEMD_USER_UNIT,
            )
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_systemd_reinstall_restarts_running_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            systemctl = "/usr/bin/systemctl"
            with (
                mock.patch.object(sys, "platform", "linux"),
                mock.patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(root / "config")},
                    clear=False,
                ),
                mock.patch(
                    "parallel_codex_runner_core.remote.service.shutil.which",
                    return_value=systemctl,
                ),
                mock.patch(
                    "parallel_codex_runner_core.remote.service.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as run,
            ):
                install_systemd_user_service(root / "state", home=root)

            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(
                commands,
                [
                    [systemctl, "--user", "daemon-reload"],
                    [systemctl, "--user", "enable", SYSTEMD_USER_UNIT],
                    [systemctl, "--user", "restart", SYSTEMD_USER_UNIT],
                ],
            )

    def test_systemd_status_reports_installed_enabled_and_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / "config"
            unit = config_home / "systemd" / "user" / SYSTEMD_USER_UNIT
            unit.parent.mkdir(parents=True)
            unit.write_text("[Unit]\n", encoding="utf-8")
            with (
                mock.patch.object(sys, "platform", "linux"),
                mock.patch.dict(
                    os.environ,
                    {"XDG_CONFIG_HOME": str(config_home)},
                    clear=False,
                ),
                mock.patch(
                    "parallel_codex_runner_core.remote.service.shutil.which",
                    return_value="/usr/bin/systemctl",
                ),
                mock.patch(
                    "parallel_codex_runner_core.remote.service.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0),
                ),
            ):
                status = service_status(home=root)

            self.assertEqual(status["manager"], "systemd-user")
            self.assertTrue(status["installed"])
            self.assertTrue(status["enabled"])
            self.assertTrue(status["active"])

    def test_systemd_restart_and_logs_use_the_user_service(self) -> None:
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch(
                "parallel_codex_runner_core.remote.service.service_status",
                return_value={"installed": True},
            ),
            mock.patch(
                "parallel_codex_runner_core.remote.service.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            mock.patch(
                "parallel_codex_runner_core.remote.service.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "recent log\n", ""),
                ],
            ) as run,
        ):
            restart_service()
            logs = service_logs(Path("/unused"), lines=25)

        self.assertEqual(logs, "recent log")
        self.assertEqual(
            run.call_args_list[0].args[0],
            ["/usr/bin/systemctl", "--user", "restart", SYSTEMD_USER_UNIT],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/usr/bin/journalctl",
                "--user-unit",
                SYSTEMD_USER_UNIT,
                "--lines",
                "25",
                "--no-pager",
                "--output",
                "short-iso",
            ],
        )


class PcrdInstallerTests(unittest.TestCase):
    def test_bundled_installer_bootstraps_an_isolated_versioned_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "install-pcrd.sh"
            shutil.copy2(
                Path(__file__).parents[1] / "remote" / "installers" / "install-pcrd.sh",
                installer,
            )
            installer.chmod(0o755)
            wheel = root / "parallel_codex_runner-0.1.7-py3-none-any.whl"
            wheel.write_bytes(b"test wheel")
            log = root / "python-arguments.log"
            python = root / "python"
            python.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$0:$*\" >> \"$PCR_TEST_LOG\"\n"
                'if [ "$1" = -I ] && [ "$2" = -S ]; then\n'
                '  mkdir -p "$5/bin"\n  cp "$0" "$5/bin/python"\nfi\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
            runtime = root / "isolated runtime" / "0.1.7"
            runtime_python = runtime / "bin/python"

            subprocess.run(
                [str(installer), "https://relay.test", str(python)],
                check=True,
                env={
                    **os.environ,
                    "PCR_TEST_LOG": str(log),
                    "PCR_RUNTIME_ROOT": str(runtime.parent),
                    "PCR_RELAY_MACHINE_ENROLLMENT_TOKEN": "enrollment-token",
                },
            )

            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                calls,
                [
                    f"{python}:-I -S -m venv {runtime}",
                    f"{runtime_python}:-I -m pip install --upgrade {wheel}",
                    f"{runtime_python}:-I -m parallel_codex_runner_core.remote.daemon install-service "
                    "--server-url https://relay.test",
                    f"{runtime_python}:-I -m parallel_codex_runner_core.remote.daemon service-status",
                ],
            )

    def test_bundled_installer_rejects_missing_noninteractive_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "install-pcrd.sh"
            shutil.copy2(
                Path(__file__).parents[1] / "remote" / "installers" / "install-pcrd.sh",
                installer,
            )
            installer.chmod(0o755)
            (root / "parallel_codex_runner-0.1.7-py3-none-any.whl").write_bytes(
                b"test wheel"
            )

            environment = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "PCR_RELAY_MACHINE_ENROLLMENT_TOKEN",
                    "PCR_RELAY_MAC_ENROLLMENT_TOKEN",
                }
            }
            result = subprocess.run(
                [str(installer), "https://relay.test"],
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "PCR_RELAY_MACHINE_ENROLLMENT_TOKEN is required",
                result.stderr,
            )


if __name__ == "__main__":
    unittest.main()
