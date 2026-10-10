"""
Caregiver Dashboard Integration & Security Test Suite.
Tests:
- Real caregiver signup, login, profile, and logout
- JWT authentication & 401/403 security enforcement
- Strict authorization: caregivers can only view linked elders
- Elder linking via caregiver_linking_code
- Medication schedules and dose logs
- Reminders creation and toggle
- Health diary logs
- Conversation history access
- Sentiment analytics with insufficient_data handling
- Caregiver AI Copilot chat
"""

import os
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

import sys
# Ensure test DB environment before imports
os.environ["CENTRAL_DB_PATH"] = str(Path(__file__).resolve().parent / "temp_test_caregiver.db")
os.environ["SENTIMENT_DB_PATH"] = str(Path(__file__).resolve().parent / "temp_test_sentiment_cg.db")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import app
from database.mongodb import get_central_db
from database.schemas import ElderInDB, CaregiverInDB, Medicine, MedicationDosageSchedule
from auth.security import hash_password, generate_caregiver_linking_code


class CaregiverDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import database.mongodb as mongo_mod
        mongo_mod._global_central_db = None

        for path in [os.environ.get("CENTRAL_DB_PATH"), os.environ.get("SENTIMENT_DB_PATH")]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

        cls.client = TestClient(app)
        cls.db = get_central_db()

        # Ensure test email does not exist prior to test_01
        try:
            import sqlite3
            with sqlite3.connect(str(cls.db.sqlite_path)) as conn:
                conn.execute("DELETE FROM caregivers WHERE email = 'sarah.nurse@eldercare.test'")
                conn.commit()
        except Exception:
            pass

        # Create a test elder
        cls.elder_linking_code = "CARE-7711"
        cls.elder_id = "elder_test_cg_001"
        elder_record = ElderInDB(
            elder_id=cls.elder_id,
            login_identifier="appooppa_test",
            name="Appooppan Test",
            hashed_password=hash_password("elderpass123"),
            caregiver_linking_code=cls.elder_linking_code,
            caregiver_name="Sarah Nurse",
            age=82,
            emergency_contact_phone="+91 9847000000",
        )
        cls.db.save_elder(elder_record)

        # Create an unlinked elder for authorization isolation testing
        cls.unlinked_elder_id = "elder_unlinked_999"
        unlinked_record = ElderInDB(
            elder_id=cls.unlinked_elder_id,
            login_identifier="ammachi_isolated",
            name="Ammachi Isolated",
            hashed_password=hash_password("elderpass123"),
            caregiver_linking_code="CARE-9999",
            caregiver_name="Other Caretaker",
            age=79,
            emergency_contact_phone="+91 9847111111",
        )
        cls.db.save_elder(unlinked_record)

    @classmethod
    def tearDownClass(cls):
        # Cleanup temporary sqlite databases
        for path in [os.environ.get("CENTRAL_DB_PATH"), os.environ.get("SENTIMENT_DB_PATH")]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def test_01_caregiver_signup_and_duplicate_handling(self):
        # Successful signup
        res = self.client.post("/api/caregiver/signup", json={
            "name": "Sarah Nurse",
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
            "phone": "+91 9876543210",
            "role": "nurse",
        })
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["caregiver"]["email"], "sarah.nurse@eldercare.test")
        self.assertEqual(data["caregiver"]["name"], "Sarah Nurse")

        # Duplicate signup must return 409 Conflict
        res_dup = self.client.post("/api/caregiver/signup", json={
            "name": "Duplicate Sarah",
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        self.assertEqual(res_dup.status_code, 409)

    def test_02_caregiver_login_and_me(self):
        # Invalid credentials
        res_invalid = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "WrongPassword999",
        })
        self.assertEqual(res_invalid.status_code, 401)

        # Successful login
        res = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        self.assertEqual(res.status_code, 200)
        token = res.json()["access_token"]
        self.assertTrue(len(token) > 20)

        # Access /me
        headers = {"Authorization": f"Bearer {token}"}
        res_me = self.client.get("/api/caregiver/me", headers=headers)
        self.assertEqual(res_me.status_code, 200)
        self.assertEqual(res_me.json()["email"], "sarah.nurse@eldercare.test")

    def test_03_unauthenticated_request_rejected(self):
        res = self.client.get("/api/caregiver/elders")
        self.assertEqual(res.status_code, 401)

    def test_04_elder_linking_and_authorization(self):
        # Login Sarah
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        token = res_login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Link elder using code
        res_link = self.client.post("/api/caregiver/link-elder", json={
            "caregiver_linking_code": self.elder_linking_code,
        }, headers=headers)
        self.assertEqual(res_link.status_code, 200)
        self.assertTrue(res_link.json()["ok"])
        self.assertEqual(res_link.json()["elder"]["elder_id"], self.elder_id)

        # Get authorized elders
        res_elders = self.client.get("/api/caregiver/elders", headers=headers)
        self.assertEqual(res_elders.status_code, 200)
        elders_list = res_elders.json()["elders"]
        elder_ids = [e["elder_id"] for e in elders_list]
        self.assertIn(self.elder_id, elder_ids)
        self.assertNotIn(self.unlinked_elder_id, elder_ids)

        # Unauthorized elder access must return 403 Forbidden
        res_forbidden = self.client.get(f"/api/caregiver/elders/{self.unlinked_elder_id}", headers=headers)
        self.assertEqual(res_forbidden.status_code, 403)

    def test_05_medications_and_dose_logs(self):
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

        # Add medication
        res_add = self.client.post(f"/api/caregiver/elders/{self.elder_id}/medications", json={
            "name": "Metformin",
            "dosage": "500mg Oral Tablet",
            "instructions": "Take with breakfast",
            "prescribed_by": "Dr. Joseph",
            "schedules": [{"time": "08:00", "meal_relation": "before_food"}],
        }, headers=headers)
        self.assertEqual(res_add.status_code, 201)
        med_id = res_add.json()["medication"]["id"]

        # List medications
        res_list = self.client.get(f"/api/caregiver/elders/{self.elder_id}/medications", headers=headers)
        self.assertEqual(res_list.status_code, 200)
        meds = res_list.json()["medications"]
        self.assertTrue(any(m["name"] == "Metformin" for m in meds))

        # Log dose taken
        res_log = self.client.post(f"/api/caregiver/elders/{self.elder_id}/medication-logs", json={
            "medicine_id": med_id,
            "medicine_name": "Metformin",
            "scheduled_time": "08:00",
            "taken_status": "taken",
            "notes": "Taken with water",
        }, headers=headers)
        self.assertEqual(res_log.status_code, 201)

        # Query logs
        res_logs = self.client.get(f"/api/caregiver/elders/{self.elder_id}/medication-logs", headers=headers)
        self.assertEqual(res_logs.status_code, 200)
        self.assertTrue(len(res_logs.json()["logs"]) >= 1)

    def test_06_reminders_crud_and_toggle(self):
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

        # Create reminder
        res_rem = self.client.post(f"/api/caregiver/elders/{self.elder_id}/reminders", json={
            "title": "Morning Walk",
            "time": "07:00",
            "type": "Wellness",
            "dose": "15 min gentle pace",
        }, headers=headers)
        self.assertEqual(res_rem.status_code, 201)
        rem_id = res_rem.json()["reminder"]["id"]

        # Toggle reminder
        res_toggle = self.client.patch(f"/api/caregiver/elders/{self.elder_id}/reminders/{rem_id}/toggle", headers=headers)
        self.assertEqual(res_toggle.status_code, 200)
        self.assertTrue(res_toggle.json()["reminder"]["done"])

    def test_07_health_diary(self):
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

        res_diary = self.client.post(f"/api/caregiver/elders/{self.elder_id}/health-diary", json={
            "date": "2026-10-10",
            "mood_rating": 4,
            "sleep_quality": "8 hours peaceful sleep",
            "pain_level": 1,
            "notes": "Elder was energetic and cheerful today.",
        }, headers=headers)
        self.assertEqual(res_diary.status_code, 201)

        res_list = self.client.get(f"/api/caregiver/elders/{self.elder_id}/health-diary", headers=headers)
        self.assertEqual(res_list.status_code, 200)
        self.assertTrue(len(res_list.json()["entries"]) >= 1)

    def test_08_conversations_and_real_analytics(self):
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

        # Conversations
        res_conv = self.client.get(f"/api/caregiver/elders/{self.elder_id}/conversations", headers=headers)
        self.assertEqual(res_conv.status_code, 200)
        self.assertIn("sessions", res_conv.json())

        # Analytics (with insufficient data handling)
        res_analytics = self.client.get(f"/api/caregiver/elders/{self.elder_id}/analytics", headers=headers)
        self.assertEqual(res_analytics.status_code, 200)
        analytics_data = res_analytics.json()
        self.assertIn("daily_summary", analytics_data)
        self.assertIn("historical_trends", analytics_data)
        # Should gracefully indicate insufficient data or return valid summary
        self.assertTrue(
            analytics_data["daily_summary"].get("insufficient_data") in [True, False]
        )

    def test_09_caregiver_copilot_chat(self):
        res_login = self.client.post("/api/caregiver/login", json={
            "email": "sarah.nurse@eldercare.test",
            "password": "Password123!",
        })
        headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

        res_chat = self.client.post(f"/api/caregiver/elders/{self.elder_id}/copilot-chat", json={
            "elder_id": self.elder_id,
            "message": "What medications are scheduled for today?",
        }, headers=headers)
        self.assertEqual(res_chat.status_code, 200)
        self.assertIn("Metformin", res_chat.json()["reply"])


if __name__ == "__main__":
    unittest.main()
