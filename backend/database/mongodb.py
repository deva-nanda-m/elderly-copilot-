"""
Central Database Connector (MongoDB with resilient local SQLite mirror).
Provides modular persistence for:
- Elder profiles & account credentials
- Chat messages & conversation sessions
- Sentiment records
- Medicines & medication logs
- Health diary entries
- Proactive care check-ins
- Client synchronization operations
"""

import os
import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

from .schemas import (
    ElderInDB,
    ElderProfile,
    ChatMessageRecord,
    ConversationSession,
    Medicine,
    MedicationLog,
    HealthDiaryEntry,
    CareCheckinRecord,
    SyncOperation,
    SyncBatchResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_SQLITE_CENTRAL_PATH = Path(__file__).resolve().parent.parent / "eldercare_central.db"


class CentralDatabase:
    """
    Modular central database that targets MongoDB if available and configured,
    with an automatic persistent SQLite mirror so the system runs cleanly in all environments.
    """

    def __init__(self, mongo_uri: Optional[str] = None, sqlite_path: Optional[Path] = None):
        self.mongo_uri = mongo_uri or os.getenv("MONGO_URI")
        self.sqlite_path = sqlite_path or DEFAULT_SQLITE_CENTRAL_PATH
        self.mongo_client = None
        self.mongo_db = None
        self.using_mongo = False

        self._init_sqlite_schema()
        self._try_connect_mongo()

    def _try_connect_mongo(self):
        """Attempts to connect to MongoDB if MONGO_URI is set."""
        if not self.mongo_uri:
            logger.info("MONGO_URI not specified. Using local central SQLite storage.")
            return

        try:
            from pymongo import MongoClient
            client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=2000)
            # Trigger server check
            client.admin.command('ping')
            self.mongo_client = client
            db_name = os.getenv("MONGO_DB_NAME", "eldercare")
            self.mongo_db = client[db_name]
            self.using_mongo = True
            logger.info(f"Connected to central MongoDB ({db_name}) successfully.")
        except Exception as e:
            logger.warning(f"Could not connect to MongoDB ({e}). Falling back to local central database.")
            self.using_mongo = False

    def _init_sqlite_schema(self):
        """Ensures all central tables and indexes exist in SQLite."""
        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS elders (
                    elder_id TEXT PRIMARY KEY,
                    login_identifier TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    name TEXT NOT NULL,
                    preferred_language TEXT NOT NULL,
                    emergency_contact TEXT,
                    caregiver_name TEXT,
                    caregiver_linking_code TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    elder_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    audio_url TEXT,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT,
                    FOREIGN KEY (elder_id) REFERENCES elders(elder_id)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS medicines (
                    id TEXT PRIMARY KEY,
                    elder_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    dosage TEXT NOT NULL,
                    schedules_json TEXT NOT NULL,
                    instructions TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    prescribed_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (elder_id) REFERENCES elders(elder_id)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS medication_logs (
                    id TEXT PRIMARY KEY,
                    elder_id TEXT NOT NULL,
                    medicine_id TEXT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    taken_status TEXT NOT NULL,
                    taken_at TEXT,
                    notes TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (elder_id) REFERENCES elders(elder_id)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health_diary (
                    id TEXT PRIMARY KEY,
                    elder_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    mood_rating INTEGER,
                    sleep_quality TEXT,
                    pain_level INTEGER,
                    notes TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (elder_id) REFERENCES elders(elder_id)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_audit (
                    operation_id TEXT PRIMARY KEY,
                    elder_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    client_timestamp TEXT NOT NULL,
                    server_timestamp TEXT NOT NULL
                );
            """)
            conn.commit()

    # ==========================================================================
    # ELDER ACCOUNTS & PROFILES
    # ==========================================================================

    def save_elder(self, elder: ElderInDB) -> ElderProfile:
        """Saves a new elder profile with hashed credentials."""
        # 1. MongoDB if active
        if self.using_mongo and self.mongo_db is not None:
            doc = elder.model_dump()
            self.mongo_db.elders.replace_one({"elder_id": elder.elder_id}, doc, upsert=True)

        # 2. SQLite mirror
        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO elders 
                (elder_id, login_identifier, hashed_password, name, preferred_language,
                 emergency_contact, caregiver_name, caregiver_linking_code, created_at, updated_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                elder.elder_id,
                elder.login_identifier.strip().lower(),
                elder.hashed_password,
                elder.name.strip(),
                elder.preferred_language.value,
                elder.emergency_contact,
                elder.caregiver_name,
                elder.caregiver_linking_code,
                elder.created_at,
                elder.updated_at,
                1 if elder.is_active else 0,
            ))
            conn.commit()

        return ElderProfile(
            elder_id=elder.elder_id,
            login_identifier=elder.login_identifier,
            name=elder.name,
            preferred_language=elder.preferred_language,
            emergency_contact=elder.emergency_contact,
            caregiver_name=elder.caregiver_name,
            caregiver_linking_code=elder.caregiver_linking_code,
            created_at=elder.created_at,
            updated_at=elder.updated_at,
            is_active=elder.is_active,
        )

    def get_elder_by_identifier(self, login_identifier: str) -> Optional[ElderInDB]:
        """Retrieves an elder by username or phone number (for login)."""
        clean_id = login_identifier.strip().lower()

        if self.using_mongo and self.mongo_db is not None:
            doc = self.mongo_db.elders.find_one({"login_identifier": clean_id})
            if doc:
                doc.pop("_id", None)
                return ElderInDB(**doc)

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM elders WHERE login_identifier = ?", (clean_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["is_active"] = bool(data["is_active"])
            return ElderInDB(**data)

    def get_elder_by_id(self, elder_id: str) -> Optional[ElderProfile]:
        """Retrieves public elder profile without password hash."""
        if self.using_mongo and self.mongo_db is not None:
            doc = self.mongo_db.elders.find_one({"elder_id": elder_id})
            if doc:
                doc.pop("_id", None)
                doc.pop("hashed_password", None)
                return ElderProfile(**doc)

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM elders WHERE elder_id = ?", (elder_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data.pop("hashed_password", None)
            data["is_active"] = bool(data["is_active"])
            return ElderProfile(**data)

    def get_elder_by_linking_code(self, linking_code: str) -> Optional[ElderProfile]:
        """Finds elder profile by 6-digit caregiver code (for future MERN link)."""
        code = linking_code.strip().upper()
        if self.using_mongo and self.mongo_db is not None:
            doc = self.mongo_db.elders.find_one({"caregiver_linking_code": code})
            if doc:
                doc.pop("_id", None)
                doc.pop("hashed_password", None)
                return ElderProfile(**doc)

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM elders WHERE caregiver_linking_code = ?", (code,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data.pop("hashed_password", None)
            data["is_active"] = bool(data["is_active"])
            return ElderProfile(**data)

    # ==========================================================================
    # CHAT MESSAGES & HISTORY
    # ==========================================================================

    def save_chat_message(self, message: ChatMessageRecord):
        """Persists a chat message in the central database."""
        if self.using_mongo and self.mongo_db is not None:
            self.mongo_db.chat_messages.replace_one({"id": message.id}, message.model_dump(), upsert=True)

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO chat_messages 
                (id, elder_id, session_id, role, text, audio_url, timestamp, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message.id,
                message.elder_id,
                message.session_id,
                message.role.value,
                message.text,
                message.audio_url,
                message.timestamp,
                json.dumps(message.metadata),
            ))
            conn.commit()

    def get_chat_history(self, elder_id: str, limit: int = 50) -> List[ChatMessageRecord]:
        """Retrieves latest chat history for an elder."""
        if self.using_mongo and self.mongo_db is not None:
            docs = list(self.mongo_db.chat_messages.find({"elder_id": elder_id}).sort("timestamp", -1).limit(limit))
            results = []
            for d in reversed(docs):
                d.pop("_id", None)
                results.append(ChatMessageRecord(**d))
            return results

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM chat_messages 
                WHERE elder_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (elder_id, limit))
            rows = cursor.fetchall()
            results = []
            for r in reversed(rows):
                d = dict(r)
                d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
                results.append(ChatMessageRecord(**d))
            return results

    # ==========================================================================
    # MEDICINES & SCHEDULES
    # ==========================================================================

    def save_medicine(self, medicine: Medicine):
        """Saves a prescribed medicine."""
        if self.using_mongo and self.mongo_db is not None:
            self.mongo_db.medicines.replace_one({"id": medicine.id}, medicine.model_dump(), upsert=True)

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            schedules_json = json.dumps([s.model_dump() for s in medicine.schedules])
            conn.execute("""
                INSERT OR REPLACE INTO medicines 
                (id, elder_id, name, dosage, schedules_json, instructions, is_active, prescribed_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                medicine.id,
                medicine.elder_id,
                medicine.name,
                medicine.dosage,
                schedules_json,
                medicine.instructions,
                1 if medicine.is_active else 0,
                medicine.prescribed_by,
                medicine.created_at,
                medicine.updated_at,
            ))
            conn.commit()

    def get_medicines(self, elder_id: str) -> List[Medicine]:
        """Retrieves active medicines for an elder."""
        if self.using_mongo and self.mongo_db is not None:
            docs = list(self.mongo_db.medicines.find({"elder_id": elder_id, "is_active": True}))
            res = []
            for d in docs:
                d.pop("_id", None)
                res.append(Medicine(**d))
            return res

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM medicines WHERE elder_id = ? AND is_active = 1", (elder_id,))
            res = []
            for r in cursor.fetchall():
                d = dict(r)
                d["schedules"] = json.loads(d.pop("schedules_json", "[]"))
                d["is_active"] = bool(d["is_active"])
                res.append(Medicine(**d))
            return res

    # ==========================================================================
    # DUPLICATE-SAFE SYNCHRONIZATION PIPELINE
    # ==========================================================================

    def process_sync_batch(self, elder_id: str, operations: List[SyncOperation]) -> SyncBatchResponse:
        """
        Processes client offline sync queue idempotently.
        Ensures each operation is applied exactly once via operation_id audit check.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        synced_ids = []
        rejected = []

        with sqlite3.connect(str(self.sqlite_path), timeout=15.0) as conn:
            conn.row_factory = sqlite3.Row

            for op in operations:
                # 1. Scope check: elder must own this operation
                if op.elder_id != elder_id:
                    rejected.append({"operation_id": op.operation_id, "reason": "elder_id mismatch"})
                    continue

                # 2. Check duplicate / idempotency
                cursor = conn.execute("SELECT operation_id FROM sync_audit WHERE operation_id = ?", (op.operation_id,))
                if cursor.fetchone():
                    # Already processed successfully
                    synced_ids.append(op.operation_id)
                    continue

                # 3. Apply operation by action type
                try:
                    if op.action.value == "insert_message":
                        data = op.data
                        msg = ChatMessageRecord(
                            id=op.entity_id,
                            elder_id=elder_id,
                            session_id=data.get("session_id", "default_session"),
                            role=data.get("role", "user"),
                            text=data.get("text", ""),
                            audio_url=data.get("audio_url"),
                            timestamp=op.client_timestamp,
                        )
                        self.save_chat_message(msg)

                    elif op.action.value == "log_medication":
                        data = op.data
                        log = MedicationLog(
                            id=op.entity_id,
                            elder_id=elder_id,
                            medicine_id=data.get("medicine_id", "unknown"),
                            medicine_name=data.get("medicine_name", "Medicine"),
                            scheduled_time=data.get("scheduled_time", now_iso),
                            taken_status=data.get("taken_status", "taken"),
                            taken_at=data.get("taken_at", now_iso),
                            notes=data.get("notes"),
                        )
                        conn.execute("""
                            INSERT OR REPLACE INTO medication_logs 
                            (id, elder_id, medicine_id, medicine_name, scheduled_time, taken_status, taken_at, notes, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            log.id, log.elder_id, log.medicine_id, log.medicine_name,
                            log.scheduled_time, log.taken_status, log.taken_at, log.notes, log.timestamp
                        ))

                    elif op.action.value == "log_health_diary":
                        data = op.data
                        conn.execute("""
                            INSERT OR REPLACE INTO health_diary 
                            (id, elder_id, date, mood_rating, sleep_quality, pain_level, notes, recorded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            op.entity_id,
                            elder_id,
                            data.get("date", now_iso[:10]),
                            data.get("mood_rating"),
                            data.get("sleep_quality"),
                            data.get("pain_level"),
                            data.get("notes"),
                            op.client_timestamp,
                        ))

                    # 4. Audit log the processed operation
                    conn.execute("""
                        INSERT INTO sync_audit (operation_id, elder_id, action, entity_id, client_timestamp, server_timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (op.operation_id, elder_id, op.action.value, op.entity_id, op.client_timestamp, now_iso))

                    conn.commit()
                    synced_ids.append(op.operation_id)

                except Exception as ex:
                    logger.error(f"Error processing sync operation {op.operation_id}: {ex}")
                    rejected.append({"operation_id": op.operation_id, "reason": str(ex)})

        return SyncBatchResponse(
            elder_id=elder_id,
            processed_count=len(synced_ids),
            synced_operations=synced_ids,
            rejected_operations=rejected,
            server_time=now_iso,
        )


_global_central_db: Optional[CentralDatabase] = None


def get_central_db() -> CentralDatabase:
    """Returns singleton central database instance."""
    global _global_central_db
    if _global_central_db is None:
        _global_central_db = CentralDatabase()
    return _global_central_db
