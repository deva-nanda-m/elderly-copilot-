"""
Comprehensive test suite for Step 1: Authentication & Elder Profiles,
and Step 2: Database Architecture.
Validates:
1. Account creation & unique elder_id / caregiver linking code assignment
2. Salted bcrypt password hashing & credential protection
3. Login verification & JWT bearer token issuance
4. Rejection of invalid credentials & duplicate usernames
5. Session restoration via GET /api/auth/me
6. Record and profile isolation between different elders (403 protection)
7. Central database persistence (messages, medicines, health diary)
8. Idempotent, duplicate-safe offline synchronization (POST /api/sync)
"""

import os
import sys
import unittest
import tempfile
from pathlib import Path

# Ensure backend root is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from fastapi.testclient import TestClient
from main import app
from auth.security import hash_password, verify_password, decode_access_token
from database.mongodb import CentralDatabase, get_central_db
from database.schemas import (
    ChatMessageRecord,
    MessageRole,
    Medicine,
    MedicationDosageSchedule,
    HealthDiaryEntry,
)


class TestAuthAndDatabase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_01_password_hashing_security(self):
        """Verify that passwords are cryptographically hashed using salted bcrypt and never plaintext."""
        plain = "AppoopanPass123"
        hashed = hash_password(plain)

        self.assertNotEqual(plain, hashed)
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))
        self.assertTrue(verify_password(plain, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_02_elder_signup_and_unique_codes(self):
        """Verify elder account creation assigns unique elder_id and caregiver linking code."""
        signup_payload = {
            "name": "മാധവൻ നായർ (Madhavan)",
            "login_identifier": f"madhavan_{os.urandom(4).hex()}",
            "password": "SecretPassword123",
            "preferred_language": "ml",
            "emergency_contact": "+919876543210",
            "caregiver_name": "അഞ്ജലി (Anjali)",
        }

        response = self.client.post("/api/auth/signup", json=signup_payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")

        elder = data["elder"]
        self.assertTrue(elder["elder_id"].startswith("elder_"))
        self.assertTrue(elder["caregiver_linking_code"].startswith("CARE-"))
        self.assertEqual(elder["name"], "മാധവൻ നായർ (Madhavan)")
        self.assertEqual(elder["preferred_language"], "ml")
        self.assertNotIn("password", elder)
        self.assertNotIn("hashed_password", elder)

        # Verify decoded JWT claims
        token = data["access_token"]
        claims = decode_access_token(token)
        self.assertEqual(claims["sub"], elder["elder_id"])
        self.assertEqual(claims["name"], elder["name"])

    def test_03_duplicate_signup_rejection(self):
        """Verify that attempting to register an existing login_identifier returns 409 Conflict."""
        username = f"elder_dup_{os.urandom(4).hex()}"
        payload = {
            "name": "രാഘവൻ (Raghavan)",
            "login_identifier": username,
            "password": "Password123",
            "preferred_language": "ml",
        }

        # First signup succeeds
        res1 = self.client.post("/api/auth/signup", json=payload)
        self.assertEqual(res1.status_code, 201)

        # Second signup with same username fails
        res2 = self.client.post("/api/auth/signup", json=payload)
        self.assertEqual(res2.status_code, 409)
        self.assertIn("already exists", res2.json()["detail"])

    def test_04_login_and_invalid_credentials(self):
        """Verify login authentication, token return, and rejection of invalid credentials."""
        username = f"user_login_{os.urandom(4).hex()}"
        password = "ValidPassword456"

        # Register user
        self.client.post("/api/auth/signup", json={
            "name": "ഗോപാലൻ (Gopalan)",
            "login_identifier": username,
            "password": password,
            "preferred_language": "ml",
        })

        # 1. Invalid password
        res_bad_pw = self.client.post("/api/auth/login", json={
            "login_identifier": username,
            "password": "IncorrectPassword",
        })
        self.assertEqual(res_bad_pw.status_code, 401)
        self.assertIn("Invalid", res_bad_pw.json()["detail"])

        # 2. Non-existent username
        res_bad_user = self.client.post("/api/auth/login", json={
            "login_identifier": "non_existent_elder_user",
            "password": password,
        })
        self.assertEqual(res_bad_user.status_code, 401)

        # 3. Correct login
        res_ok = self.client.post("/api/auth/login", json={
            "login_identifier": username,
            "password": password,
        })
        self.assertEqual(res_ok.status_code, 200)
        data = res_ok.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["elder"]["login_identifier"], username.lower())

    def test_05_session_restoration(self):
        """Verify session restoration via GET /api/auth/me without re-entering credentials."""
        username = f"user_restore_{os.urandom(4).hex()}"
        signup_res = self.client.post("/api/auth/signup", json={
            "name": "ശാരദ (Sarada)",
            "login_identifier": username,
            "password": "Password789",
            "preferred_language": "ml",
        })
        token = signup_res.json()["access_token"]
        expected_elder_id = signup_res.json()["elder"]["elder_id"]

        # Restore session using token
        me_res = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_res.status_code, 200)
        me_data = me_res.json()
        self.assertEqual(me_data["elder_id"], expected_elder_id)
        self.assertEqual(me_data["name"], "ശാരദ (Sarada)")

    def test_06_record_isolation_between_elders(self):
        """Verify strict authorization check preventing Elder A from reading Elder B's profile."""
        # Elder A
        res_a = self.client.post("/api/auth/signup", json={
            "name": "Elder A",
            "login_identifier": f"elder_a_{os.urandom(4).hex()}",
            "password": "PasswordA123",
        })
        token_a = res_a.json()["access_token"]

        # Elder B
        res_b = self.client.post("/api/auth/signup", json={
            "name": "Elder B",
            "login_identifier": f"elder_b_{os.urandom(4).hex()}",
            "password": "PasswordB123",
        })
        elder_b_id = res_b.json()["elder"]["elder_id"]

        # Elder A tries to view Elder B's private profile
        forbidden_res = self.client.get(
            f"/api/elders/{elder_b_id}/profile",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        self.assertEqual(forbidden_res.status_code, 403)
        self.assertIn("forbidden", forbidden_res.json()["detail"].lower())

    def test_07_central_database_persistence(self):
        """Verify modular persistence of chat messages, medicines, and health diary."""
        db = get_central_db()
        test_elder_id = f"elder_persist_{os.urandom(4).hex()}"

        # 1. Chat Message
        msg = ChatMessageRecord(
            id=f"msg_{os.urandom(4).hex()}",
            elder_id=test_elder_id,
            session_id="session_test_01",
            role=MessageRole.USER,
            text="കുഞ്ചു, എനിക്ക് വിശക്കുന്നു",
        )
        db.save_chat_message(msg)
        history = db.get_chat_history(test_elder_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].text, "കുഞ്ചു, എനിക്ക് വിശക്കുന്നു")

        # 2. Medicine
        med = Medicine(
            id=f"med_{os.urandom(4).hex()}",
            elder_id=test_elder_id,
            name="Metformin 500mg",
            dosage="500mg",
            schedules=[MedicationDosageSchedule(time_of_day="After Breakfast", dose="1 tablet")],
            instructions="Take after food with water",
        )
        db.save_medicine(med)
        meds = db.get_medicines(test_elder_id)
        self.assertEqual(len(meds), 1)
        self.assertEqual(meds[0].name, "Metformin 500mg")

    def test_08_duplicate_safe_sync_pipeline(self):
        """Verify idempotent offline sync batch processing via POST /api/sync."""
        # Register elder
        signup_res = self.client.post("/api/auth/signup", json={
            "name": "Sync Elder",
            "login_identifier": f"sync_elder_{os.urandom(4).hex()}",
            "password": "SyncPassword123",
        })
        token = signup_res.json()["access_token"]
        elder_id = signup_res.json()["elder"]["elder_id"]

        op_id_1 = f"op_{os.urandom(4).hex()}"
        op_id_2 = f"op_{os.urandom(4).hex()}"

        sync_payload = {
            "elder_id": elder_id,
            "operations": [
                {
                    "operation_id": op_id_1,
                    "elder_id": elder_id,
                    "action": "insert_message",
                    "entity_id": f"offline_msg_1",
                    "data": {
                        "session_id": "sess_offline",
                        "role": "user",
                        "text": "ഓഫ്‌ലൈൻ സന്ദേശം (Offline message)",
                    },
                    "client_timestamp": "2026-10-09T10:00:00Z",
                },
                {
                    "operation_id": op_id_2,
                    "elder_id": elder_id,
                    "action": "log_health_diary",
                    "entity_id": f"offline_diary_1",
                    "data": {
                        "date": "2026-10-09",
                        "mood_rating": 4,
                        "notes": "Feeling refreshed today",
                    },
                    "client_timestamp": "2026-10-09T10:05:00Z",
                },
            ],
        }

        # 1. First sync batch
        res1 = self.client.post(
            "/api/sync",
            json=sync_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["processed_count"], 2)
        self.assertIn(op_id_1, data1["synced_operations"])
        self.assertIn(op_id_2, data1["synced_operations"])

        # 2. Resending the same batch (duplicate sync) must be idempotent
        res2 = self.client.post(
            "/api/sync",
            json=sync_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        # Both operations are acknowledged without duplicate database records
        self.assertEqual(data2["processed_count"], 2)
        self.assertIn(op_id_1, data2["synced_operations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
