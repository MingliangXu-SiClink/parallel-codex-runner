import base64
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from pcr_relay.app import create_app
from pcr_relay.auth import WeChatIdentity
from pcr_relay.config import Settings


class FakeWeChatClient:
    async def exchange(self, code: str) -> WeChatIdentity:
        if code not in {"first", "second"}:
            raise RuntimeError("unexpected code")
        return WeChatIdentity(f"unionid:{code}")


def public_key(value: bytes) -> str:
    return base64.b64encode(value * 32).decode("ascii")


class FeedbackInboxTests(unittest.TestCase):
    def settings(self, database: Path) -> Settings:
        return Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database}",
            public_base_url="https://relay.test",
            jwt_secret="jwt-secret-" * 4,
            mac_enrollment_token="enrollment-secret-" * 3,
            allowed_wechat_subjects=frozenset(
                {"unionid:first", "unionid:second"}
            ),
        )

    def login(self, client: TestClient, code: str, key: bytes) -> dict[str, str]:
        response = client.post(
            "/v1/auth/wechat",
            json={
                "code": code,
                "device_name": code.title(),
                "public_key": public_key(key),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_feedback_is_central_private_idempotent_and_paginated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                self.settings(Path(tmp) / "relay.db"),
                wechat_client=FakeWeChatClient(),  # type: ignore[arg-type]
            )
            with TestClient(app) as client:
                first = self.login(client, "first", b"a")
                second = self.login(client, "second", b"b")
                payload = {
                    "client_feedback_id": "phone-feedback-1",
                    "kind": "bug",
                    "description": "The run view stopped updating.",
                    "diagnostics": {
                        "app_platform": "android",
                        "app_build": 111,
                        "workspace_id": "workspace-1",
                    },
                }
                created = client.post("/v1/feedback", json=payload, headers=first)
                self.assertEqual(created.status_code, 200, created.text)
                item = created.json()["feedback"]
                feedback_id = item["feedback_id"]
                self.assertEqual(item["status"], "submitted")

                repeated = client.post("/v1/feedback", json=payload, headers=first)
                self.assertEqual(repeated.status_code, 200, repeated.text)
                self.assertEqual(
                    repeated.json()["feedback"]["feedback_id"],
                    feedback_id,
                )
                conflicting = client.post(
                    "/v1/feedback",
                    json={**payload, "description": "Different payload"},
                    headers=first,
                )
                self.assertEqual(conflicting.status_code, 400)

                hidden = client.get(f"/v1/feedback/{feedback_id}", headers=second)
                self.assertEqual(hidden.status_code, 404)

                for index in range(2, 5):
                    response = client.post(
                        "/v1/feedback",
                        json={
                            **payload,
                            "client_feedback_id": f"phone-feedback-{index}",
                            "description": f"Feedback {index}",
                        },
                        headers=first,
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                page = client.get("/v1/feedback?limit=2", headers=first)
                self.assertEqual(page.status_code, 200, page.text)
                self.assertEqual(len(page.json()["items"]), 2)
                self.assertTrue(page.json()["has_more"])
                cursor = page.json()["next_cursor"]
                next_page = client.get(
                    f"/v1/feedback?limit=2&cursor={cursor}",
                    headers=first,
                )
                self.assertEqual(next_page.status_code, 200, next_page.text)
                self.assertEqual(len(next_page.json()["items"]), 2)
                first_ids = {value["feedback_id"] for value in page.json()["items"]}
                next_ids = {
                    value["feedback_id"] for value in next_page.json()["items"]
                }
                self.assertFalse(first_ids & next_ids)

                with closing(sqlite3.connect(Path(tmp) / "relay.db")) as database:
                    self.assertEqual(
                        database.execute("SELECT COUNT(*) FROM feedback").fetchone()[0],
                        4,
                    )
                    self.assertEqual(
                        database.execute(
                            "SELECT COUNT(*) FROM feedback_activity "
                            "WHERE event_type = 'status' AND status = 'submitted'"
                        ).fetchone()[0],
                        4,
                    )

    def test_notes_screenshots_and_withdrawal_are_safe_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                self.settings(Path(tmp) / "relay.db"),
                wechat_client=FakeWeChatClient(),  # type: ignore[arg-type]
            )
            with TestClient(app) as client:
                headers = self.login(client, "first", b"a")
                created = client.post(
                    "/v1/feedback",
                    json={
                        "client_feedback_id": "feedback-1",
                        "kind": "experience",
                        "description": "The layout is difficult to scan.",
                        "diagnostics": {},
                    },
                    headers=headers,
                )
                feedback_id = created.json()["feedback"]["feedback_id"]
                note_body = {"client_note_id": "note-1", "text": "More context"}
                note = client.post(
                    f"/v1/feedback/{feedback_id}/notes",
                    json=note_body,
                    headers=headers,
                )
                self.assertEqual(note.status_code, 200, note.text)
                repeated = client.post(
                    f"/v1/feedback/{feedback_id}/notes",
                    json=note_body,
                    headers=headers,
                )
                self.assertEqual(len(repeated.json()["feedback"]["notes"]), 1)

                jpeg = b"\xff\xd8\xff\xe0" + b"test-image"
                screenshot = client.post(
                    f"/v1/feedback/{feedback_id}/attachments",
                    json={
                        "content_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    },
                    headers=headers,
                )
                self.assertEqual(screenshot.status_code, 200, screenshot.text)
                attachment = screenshot.json()["feedback"]["attachments"][0]
                repeated = client.post(
                    f"/v1/feedback/{feedback_id}/attachments",
                    json={
                        "content_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    },
                    headers=headers,
                )
                self.assertEqual(len(repeated.json()["feedback"]["attachments"]), 1)
                downloaded = client.get(
                    f"/v1/feedback/{feedback_id}/attachments/"
                    f"{attachment['attachment_id']}",
                    headers=headers,
                )
                self.assertEqual(downloaded.status_code, 200, downloaded.text)
                self.assertEqual(downloaded.content, jpeg)

                invalid = client.post(
                    f"/v1/feedback/{feedback_id}/attachments",
                    json={
                        "content_type": "image/png",
                        "data": base64.b64encode(b"not-an-image").decode("ascii"),
                    },
                    headers=headers,
                )
                self.assertEqual(invalid.status_code, 400)

                withdrawn = client.post(
                    f"/v1/feedback/{feedback_id}/withdraw",
                    headers=headers,
                )
                self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
                self.assertEqual(withdrawn.json()["feedback"]["status"], "withdrawn")
                after = client.post(
                    f"/v1/feedback/{feedback_id}/notes",
                    json={"client_note_id": "note-2", "text": "Too late"},
                    headers=headers,
                )
                self.assertEqual(after.status_code, 400)


if __name__ == "__main__":
    unittest.main()
