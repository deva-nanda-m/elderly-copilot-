"""
Central Database Schemas & Pydantic Models for ElderCare Copilot.
Covers:
1. Elder profiles & account references
2. Conversation sessions & individual chat messages
3. Sentiment analysis records
4. Medicines, prescribed dosages, schedules, and medication logs
5. Health diary entries
6. Proactive care check-ins and reminder records
7. Pending synchronization operations
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ==============================================================================
# 1. ELDER PROFILES & ACCOUNT REFERENCES
# ==============================================================================

class PreferredLanguage(str, Enum):
    MALAYALAM = "ml"
    ENGLISH = "en"
    MIXED = "mixed"


class ElderProfileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Full or display name of the elder")
    login_identifier: str = Field(..., min_length=3, max_length=50, description="Username or phone number")
    preferred_language: PreferredLanguage = Field(default=PreferredLanguage.MALAYALAM)
    emergency_contact: Optional[str] = Field(default=None, description="Optional primary emergency phone number")
    caregiver_name: Optional[str] = Field(default=None, description="Name of the linked caregiver if known")


class ElderSignupRequest(ElderProfileBase):
    password: str = Field(..., min_length=4, description="Elder's account password (hashed before storage)")


class ElderLoginRequest(BaseModel):
    login_identifier: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class ElderProfile(ElderProfileBase):
    elder_id: str = Field(..., description="Unique immutable elder identifier (e.g., elder_2026_a1b2c3)")
    caregiver_linking_code: str = Field(..., description="Unique code for caregiver account linking (e.g., CARE-9842)")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True


class ElderInDB(ElderProfile):
    hashed_password: str = Field(..., description="Salted bcrypt/PBKDF2 password hash")


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    elder: ElderProfile
    expires_in_days: int = 30


# ==============================================================================
# 2. CONVERSATION SESSIONS & INDIVIDUAL CHAT MESSAGES
# ==============================================================================

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessageRecord(BaseModel):
    id: str = Field(..., description="Client or server UUID")
    elder_id: str
    session_id: str
    role: MessageRole
    text: str
    audio_url: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationSession(BaseModel):
    session_id: str
    elder_id: str
    start_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    end_time: Optional[str] = None
    message_count: int = 0
    dominant_sentiment: Optional[str] = None
    dominant_emotion: Optional[str] = None
    summary_notes: Optional[str] = None


# ==============================================================================
# 3. SENTIMENT ANALYSIS RECORDS
# ==============================================================================

class SentimentRecord(BaseModel):
    id: str
    elder_id: str
    session_id: str
    text: str
    language: str = "malayalam"
    sentiment: str  # positive, neutral, negative, mixed
    emotion: str    # happy, sad, lonely, anxious, calm, frustrated
    confidence: float
    explanation: str
    request_hash: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    medical_disclaimer: str = (
        "Sentiment and emotional analysis is provided for companionship and wellbeing awareness "
        "only and is NOT a medical, psychiatric, or clinical diagnosis."
    )


# ==============================================================================
# 4. MEDICINES & MEDICATION LOGS
# ==============================================================================

class MedicationDosageSchedule(BaseModel):
    time_of_day: Optional[str] = Field(default="08:00 AM", description="e.g. '08:00 AM', 'After Breakfast'")
    dose: Optional[str] = Field(default="1 dose", description="e.g. '1 tablet', '5 ml'")
    time: Optional[str] = None
    meal_relation: Optional[str] = None
    special_instructions: Optional[str] = Field(default=None, description="e.g. 'Take with water before food'")


class Medicine(BaseModel):
    id: str
    elder_id: str
    name: str = Field(..., description="Brand or generic medicine name (e.g. Paracetamol, Metformin)")
    dosage: str = Field(..., description="Strength, e.g. 500mg")
    schedules: List[MedicationDosageSchedule] = Field(default_factory=list)
    instructions: Optional[str] = None
    is_active: bool = True
    prescribed_by: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MedicationLog(BaseModel):
    id: str
    elder_id: str
    medicine_id: str
    medicine_name: str
    scheduled_time: str
    taken_status: str = Field(..., description="'taken', 'missed', 'snoozed', 'skipped'")
    taken_at: Optional[str] = None
    notes: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ==============================================================================
# 5. HEALTH DIARY ENTRIES
# ==============================================================================

class HealthDiaryEntry(BaseModel):
    id: str
    elder_id: str
    date: str = Field(..., description="YYYY-MM-DD")
    mood_rating: Optional[int] = Field(default=None, ge=1, le=5, description="1 to 5 scale")
    sleep_quality: Optional[str] = Field(default=None, description="'good', 'restless', 'poor'")
    pain_level: Optional[int] = Field(default=None, ge=0, le=10, description="0 (none) to 10 (severe)")
    notes: Optional[str] = None
    recorded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ==============================================================================
# 6. PROACTIVE CARE CHECK-INS & REMINDER RECORDS
# ==============================================================================

class CareCheckinRecord(BaseModel):
    id: str
    elder_id: str
    checkin_type: str = Field(..., description="'hydration', 'mobility', 'meal', 'mood', 'routine'")
    prompt_text: str = Field(..., description="Spoken prompt in Malayalam/English")
    elder_response: Optional[str] = None
    status: str = Field(default="pending", description="'pending', 'completed', 'dismissed'")
    scheduled_for: str
    completed_at: Optional[str] = None


# ==============================================================================
# 7. PENDING SYNCHRONIZATION OPERATIONS
# ==============================================================================

class SyncActionType(str, Enum):
    INSERT_MESSAGE = "insert_message"
    LOG_MEDICATION = "log_medication"
    LOG_HEALTH_DIARY = "log_health_diary"
    UPDATE_PROFILE = "update_profile"


class SyncOperation(BaseModel):
    operation_id: str = Field(..., description="Client-generated unique ID for idempotency")
    elder_id: str
    action: SyncActionType
    entity_id: str
    data: Dict[str, Any]
    client_timestamp: str
    status: str = Field(default="pending", description="'pending', 'synced', 'conflict'")


class SyncBatchRequest(BaseModel):
    elder_id: str
    operations: List[SyncOperation]
    last_synced_at: Optional[str] = None


class SyncBatchResponse(BaseModel):
    elder_id: str
    processed_count: int
    synced_operations: List[str]  # operation_ids successfully processed
    rejected_operations: List[Dict[str, str]] = []  # operation_id -> reason
    server_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ==============================================================================
# 8. CAREGIVER ACCOUNTS, PROFILES & DASHBOARD SCHEMAS
# ==============================================================================

class CaregiverRole(str, Enum):
    FAMILY = "family"
    NURSE = "nurse"
    OTHER = "other"


class CaregiverSignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5, max_length=100)
    password: str = Field(..., min_length=6)
    phone: Optional[str] = None
    role: str = "family"


class CaregiverLoginRequest(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class CaregiverProfile(BaseModel):
    caregiver_id: str
    name: str
    email: str
    phone: Optional[str] = None
    role: str = "family"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True


class CaregiverInDB(CaregiverProfile):
    hashed_password: str


class CaregiverAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    caregiver: CaregiverProfile
    expires_in_days: int = 30


class LinkElderRequest(BaseModel):
    caregiver_linking_code: str = Field(..., min_length=3, max_length=50)


class CaregiverMedicationCreate(BaseModel):
    name: str = Field(..., min_length=1)
    dosage: str = Field(..., min_length=1)
    times: Optional[List[str]] = None
    diet: Optional[str] = None
    schedules: Optional[List[MedicationDosageSchedule]] = None
    instructions: Optional[str] = None
    prescribed_by: Optional[str] = None


class CaregiverMedicationLogCreate(BaseModel):
    medicine_id: str
    medicine_name: str
    scheduled_time: str
    taken_status: str = Field(default="taken", description="'taken', 'missed', 'snoozed', 'skipped'")
    notes: Optional[str] = None


class CaregiverReminderCreate(BaseModel):
    title: str = Field(..., min_length=1)
    time: str = Field(..., min_length=1, description="HH:MM format, e.g. 08:00")
    type: str = Field(default="Supplement", description="'Supplement', 'Wellness', 'Routine'")
    dose: str = Field(default="", description="e.g. 500mg • After Breakfast")


class CaregiverHealthDiaryCreate(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    mood_rating: Optional[int] = Field(default=None, ge=1, le=5)
    sleep_quality: Optional[str] = Field(default=None)
    pain_level: Optional[int] = Field(default=None, ge=0, le=10)
    notes: Optional[str] = None


class CaregiverCopilotChatRequest(BaseModel):
    elder_id: str
    message: str
    history: Optional[List[Dict[str, str]]] = None

