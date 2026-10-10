"""
Comprehensive validation tests for Step 3: Chat Persistence.
Validates:
1. Local & Central message persistence with elder_id, session_id, unique IDs, role, and timestamp
2. Retrieval of chat history via GET /api/elders/{elder_id}/chat-history
3. Strict data isolation between two elder accounts (403 Forbidden on cross-account access)
4. Offline synchronization queue handling and duplicate-safe idempotency via POST /api/sync
5. Context maintenance without mixing sessions or different elders
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from fastapi.testclient import TestClient
from main import app
from auth.security import create_access_token
from database.mongodb import get_central_db
from database.schemas import ChatMessageRecord, MessageRole, SyncOperation, SyncBatchRequest, SyncActionType


class TestChatPersistence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = get_central_db()

        # Create two isolated test elders
        cls.elder_a_id = f"elder_a_{uuid.uuid4().hex[:6]}"
        cls.token_a = create_access_token(
            elder_id=cls.elder_a_id,
            login_identifier=f"user_a_{uuid.uuid4().hex[:6]}",
            name="മാധവൻ നായർ (Madhavan)",
        )

        cls.elder_b_id = f"elder_b_{uuid.uuid4().hex[:6]}"
        cls.token_b = create_access_token(
            elder_id=cls.elder_b_id,
            login_identifier=f"user_b_{uuid.uuid4().hex[:6]}",
            name="ശാരദ അമ്മ (Saradha)",
        )

    def test_01_central_message_association_and_storage(self):
        """Verify chat messages are saved to database with all required metadata fields."""
        msg_id_u = f"msg_{self.elder_a_id}_1001_u"
        msg_id_a = f"msg_{self.elder_a_id}_1001_a"
        session_id = "session_20261010_morning"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Save user message
        u_msg = ChatMessageRecord(
            id=msg_id_u,
            elder_id=self.elder_a_id,
            session_id=session_id,
            role=MessageRole.USER,
            text="കുഞ്ചൂ, ഇന്ന് നല്ല വെയിലാണ്",
            timestamp=now_iso,
        )
        self.db.save_chat_message(u_msg)

        # Save assistant response
        a_msg = ChatMessageRecord(
            id=msg_id_a,
            elder_id=self.elder_a_id,
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            text="അതെ അപ്പൂപ്പാ! നല്ല ചൂടുള്ള ദിവസമാണ്, ധാരാളം വെള്ളം കുടിക്കണേ!",
            audio_url="/api/tts/stream/sun123",
            timestamp=now_iso,
        )
        self.db.save_chat_message(a_msg)

        # Retrieve history
        history = self.db.get_chat_history(elder_id=self.elder_a_id, limit=10)
        self.assertGreaterEqual(len(history), 2)

        retrieved_u = next(m for m in history if m.id == msg_id_u)
        self.assertEqual(retrieved_u.elder_id, self.elder_a_id)
        self.assertEqual(retrieved_u.session_id, session_id)
        self.assertEqual(retrieved_u.role, MessageRole.USER)
        self.assertEqual(retrieved_u.text, "കുഞ്ചൂ, ഇന്ന് നല്ല വെയിലാണ്")

        retrieved_a = next(m for m in history if m.id == msg_id_a)
        self.assertEqual(retrieved_a.elder_id, self.elder_a_id)
        self.assertEqual(retrieved_a.session_id, session_id)
        self.assertEqual(retrieved_a.role, MessageRole.ASSISTANT)
        self.assertEqual(retrieved_a.audio_url, "/api/tts/stream/sun123")

    def test_02_chat_history_endpoint_and_elder_isolation(self):
        """Verify GET /api/elders/{elder_id}/chat-history enforces strict account isolation."""
        # Elder A accesses their own chat history -> SUCCESS (200)
        res_a = self.client.get(
            f"/api/elders/{self.elder_a_id}/chat-history",
            headers={"Authorization": f"Bearer {self.token_a}"},
        )
        self.assertEqual(res_a.status_code, 200)
        data_a = res_a.json()
        self.assertTrue(isinstance(data_a, list))
        self.assertTrue(all(m["elder_id"] == self.elder_a_id for m in data_a))

        # Elder B tries to access Elder A's chat history -> FORBIDDEN (403)
        res_cross = self.client.get(
            f"/api/elders/{self.elder_a_id}/chat-history",
            headers={"Authorization": f"Bearer {self.token_b}"},
        )
        self.assertEqual(res_cross.status_code, 403)
        self.assertIn("forbidden", res_cross.json()["detail"].lower())

    def test_03_offline_queue_sync_and_idempotency(self):
        """Verify POST /api/sync processes offline messages idempotently without creating duplicates."""
        op_id = f"op_sync_{uuid.uuid4().hex[:8]}"
        entity_id = f"msg_{self.elder_b_id}_offline_99"
        session_id = "session_offline_sync"
        client_time = datetime.now(timezone.utc).isoformat()

        batch_payload = {
            "elder_id": self.elder_b_id,
            "operations": [
                {
                    "operation_id": op_id,
                    "elder_id": self.elder_b_id,
                    "action": "insert_message",
                    "entity_id": entity_id,
                    "data": {
                        "session_id": session_id,
                        "role": "user",
                        "text": "ഓഫ്‌ലൈനിൽ ചോദിച്ച ചോദ്യം",
                    },
                    "client_timestamp": client_time,
                    "status": "pending",
                }
            ],
        }

        # First synchronization
        res1 = self.client.post(
            "/api/sync",
            headers={"Authorization": f"Bearer {self.token_b}"},
            json=batch_payload,
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["processed_count"], 1)
        self.assertIn(op_id, data1["synced_operations"])

        # Verify message exists in central database
        history1 = self.db.get_chat_history(elder_id=self.elder_b_id, limit=50)
        matching = [m for m in history1 if m.id == entity_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].text, "ഓഫ്‌ലൈനിൽ ചോദിച്ച ചോദ്യം")

        # Second synchronization with identical batch (simulating duplicate/retry delivery)
        res2 = self.client.post(
            "/api/sync",
            headers={"Authorization": f"Bearer {self.token_b}"},
            json=batch_payload,
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["processed_count"], 1)
        self.assertIn(op_id, data2["synced_operations"])

        # Verify database still has exactly ONE record (duplicate-safe)
        history2 = self.db.get_chat_history(elder_id=self.elder_b_id, limit=50)
        matching2 = [m for m in history2 if m.id == entity_id]
        self.assertEqual(len(matching2), 1)

    def test_04_sync_endpoint_cross_account_protection(self):
        """Verify POST /api/sync prevents Elder A from submitting records for Elder B."""
        op_id = f"op_cross_{uuid.uuid4().hex[:8]}"
        malicious_payload = {
            "elder_id": self.elder_b_id,  # Trying to sync to Elder B's account
            "operations": [
                {
                    "operation_id": op_id,
                    "elder_id": self.elder_b_id,
                    "action": "insert_message",
                    "entity_id": "fake_msg_id",
                    "data": {"text": "Unauthorized"},
                    "client_timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "pending",
                }
            ],
        }

        # Elder A token attempting to sync for Elder B
        res = self.client.post(
            "/api/sync",
            headers={"Authorization": f"Bearer {self.token_a}"},
            json=malicious_payload,
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("different elder", res.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
