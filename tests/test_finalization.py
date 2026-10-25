import tempfile
import unittest
from pathlib import Path
from unittest import mock

from parallel_codex_runner_core.finalization import (
    FinalizationStageError,
    finalize_candidate_workspace,
)
from parallel_codex_runner_core.models import AgentResult


class FinalizationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.result = AgentResult(
            idx=1,
            workspace_dir=str(self.workspace / "candidate"),
            meta_dir="",
            codex_home="",
            stdout_log="",
            stderr_log="",
            final_message="",
            command=["codex"],
            returncode=0,
            status="success",
            seconds=1.0,
            codex_thread_id="session-1",
        )
        self.promotion = {
            "session_id": "session-1",
            "workspace": str(self.workspace),
        }

    def recover(self, **options):
        return finalize_candidate_workspace(
            self.result,
            candidate_workspace=None,
            workspace=self.workspace,
            workspaces_root=None,
            already_synced=True,
            **options,
        )

    def test_confirmed_sync_still_promotes_before_cleanup(self) -> None:
        events = []
        sync = mock.Mock()
        after_sync = mock.Mock()

        def promote(result, workspace):
            self.assertIs(result, self.result)
            self.assertEqual(workspace, self.workspace)
            events.append("promote")
            return self.promotion

        outcome = self.recover(
            promote_session=promote,
            sync_workspace=sync,
            after_sync=after_sync,
            after_promotion=lambda value: events.append(("promoted", value)),
            before_cleanup=lambda: events.append("cleanup"),
            require_resume=True,
        )
        self.assertEqual(events, ["promote", ("promoted", self.promotion), "cleanup"])
        self.assertEqual(outcome.session_promotion, self.promotion)
        sync.assert_not_called()
        after_sync.assert_not_called()

    def test_cleanup_retry_keeps_existing_successful_promotion(self) -> None:
        promote = mock.Mock()
        after_promotion = mock.Mock()
        outcome = self.recover(
            promote_session=promote,
            existing_promotion=self.promotion,
            after_promotion=after_promotion,
            require_resume=True,
        )
        self.assertEqual(outcome.session_promotion, self.promotion)
        promote.assert_not_called()
        after_promotion.assert_not_called()

    def test_incomplete_or_failed_promotion_is_retried_without_sync(self) -> None:
        for existing in ({}, {"session_id": " "}, {**self.promotion, "error": "busy"}):
            with self.subTest(existing=existing):
                promote = mock.Mock(return_value=self.promotion)
                sync = mock.Mock()
                outcome = self.recover(
                    promote_session=promote,
                    existing_promotion=existing,
                    sync_workspace=sync,
                    keep_workspaces=True,
                )
                promote.assert_called_once_with(self.result, self.workspace)
                sync.assert_not_called()
                self.assertEqual(outcome.session_promotion, self.promotion)

    def test_resume_required_failure_preserves_candidates(self) -> None:
        for result in (None, {**self.promotion, "error": "busy"}):
            with self.subTest(result=result):
                cleanup = mock.Mock()
                with self.assertRaises(FinalizationStageError) as failure:
                    self.recover(
                        promote_session=mock.Mock(return_value=result),
                        before_cleanup=cleanup,
                        require_resume=True,
                    )
                self.assertEqual(failure.exception.stage, "promotion")
                cleanup.assert_not_called()

    def test_promotion_exception_preserves_confirmed_sync(self) -> None:
        sync = mock.Mock()
        outcome = self.recover(
            promote_session=mock.Mock(side_effect=RuntimeError("busy")),
            sync_workspace=sync,
            keep_workspaces=True,
        )
        self.assertEqual(outcome.session_promotion["error"], "busy")
        self.assertFalse(outcome.workspaces_deleted)
        sync.assert_not_called()

    def test_no_sync_back_does_not_promote_into_original_workspace(self) -> None:
        promote = mock.Mock()
        outcome = self.recover(promote_session=promote, sync_back=False)
        self.assertIsNone(outcome.session_promotion)
        promote.assert_not_called()

    def test_timing_is_observational_and_never_blocks_finalization(self) -> None:
        events = []
        outcome = self.recover(
            promote_session=mock.Mock(return_value=self.promotion),
            phase_callback=lambda name, seconds: events.append((name, seconds)),
        )
        self.assertEqual(events[0], ("session_promotion", None))
        self.assertGreaterEqual(events[1][1], 0)
        self.assertEqual(outcome.session_promotion, self.promotion)
        with self.assertLogs("parallel_codex_runner_core.finalization", level="ERROR"):
            outcome = self.recover(
                promote_session=mock.Mock(return_value=self.promotion),
                phase_callback=mock.Mock(side_effect=OSError("telemetry unavailable")),
            )
        self.assertEqual(outcome.session_promotion, self.promotion)


if __name__ == "__main__":
    unittest.main()
