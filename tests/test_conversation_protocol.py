import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import parallel_codex_runner_core.finalization as finalization
import parallel_codex_runner_core.conversation_service as conversation_service
import parallel_codex_runner_core.conversation_state as conversation_state_module
from parallel_codex_runner_core.conversation_service import ConversationApplicationService
from parallel_codex_runner_core.conversation_protocol import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationSnapshot,
    TurnPhase,
)
from parallel_codex_runner_core.conversation_state import ConversationStateStore
from parallel_codex_runner_core.finalization import finalize_candidate_workspace
from parallel_codex_runner_core.models import AgentResult, CodexSessionPromotion


ROOT = Path(__file__).resolve().parents[1]


def run_summary(
    workspace: Path,
    run_id: str,
    *,
    conversation_id: str,
    turn_id: str,
    turn_index: int = 0,
    continued_from_run_id: str = "",
    continued_from_agent: int | None = None,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "workspace": str(workspace),
        "prompt": f"Prompt for {run_id}",
        "status": "completed",
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "turn_index": turn_index,
        "continued_from_run_id": continued_from_run_id or None,
        "continued_from_agent": continued_from_agent,
        "created_at": f"2026-08-29T00:00:0{turn_index}+00:00",
        "updated_at": f"2026-08-29T00:00:0{turn_index}+00:00",
        "config": {
            "num_agents": 1,
            "synthesis_agents": 0,
            "subagents_limit": 8,
            "sync_back": True,
        },
        "recommended_agent": 1,
        "agents": [
            {
                "agent": 1,
                "name": "AGENT-001",
                "status": "success",
                "role": "candidate",
                "recommended": True,
                "workspace_available": True,
                "can_accept": True,
                "can_continue": True,
                "workspace_dir": str(workspace / ".candidate"),
            }
        ],
    }


class ConversationProtocolTests(unittest.TestCase):
    def test_startup_stages_remain_active_across_domain_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStateStore(root / "state")
            raw = run_summary(root, "run", conversation_id="chat", turn_id="turn")
            for status in ("resuming", "submitting", "waiting_for_output"):
                raw["agents"][0].update(status=status, role="synthesis")
                snapshot = store.upsert_run(raw, workspace_id="workspace")
                self.assertEqual(snapshot.candidates[0].status.value, status)
                self.assertEqual(snapshot.turn.phase, TurnPhase.SYNTHESIS_RUNNING)

    def test_manual_title_survives_polling_new_turns_and_stale_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStateStore(root / "state")
            first = run_summary(root, "run-1", conversation_id="chat", turn_id="turn-1")
            store.upsert_run(first, workspace_id="workspace")
            stale = ConversationStateStore(root / "state")
            renamed = store.rename_conversation("chat", "  PCR\n 开发  ", workspace_id="workspace")
            self.assertEqual(renamed.title, "PCR 开发")
            self.assertEqual(renamed.title_revision, 1)
            self.assertEqual(store.rename_conversation("chat", "PCR 开发", workspace_id="workspace"), renamed)

            next_turn = run_summary(root, "run-2", conversation_id="chat", turn_id="turn-2", turn_index=1)
            snapshot = stale.upsert_run(next_turn, workspace_id="workspace")
            self.assertEqual(snapshot.conversation.custom_title, "PCR 开发")
            self.assertEqual(snapshot.turn.prompt, "Prompt for run-2")
            self.assertEqual(snapshot.conversation.title_revision, 1)
            store.rename_conversation("chat", "你好", workspace_id="workspace")
            replayed = stale.upsert_run(first, workspace_id="workspace")
            self.assertEqual(replayed.conversation.title, "你好")
            self.assertEqual(replayed.conversation.title_revision, 2)
            self.assertEqual(replayed.conversation.active_turn_id, "turn-2")
            self.assertEqual(ConversationSnapshot.from_mapping(replayed.to_mapping()), replayed)

    def test_manual_title_validates_length_and_workspace_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStateStore(root / "state")
            store.upsert_run(run_summary(root, "run", conversation_id="chat", turn_id="turn"), workspace_id="workspace")
            for title, workspace in [("\n\t", "workspace"), ("a" * 201, "workspace"), ("Private", "other")]:
                with self.subTest(title=title, workspace=workspace), self.assertRaises(ValueError):
                    store.rename_conversation("chat", title, workspace_id=workspace)
            self.assertIsNone(store.conversation("chat").custom_title)
            self.assertEqual(store.rename_conversation("chat", "字" * 200, workspace_id="workspace").title_revision, 1)

    def test_imported_session_title_is_inherited_only_by_its_workspace_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStateStore(root / "state")
            store.rename_conversation("resume:session", "Imported name", workspace_id="workspace",
                                      resume_session_id="session", workspace_path=str(root))
            run = run_summary(root, "run", conversation_id="chat", turn_id="turn")
            run["config"]["resume_session_id"] = "session"
            snapshot = store.upsert_run(run, workspace_id="workspace")
            self.assertEqual(snapshot.conversation.custom_title, "Imported name")
            self.assertEqual(snapshot.conversation.title_revision, 1)
            unrelated = run_summary(root, "other", conversation_id="other", turn_id="other")
            self.assertIsNone(store.upsert_run(unrelated, workspace_id="workspace").conversation.custom_title)
            unrelated["config"]["resume_session_id"] = "session"
            self.assertIsNone(store.upsert_run(unrelated, workspace_id="another").conversation.custom_title)

    def test_candidate_timing_survives_protocol_and_store_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = run_summary(root, "timing", conversation_id="conversation", turn_id="turn")
            raw["agents"][0].update({
                "status": "running",
                "started_at": "2026-09-18T10:00:00Z",
                "finished_at": None,
            })
            store = ConversationStateStore(root / "state")
            snapshot = store.upsert_run(raw, machine_id="machine")
            decoded = ConversationSnapshot.from_mapping(snapshot.to_mapping())
            self.assertEqual(decoded.candidates[0].started_at, "2026-09-18T10:00:00Z")
            self.assertIsNone(decoded.candidates[0].finished_at)

    def test_generated_protocol_files_match_schema(self) -> None:
        subprocess.run(
            [sys.executable, "scripts/generate_conversation_protocol.py", "--check"],
            cwd=ROOT,
            check=True,
        )

    def test_store_preserves_conversation_turn_and_candidate_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            first = store.upsert_run(
                run_summary(
                    workspace,
                    "run-1",
                    conversation_id="conversation-1",
                    turn_id="turn-1",
                ),
                machine_id="machine-1",
            )
            second = store.upsert_run(
                run_summary(
                    workspace,
                    "run-2",
                    conversation_id="conversation-1",
                    turn_id="turn-2",
                    turn_index=1,
                    continued_from_run_id="run-1",
                    continued_from_agent=1,
                ),
                machine_id="machine-1",
            )

            self.assertEqual(first.schema_version, CONVERSATION_SCHEMA_VERSION)
            self.assertEqual(second.conversation.turn_ids, ["turn-1", "turn-2"])
            self.assertEqual(second.turn.continued_from_turn_id, "turn-1")
            self.assertEqual(
                second.turn.continued_from_candidate_id,
                "turn-1:agent:1",
            )
            self.assertEqual(second.candidates[0].workspace_dir, str(workspace / ".candidate"))
            decoded = ConversationSnapshot.from_mapping(second.to_mapping())
            self.assertEqual(decoded, second)

            refreshed_old_turn = store.upsert_run(
                run_summary(
                    workspace,
                    "run-1",
                    conversation_id="conversation-1",
                    turn_id="turn-1",
                )
            )
            self.assertEqual(
                refreshed_old_turn.conversation.active_turn_id,
                "turn-2",
            )
            self.assertEqual(refreshed_old_turn.machine.machine_id, "machine-1")

    def test_conversation_title_promotes_past_a_generic_greeting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            greeting = run_summary(
                workspace,
                "run-1",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )
            greeting["prompt"] = "你好。"
            store.upsert_run(greeting, machine_id="machine-1")

            substantive = run_summary(
                workspace,
                "run-2",
                conversation_id="conversation-1",
                turn_id="turn-2",
                turn_index=1,
                continued_from_run_id="run-1",
                continued_from_agent=1,
            )
            substantive["prompt"] = "修复重复回答，并保留我的输入。"
            snapshot = store.upsert_run(substantive, machine_id="machine-1")

            self.assertEqual(snapshot.conversation.title, "修复重复回答，并保留我的输入。")

            # Replaying the older Turn must not restore the greeting title.
            replayed = store.upsert_run(greeting, machine_id="machine-1")
            self.assertEqual(replayed.conversation.title, snapshot.conversation.title)

    def test_unchanged_poll_does_not_rewrite_the_conversation_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            summary = run_summary(
                workspace,
                "run-1",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )
            with mock.patch.object(
                store,
                "_write_locked",
                wraps=store._write_locked,
            ) as write:
                store.upsert_run(summary, machine_id="machine-1")
                store.upsert_run(summary, machine_id="machine-1")

            self.assertEqual(write.call_count, 1)

    def test_run_page_batch_loads_and_writes_conversation_state_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            summaries = [
                run_summary(
                    workspace,
                    f"run-{index}",
                    conversation_id=f"conversation-{index}",
                    turn_id=f"turn-{index}",
                    turn_index=index,
                )
                for index in range(1, 4)
            ]

            with mock.patch.object(
                store,
                "_load_locked",
                wraps=store._load_locked,
            ) as load, mock.patch.object(
                store,
                "_write_locked",
                wraps=store._write_locked,
            ) as write:
                snapshots = store.upsert_runs(summaries, machine_id="machine-1")

            self.assertEqual(len(snapshots), 3)
            self.assertEqual(load.call_count, 1)
            self.assertEqual(write.call_count, 1)
            reloaded = ConversationStateStore(root / "state")
            self.assertEqual(len(reloaded.list_conversations()), 3)

    def test_failed_run_page_batch_does_not_publish_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            store.upsert_run(
                run_summary(
                    workspace,
                    "run-existing",
                    conversation_id="conversation-existing",
                    turn_id="turn-existing",
                )
            )

            with self.assertRaisesRegex(ValueError, "run_id is required"):
                store.upsert_runs(
                    [
                        run_summary(
                            workspace,
                            "run-new",
                            conversation_id="conversation-new",
                            turn_id="turn-new",
                        ),
                        {},
                    ]
                )

            self.assertEqual(
                [
                    item.conversation_id
                    for item in ConversationStateStore(root / "state").list_conversations()
                ],
                ["conversation-existing"],
            )

    def test_legacy_worker_refresh_keeps_existing_conversation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            original = run_summary(
                workspace,
                "run-1",
                conversation_id="conversation-1",
                turn_id="turn-1",
            )
            store.upsert_run(original, machine_id="machine-1")

            legacy_refresh = dict(original)
            legacy_refresh.pop("conversation_id")
            legacy_refresh.pop("turn_id")
            refreshed = store.upsert_run(legacy_refresh, machine_id="machine-1")

            self.assertEqual(refreshed.conversation.conversation_id, "conversation-1")
            self.assertEqual(refreshed.turn.turn_id, "turn-1")
            self.assertEqual(refreshed.conversation.turn_ids, ["turn-1"])

    def test_store_merges_writes_from_multiple_process_owners(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            first = ConversationStateStore(root / "state")
            stale_second = ConversationStateStore(root / "state")
            first.upsert_run(
                run_summary(
                    workspace,
                    "run-1",
                    conversation_id="conversation-1",
                    turn_id="turn-1",
                )
            )
            stale_second.upsert_run(
                run_summary(
                    workspace,
                    "run-2",
                    conversation_id="conversation-2",
                    turn_id="turn-2",
                )
            )
            reloaded = ConversationStateStore(root / "state")
            self.assertEqual(
                {item.conversation_id for item in reloaded.list_conversations()},
                {"conversation-1", "conversation-2"},
            )

    def test_conversation_list_cursor_is_stable_when_newer_items_are_inserted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = mock.Mock(state_dir=root / "state")
            service = ConversationApplicationService(manager, root / "state")
            for index in range(1, 4):
                service.state.upsert_run(
                    run_summary(
                        workspace,
                        f"run-{index}",
                        conversation_id=f"conversation-{index}",
                        turn_id=f"turn-{index}",
                        turn_index=index,
                    )
                )

            first = service.list_conversations(limit=2)
            self.assertEqual(
                [item["conversation_id"] for item in first["conversations"]],
                ["conversation-3", "conversation-2"],
            )
            self.assertTrue(first["has_more"])

            service.state.upsert_run(
                {
                    **run_summary(
                        workspace,
                        "run-4",
                        conversation_id="conversation-4",
                        turn_id="turn-4",
                        turn_index=4,
                    ),
                    "created_at": "2026-08-29T00:00:04+00:00",
                    "updated_at": "2026-08-29T00:00:04+00:00",
                }
            )
            second = service.list_conversations(
                limit=2,
                cursor=first["next_cursor"],
            )

            self.assertEqual(
                [item["conversation_id"] for item in second["conversations"]],
                ["conversation-1"],
            )
            self.assertFalse(second["has_more"])
            self.assertEqual(second["total"], 4)

    def test_conversation_list_rejects_invalid_cursor(self) -> None:
        with self.assertRaisesRegex(ValueError, "Conversation list cursor is invalid"):
            conversation_service._parse_conversation_cursor("not-a-cursor")

    def test_conversation_state_load_skips_parse_when_file_unchanged(self) -> None:
        """Mtime-cache: successive reads do not re-parse an unchanged conversations.json."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ConversationStateStore(root / "state")
            summary = run_summary(workspace, "pcr-20261015T000000Z-aabbccdd",
                conversation_id="conv-1", turn_id="turn-1")
            store.upsert_run(summary)

            # Wrap the path object's read_text to count disk reads. PosixPath
            # attributes are read-only, so monkey-patch json.loads instead: the
            # only caller of json.loads in _load_locked is the conversations.json
            # parse, so counting json.loads calls on that path counts re-parses.
            load_calls: list[int] = []
            original_json_loads = conversation_state_module.json.loads

            def counting_loads(*args: object, **kwargs: object) -> object:
                load_calls.append(1)
                return original_json_loads(*args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(conversation_state_module.json, "loads", counting_loads):
                store.list_conversations()
                store.list_conversations()
            self.assertEqual(load_calls, [],
                "Rapid successive list_conversations() should not re-parse an unchanged conversations.json")

    def test_conversation_state_reloads_after_peer_write(self) -> None:
        """Mtime-cache: a second store instance's write is visible to the first."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            store_a = ConversationStateStore(root / "state")
            store_b = ConversationStateStore(root / "state")

            summary = run_summary(workspace, "pcr-20261015T000000Z-aabbccdd",
                conversation_id="conv-cross", turn_id="turn-cross")
            store_a.upsert_run(summary)

            # store_b has not loaded this yet — its stamp differs from store_a's write.
            result = store_b.list_conversations()
            self.assertEqual(len(result), 1,
                "Second store instance must see a run written by the first")


        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            summary = run_summary(
                workspace,
                "run-failed",
                conversation_id="conversation-1",
                turn_id="turn-failed",
            )
            summary["recommended_agent"] = None
            summary["agents"][0]["status"] = "failed"  # type: ignore[index]
            summary["agents"][0]["recommended"] = False  # type: ignore[index]
            snapshot = ConversationStateStore(root / "state").upsert_run(summary)

            self.assertEqual(snapshot.turn.phase, TurnPhase.FAILED)

    def test_shared_finalizer_uses_one_ordered_sync_promotion_cleanup_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            candidate = root / "runs" / "workspaces" / "agent_001"
            workspaces_root = candidate.parent
            workspace.mkdir(parents=True)
            candidate.mkdir(parents=True)
            events: list[str] = []
            result = AgentResult(
                idx=1,
                workspace_dir=str(candidate),
                meta_dir=str(root / "meta"),
                codex_home=str(root / "codex-home"),
                stdout_log="",
                stderr_log="",
                final_message="",
                command=["codex"],
                returncode=0,
                status="success",
                seconds=1.0,
                codex_thread_id="session-1",
            )

            def sync(_candidate: Path, _workspace: Path) -> None:
                events.append("sync")

            def promote(_result: AgentResult, _workspace: Path) -> CodexSessionPromotion:
                events.append("promote")
                return CodexSessionPromotion("session-1", str(workspace))

            def cleanup(_workspace: Path, root_to_remove: Path) -> None:
                events.append("cleanup")
                shutil.rmtree(root_to_remove)

            with mock.patch.object(finalization, "sync_best_workspace_back", sync), mock.patch.object(
                finalization,
                "cleanup_workspace_copies",
                cleanup,
            ):
                outcome = finalize_candidate_workspace(
                    result,
                    candidate_workspace=candidate,
                    workspace=workspace,
                    workspaces_root=workspaces_root,
                    promote_session=promote,
                    cleanup_codex_homes=lambda: events.append("codex-homes") or True,
                    after_sync=lambda: events.append("synced"),
                    after_promotion=lambda _value: events.append("promoted"),
                    before_cleanup=lambda: events.append("cleanup-started"),
                    require_resume=True,
                )

            self.assertEqual(
                events,
                [
                    "sync",
                    "synced",
                    "promote",
                    "promoted",
                    "cleanup-started",
                    "cleanup",
                    "codex-homes",
                ],
            )
            self.assertTrue(outcome.workspaces_deleted)
            self.assertTrue(outcome.codex_homes_deleted)
            self.assertEqual(outcome.session_promotion["session_id"], "session-1")


if __name__ == "__main__":
    unittest.main()
