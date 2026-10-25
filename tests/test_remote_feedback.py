import base64
import tempfile
import unittest
from pathlib import Path

from parallel_codex_runner_core.remote.feedback import FeedbackError, FeedbackStore


class FeedbackStoreTests(unittest.TestCase):
    def test_submit_is_idempotent_and_diagnostics_are_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")

            first = store.submit(
                client_id="android-device",
                client_feedback_id="client-feedback-1",
                kind="bug",
                description="The task view stopped updating.",
                diagnostics={
                    "app_version": "0.1.91",
                    "run_id": "run-1",
                    "prompt": "must never be retained",
                    "token": "must never be retained",
                },
            )
            repeated = store.submit(
                client_id="android-device",
                client_feedback_id="client-feedback-1",
                kind="bug",
                description="The task view stopped updating.",
                diagnostics={"app_version": "0.1.91"},
            )

            self.assertEqual(first["feedback_id"], repeated["feedback_id"])
            self.assertEqual(first["diagnostics"]["run_id"], "run-1")
            self.assertNotIn("prompt", first["diagnostics"])
            self.assertNotIn("token", first["diagnostics"])
            self.assertNotIn("client_id", first)

    def test_same_client_feedback_id_cannot_change_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            store.submit(
                client_id="android-device",
                client_feedback_id="client-feedback-1",
                kind="bug",
                description="Original",
            )

            with self.assertRaisesRegex(FeedbackError, "already used"):
                store.submit(
                    client_id="android-device",
                    client_feedback_id="client-feedback-1",
                    kind="bug",
                    description="Changed",
                )

    def test_attachment_round_trip_and_withdrawal_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            feedback = store.submit(
                client_id="mac",
                client_feedback_id="client-feedback-1",
                kind="experience",
                description="Spacing is confusing.",
            )
            content = b"\x89PNG\r\n\x1a\nprivate-test-bytes"
            updated = store.add_attachment(
                feedback["feedback_id"],
                content_type="image/png",
                encoded=base64.b64encode(content).decode("ascii"),
            )
            metadata = updated["attachments"][0]

            loaded = store.attachment(
                feedback["feedback_id"],
                metadata["attachment_id"],
            )
            self.assertEqual(base64.b64decode(loaded["data"]), content)
            self.assertNotIn("file_name", metadata)

            withdrawn = store.withdraw(feedback["feedback_id"])
            self.assertEqual(withdrawn["status"], "withdrawn")
            with self.assertRaisesRegex(FeedbackError, "withdrawn"):
                store.add_note(feedback["feedback_id"], "More details")

    def test_feedback_is_private_to_the_submitting_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            first = store.submit(
                client_id="phone-a",
                client_feedback_id="feedback-a",
                kind="bug",
                description="Visible only to phone A.",
            )
            store.submit(
                client_id="phone-b",
                client_feedback_id="feedback-b",
                kind="feature",
                description="Visible only to phone B.",
            )

            listed = store.list(client_id="phone-a")

            self.assertEqual(listed["total"], 1)
            self.assertEqual(listed["items"][0]["feedback_id"], first["feedback_id"])
            with self.assertRaisesRegex(FeedbackError, "not found"):
                store.get(first["feedback_id"], client_id="phone-b")

    def test_attachment_content_must_match_declared_image_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            feedback = store.submit(
                client_id="phone",
                client_feedback_id="feedback-a",
                kind="bug",
                description="Image validation.",
            )

            with self.assertRaisesRegex(FeedbackError, "does not match"):
                store.add_attachment(
                    feedback["feedback_id"],
                    content_type="image/png",
                    encoded=base64.b64encode(b"plain text").decode("ascii"),
                )

    def test_local_inbox_filters_by_status_version_device_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            first = store.submit(
                client_id="phone-a",
                client_feedback_id="feedback-a",
                kind="bug",
                description="First",
                diagnostics={
                    "app_version": "0.1.92",
                    "workspace_id": "workspace-a",
                },
            )
            store.submit(
                client_id="phone-b",
                client_feedback_id="feedback-b",
                kind="feature",
                description="Second",
                diagnostics={
                    "app_version": "0.1.91",
                    "workspace_id": "workspace-b",
                },
            )
            store.admin_update(first["feedback_id"], status="confirmed")

            filtered = store.list(
                status="confirmed",
                app_version="0.1.92",
                device_id="phone-a",
                workspace_id="workspace-a",
            )

            self.assertEqual(filtered["total"], 1)
            self.assertEqual(filtered["items"][0]["feedback_id"], first["feedback_id"])

    def test_fixed_feedback_requires_a_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp) / "feedback")
            feedback = store.submit(
                client_id="mac",
                client_feedback_id="client-feedback-1",
                kind="feature",
                description="Add feedback.",
            )

            with self.assertRaisesRegex(FeedbackError, "fixed_version"):
                store.admin_update(feedback["feedback_id"], status="fixed")

            fixed = store.admin_update(
                feedback["feedback_id"],
                status="fixed",
                developer_reply="Included in the next build.",
                fixed_version="0.1.92",
            )
            self.assertEqual(fixed["status"], "fixed")
            self.assertEqual(fixed["fixed_version"], "0.1.92")


if __name__ == "__main__":
    unittest.main()
