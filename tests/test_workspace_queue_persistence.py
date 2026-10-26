"""Test that switching workspace preserves the original workspace's follow-up queue."""
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

from parallel_codex_runner_core.tui_components import QueuedFollowUp
from parallel_codex_runner_core.workspace_config import WorkspaceSettings


def test_follow_up_queue_survives_workspace_switch():
    """
    Scenario: user runs agents in workspace A, queues follow-up messages,
    switches to workspace B, then back to A. Queue should still be there.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_a = Path(tmpdir) / "workspace_a"
        workspace_b = Path(tmpdir) / "workspace_b"
        workspace_a.mkdir()
        workspace_b.mkdir()

        config_file_a = workspace_a / ".pcr_config.json"
        config_file_b = workspace_b / ".pcr_config.json"

        # 1. User is in workspace A, has follow-up queue with 2 items
        original_queue = [
            QueuedFollowUp(
                prompt="first follow-up",
                record_history=True,
                context=(str(workspace_a), "session_123"),
                recorded_history_context=(str(workspace_a), "session_123"),
                id="msg_001",
            ),
            QueuedFollowUp(
                prompt="second follow-up",
                record_history=False,
                context=(str(workspace_a), "session_123"),
                id="msg_002",
            ),
        ]

        # Simulate saving workspace A settings with queue
        queue_dicts = []
        for item in original_queue:
            # Serialize context to list (JSON-compatible)
            context_list = list(item.context) if item.context else None
            recorded_context_list = list(item.recorded_history_context) if item.recorded_history_context else None

            queue_dicts.append({
                "prompt": item.prompt,
                "record_history": item.record_history,
                "context": context_list,
                "recorded_history_context": recorded_context_list,
                "id": item.id,
            })

        args_mock = Mock()
        args_mock.follow_up_delay = 10
        args_mock.synthesis_agents = 3
        args_mock.max_parallel = None
        args_mock.subagents = True
        args_mock.subagents_limit = 10
        args_mock.recommend_by = "auto"
        args_mock.model = None
        args_mock.effort = None
        args_mock.fast = None
        args_mock.synthesis_model = None
        args_mock.synthesis_effort = None
        args_mock.synthesis_fast = None
        args_mock.sync_back = True
        args_mock.keep_workspaces = False
        args_mock.resume_session_id = "session_123"
        args_mock.resume_include_non_interactive = True
        args_mock.runs_dir = None
        args_mock.codex_bin = "codex"

        settings_a = WorkspaceSettings.from_runtime(
            agents=5,
            synthesis_agents=3,
            args=args_mock,
            resume_session_id="session_123",
            follow_up_queue=queue_dicts,
        )

        # Write workspace A config
        config_file_a.write_text(json.dumps(settings_a.to_mapping(), indent=2))

        # 2. User switches to workspace B (empty queue)
        settings_b = WorkspaceSettings.from_runtime(
            agents=2,
            synthesis_agents=1,
            args=args_mock,
            resume_session_id="",
            follow_up_queue=[],
        )
        config_file_b.write_text(json.dumps(settings_b.to_mapping(), indent=2))

        # 3. Now switch back to workspace A and load settings
        loaded_a = WorkspaceSettings.from_mapping(json.loads(config_file_a.read_text()))
        assert loaded_a is not None
        assert len(loaded_a.follow_up_queue) == 2

        # Reconstruct QueuedFollowUp objects
        restored_queue = []
        for item_dict in loaded_a.follow_up_queue:
            if isinstance(item_dict, dict):
                # Convert list back to tuple
                context = tuple(item_dict["context"]) if item_dict.get("context") else None
                recorded_context = tuple(item_dict["recorded_history_context"]) if item_dict.get("recorded_history_context") else None

                restored_queue.append(QueuedFollowUp(
                    prompt=item_dict.get("prompt", ""),
                    record_history=item_dict.get("record_history", False),
                    context=context,
                    recorded_history_context=recorded_context,
                    id=item_dict.get("id"),
                ))

        # Verify queue content
        assert len(restored_queue) == 2
        assert restored_queue[0].prompt == "first follow-up"
        assert restored_queue[0].record_history is True
        assert restored_queue[0].context == (str(workspace_a), "session_123")
        assert restored_queue[0].id == "msg_001"

        assert restored_queue[1].prompt == "second follow-up"
        assert restored_queue[1].record_history is False
        assert restored_queue[1].id == "msg_002"


def test_empty_queue_does_not_break_persistence():
    """Test that an empty queue doesn't cause issues during save/load."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        config_file = workspace / ".pcr_config.json"

        args_mock = Mock()
        args_mock.follow_up_delay = 5
        args_mock.synthesis_agents = 2
        args_mock.max_parallel = None
        args_mock.subagents = True
        args_mock.subagents_limit = 10
        args_mock.recommend_by = "auto"
        args_mock.model = None
        args_mock.effort = None
        args_mock.fast = None
        args_mock.synthesis_model = None
        args_mock.synthesis_effort = None
        args_mock.synthesis_fast = None
        args_mock.sync_back = True
        args_mock.keep_workspaces = False
        args_mock.resume_session_id = ""
        args_mock.resume_include_non_interactive = True
        args_mock.runs_dir = None
        args_mock.codex_bin = "codex"

        settings = WorkspaceSettings.from_runtime(
            agents=3,
            synthesis_agents=2,
            args=args_mock,
            resume_session_id="",
            follow_up_queue=[],
        )

        config_file.write_text(json.dumps(settings.to_mapping(), indent=2))

        loaded = WorkspaceSettings.from_mapping(json.loads(config_file.read_text()))
        assert loaded is not None
        assert loaded.follow_up_queue == []
