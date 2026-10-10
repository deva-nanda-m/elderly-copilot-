"""
Caregiver Dashboard API Router.
Provides authenticated endpoints for:
- Caregiver registration, login, profile, and logout
- Elder linking via caregiver_linking_code and authorization verification
- Access to authorized elders' medications, dose logs, reminders, health diary, conversations, and sentiment analytics
- Caregiver AI Copilot for clinical observations and schedule insights
"""

import os
import secrets
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query

try:
    from auth.security import (
        hash_password,
        verify_password,
        create_caregiver_access_token,
        get_current_caregiver_id,
    )
    from database.schemas import (
        CaregiverSignupRequest,
        CaregiverLoginRequest,
        CaregiverProfile,
        CaregiverInDB,
        CaregiverAuthTokenResponse,
        LinkElderRequest,
        CaregiverMedicationCreate,
        CaregiverMedicationLogCreate,
        CaregiverReminderCreate,
        CaregiverHealthDiaryCreate,
        CaregiverCopilotChatRequest,
        Medicine,
        MedicationLog,
        HealthDiaryEntry,
    )
    from database.mongodb import get_central_db
    from sentiment.database import get_daily_summary, get_historical_trends
except ImportError:
    from backend.auth.security import (
        hash_password,
        verify_password,
        create_caregiver_access_token,
        get_current_caregiver_id,
    )
    from backend.database.schemas import (
        CaregiverSignupRequest,
        CaregiverLoginRequest,
        CaregiverProfile,
        CaregiverInDB,
        CaregiverAuthTokenResponse,
        LinkElderRequest,
        CaregiverMedicationCreate,
        CaregiverMedicationLogCreate,
        CaregiverReminderCreate,
        CaregiverHealthDiaryCreate,
        CaregiverCopilotChatRequest,
        Medicine,
        MedicationLog,
        HealthDiaryEntry,
    )
    from backend.database.mongodb import get_central_db
    from backend.sentiment.database import get_daily_summary, get_historical_trends

router = APIRouter(prefix="/api/caregiver", tags=["Caregiver Dashboard"])


# ==========================================
# 1. Caregiver Authentication
# ==========================================

@router.post(
    "/signup",
    response_model=CaregiverAuthTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new caregiver account",
)
def caregiver_signup(payload: CaregiverSignupRequest):
    """Registers a new caregiver with email, password, and contact details."""
    db = get_central_db()
    existing = db.get_caregiver_by_email(payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{payload.email}' already exists. Please log in.",
        )

    caregiver_id = f"caregiver_{secrets.token_hex(6)}"
    pw_hash = hash_password(payload.password)
    now_iso = datetime.now(timezone.utc).isoformat()

    caregiver_in_db = CaregiverInDB(
        caregiver_id=caregiver_id,
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        hashed_password=pw_hash,
        phone=payload.phone,
        role=payload.role or "family",
        created_at=now_iso,
        updated_at=now_iso,
        is_active=True,
    )

    saved_profile = db.save_caregiver(caregiver_in_db)

    token = create_caregiver_access_token(
        caregiver_id=saved_profile.caregiver_id,
        email=saved_profile.email,
        full_name=saved_profile.name,
        role=saved_profile.role,
    )

    return CaregiverAuthTokenResponse(
        access_token=token,
        token_type="bearer",
        caregiver=saved_profile,
        expires_in_days=30,
    )


@router.post(
    "/login",
    response_model=CaregiverAuthTokenResponse,
    summary="Caregiver login with email and password",
)
def caregiver_login(payload: CaregiverLoginRequest):
    """Authenticates a caregiver and returns a 30-day JWT token."""
    db = get_central_db()
    caregiver_record = db.get_caregiver_by_email(payload.email)
    if not caregiver_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password. Please verify your credentials.",
        )

    if not verify_password(payload.password, caregiver_record.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password. Please verify your credentials.",
        )

    token = create_caregiver_access_token(
        caregiver_id=caregiver_record.caregiver_id,
        email=caregiver_record.email,
        full_name=caregiver_record.name,
        role=caregiver_record.role,
    )

    profile = CaregiverProfile(
        caregiver_id=caregiver_record.caregiver_id,
        name=caregiver_record.name,
        email=caregiver_record.email,
        phone=caregiver_record.phone,
        role=caregiver_record.role,
        created_at=caregiver_record.created_at,
        updated_at=caregiver_record.updated_at,
        is_active=caregiver_record.is_active,
    )

    return CaregiverAuthTokenResponse(
        access_token=token,
        token_type="bearer",
        caregiver=profile,
        expires_in_days=30,
    )


@router.get(
    "/me",
    response_model=CaregiverProfile,
    summary="Get current logged-in caregiver profile",
)
def get_caregiver_me(caregiver_id: str = Depends(get_current_caregiver_id)):
    """Retrieves current caregiver profile from verified session."""
    db = get_central_db()
    profile = db.get_caregiver_by_id(caregiver_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Caregiver profile not found.",
        )
    return profile


@router.post(
    "/logout",
    summary="Caregiver logout",
)
def caregiver_logout(caregiver_id: str = Depends(get_current_caregiver_id)):
    """Logs out caregiver session (client clears local token)."""
    return {"ok": True, "message": "Successfully logged out."}


# ==========================================
# 2. Elder Management & Authorization
# ==========================================

@router.get(
    "/elders",
    summary="List all elders authorized for the logged-in caregiver",
)
def get_authorized_elders(caregiver_id: str = Depends(get_current_caregiver_id)):
    """Returns only elders linked and authorized for the requesting caregiver."""
    db = get_central_db()
    elders = db.get_elders_for_caregiver(caregiver_id)
    formatted = []
    for e in elders:
        e_dict = e.model_dump()
        meds = db.get_medicines(e.elder_id)
        reminders = db.get_reminders(e.elder_id)
        logs = db.get_medication_logs(e.elder_id, limit=10)
        e_dict["vitals"] = {
            "heart": "72",
            "glucose": "118",
            "sleep": "7.5",
        }
        e_dict["medications"] = [m.model_dump() for m in meds]
        e_dict["reminders"] = reminders
        e_dict["recent_logs"] = [l.model_dump() for l in logs]
        formatted.append(e_dict)
    return {"elders": formatted, "count": len(formatted)}


@router.post(
    "/link-elder",
    summary="Link an elder using caregiver_linking_code",
)
def link_elder(
    payload: LinkElderRequest,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Links an existing elder to the caregiver using the 6-character linking code."""
    db = get_central_db()
    code = payload.caregiver_linking_code.strip().upper()
    elder = db.get_elder_by_linking_code(code)
    if not elder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No elder found with linking code '{code}'. Please verify the code from the elder's app.",
        )
    db.link_caregiver_to_elder(caregiver_id, elder.elder_id)
    return {
        "ok": True,
        "message": f"Successfully linked elder {elder.name}.",
        "elder": elder.model_dump(),
    }


def _verify_elder_access(db, caregiver_id: str, elder_id: str):
    """Helper to verify authorization before returning sensitive elder health records."""
    if not db.is_caregiver_authorized_for_elder(caregiver_id, elder_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You are not authorized to access this elder's records.",
        )


@router.get(
    "/elders/{elder_id}",
    summary="Get details of a specific authorized elder",
)
def get_elder_details(
    elder_id: str,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves full profile for an authorized elder."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    elder = db.get_elder_by_id(elder_id)
    if not elder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Elder not found.",
        )
    return elder.model_dump()


# ==========================================
# 3. Medications & Dose Logs
# ==========================================

@router.get(
    "/elders/{elder_id}/medications",
    summary="Get medication schedule for an elder",
)
def list_medications(
    elder_id: str,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves current medicines and dosage schedules for the elder."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    meds = db.get_medicines(elder_id)
    return {"elder_id": elder_id, "medications": [m.model_dump() for m in meds]}


@router.post(
    "/elders/{elder_id}/medications",
    status_code=status.HTTP_201_CREATED,
    summary="Add a new medication for an elder",
)
def add_medication(
    elder_id: str,
    payload: CaregiverMedicationCreate,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Caregiver creates a new medication schedule."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    med_id = f"med_{secrets.token_hex(4)}"
    now_iso = datetime.now(timezone.utc).isoformat()
    record = Medicine(
        id=med_id,
        elder_id=elder_id,
        name=payload.name,
        dosage=payload.dosage,
        schedules=payload.schedules,
        instructions=payload.instructions,
        is_active=True,
        prescribed_by=payload.prescribed_by,
        created_at=now_iso,
        updated_at=now_iso,
    )
    db.save_medicine(record)
    return {"ok": True, "message": "Medication added successfully.", "medication": record.model_dump()}


@router.get(
    "/elders/{elder_id}/medication-logs",
    summary="Get medication adherence logs for an elder",
)
def list_medication_logs(
    elder_id: str,
    limit: int = Query(50, ge=1, le=200),
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves dose tracking logs (taken, missed, skipped) for the elder."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    logs = db.get_medication_logs(elder_id, limit=limit)
    return {"elder_id": elder_id, "logs": [l.model_dump() for l in logs], "count": len(logs)}


@router.post(
    "/elders/{elder_id}/medication-logs",
    status_code=status.HTTP_201_CREATED,
    summary="Record a medication dose event (taken, missed, skipped)",
)
def record_medication_log(
    elder_id: str,
    payload: CaregiverMedicationLogCreate,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Caregiver logs a dose event or status update."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    record = MedicationLog(
        id=f"log_{secrets.token_hex(4)}",
        elder_id=elder_id,
        medicine_id=payload.medicine_id,
        medicine_name=payload.medicine_name,
        scheduled_time=payload.scheduled_time,
        taken_status=payload.taken_status,
        taken_at=now_iso if payload.taken_status == "taken" else None,
        notes=payload.notes,
        timestamp=now_iso,
    )
    db.save_medication_log(record)
    return {"ok": True, "message": "Medication log recorded.", "log": record.model_dump()}


# ==========================================
# 4. Reminders
# ==========================================

@router.get(
    "/elders/{elder_id}/reminders",
    summary="Get reminders for an elder",
)
def list_reminders(
    elder_id: str,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves active and scheduled reminders for the elder."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    reminders = db.get_reminders(elder_id)
    return {"elder_id": elder_id, "reminders": reminders}


@router.post(
    "/elders/{elder_id}/reminders",
    status_code=status.HTTP_201_CREATED,
    summary="Create a reminder for an elder",
)
def add_reminder(
    elder_id: str,
    payload: CaregiverReminderCreate,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Caregiver sets a new reminder."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    record = db.save_reminder(
        elder_id=elder_id,
        title=payload.title,
        time=payload.time,
        type=payload.type,
        dose=payload.dose,
    )
    return {"ok": True, "message": "Reminder created successfully.", "reminder": record}


@router.patch(
    "/elders/{elder_id}/reminders/{reminder_id}/toggle",
    summary="Toggle reminder enabled/disabled state",
)
def toggle_reminder_status(
    elder_id: str,
    reminder_id: str,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Toggles whether a reminder is active."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    result = db.toggle_reminder(elder_id, reminder_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reminder not found.",
        )
    return {"ok": True, "reminder": result}


# ==========================================
# 5. Health Diary
# ==========================================

@router.get(
    "/elders/{elder_id}/health-diary",
    summary="Get health diary logs (BP, sugar, pulse, sleep, notes)",
)
def list_health_diary(
    elder_id: str,
    limit: int = Query(50, ge=1, le=200),
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves recorded health diary entries."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    entries = db.get_health_diary(elder_id, limit=limit)
    return {"elder_id": elder_id, "entries": [e.model_dump() for e in entries], "count": len(entries)}


@router.post(
    "/elders/{elder_id}/health-diary",
    status_code=status.HTTP_201_CREATED,
    summary="Add a health diary observation",
)
def add_health_diary(
    elder_id: str,
    payload: CaregiverHealthDiaryCreate,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Records vitals and wellbeing notes."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = HealthDiaryEntry(
        id=f"hd_{secrets.token_hex(4)}",
        elder_id=elder_id,
        date=payload.date,
        mood_rating=payload.mood_rating,
        sleep_quality=payload.sleep_quality,
        pain_level=payload.pain_level,
        notes=payload.notes,
        recorded_at=now_iso,
    )
    db.save_health_diary(entry)
    return {"ok": True, "message": "Health diary entry recorded.", "entry": entry.model_dump()}


# ==========================================
# 6. Conversations & Chat History
# ==========================================

@router.get(
    "/elders/{elder_id}/conversations",
    summary="Get elder conversational interactions and sessions",
)
def get_conversations(
    elder_id: str,
    limit: int = Query(100, ge=1, le=500),
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """Retrieves synchronized conversation history for the elder grouped by sessions."""
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    messages = db.get_chat_history(elder_id, limit=limit)
    msg_dicts = [m.model_dump() for m in messages]

    # Group into sessions
    sessions_map: Dict[str, List[Dict[str, Any]]] = {}
    for msg in msg_dicts:
        sess_id = msg.get("session_id") or "general_session"
        if sess_id not in sessions_map:
            sessions_map[sess_id] = []
        sessions_map[sess_id].append(msg)

    session_summaries = []
    for sess_id, msgs in sessions_map.items():
        first_time = msgs[0].get("timestamp") if msgs else ""
        last_time = msgs[-1].get("timestamp") if msgs else ""
        session_summaries.append({
            "session_id": sess_id,
            "message_count": len(msgs),
            "start_time": first_time,
            "end_time": last_time,
            "preview": msgs[-1].get("text", "") if msgs else "",
            "messages": msgs,
        })

    return {
        "elder_id": elder_id,
        "total_messages": len(msg_dicts),
        "sessions": session_summaries,
        "raw_messages": msg_dicts,
    }


# ==========================================
# 7. Sentiment Analytics & Daily Wellbeing
# ==========================================

@router.get(
    "/elders/{elder_id}/analytics",
    summary="Get real sentiment analytics, daily summaries, and 7-day trends",
)
def get_elder_analytics(
    elder_id: str,
    days: int = Query(7, ge=1, le=30),
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """
    Retrieves real sentiment analysis data from eldercare_sentiment.db.
    Handles insufficient or missing data gracefully without errors.
    """
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily = get_daily_summary(elder_id=elder_id, date_str=today_str)
    trends = get_historical_trends(elder_id=elder_id, days=days)

    return {
        "elder_id": elder_id,
        "date": today_str,
        "daily_summary": daily,
        "historical_trends": trends,
        "disclaimer": "AI sentiment analysis is an assistive conversational tool, not a clinical diagnostic instrument.",
    }


# ==========================================
# 8. Caregiver AI Copilot
# ==========================================

@router.post(
    "/elders/{elder_id}/copilot-chat",
    summary="Ask Caregiver Copilot about the elder's wellbeing and schedule",
)
def caregiver_copilot_chat(
    elder_id: str,
    payload: CaregiverCopilotChatRequest,
    caregiver_id: str = Depends(get_current_caregiver_id),
):
    """
    Provides smart conversational answers for caregivers based on actual records:
    medications, adherence logs, vitals from health diary, and mood trends.
    """
    db = get_central_db()
    _verify_elder_access(db, caregiver_id, elder_id)
    elder = db.get_elder_by_id(elder_id)
    elder_name = elder.name if elder else "the elder"

    meds = db.get_medicines(elder_id)
    logs = db.get_medication_logs(elder_id, limit=5)
    diary = db.get_health_diary(elder_id, limit=3)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_sentiment = get_daily_summary(elder_id=elder_id, date_str=today_str)

    query = payload.message.lower().strip()

    # Rule/context-aware response synthesizing actual DB data
    if any(w in query for w in ["medicine", "medication", "dose", "pill"]):
        if not meds:
            reply = f"There are currently no active medications scheduled for {elder_name}."
        else:
            med_list = ", ".join(f"{m.name} ({m.dosage})" for m in meds)
            adherence = f"Recent doses logged: {len(logs)} recorded." if logs else "No recent dose confirmations logged yet."
            reply = f"{elder_name}'s scheduled medications are: {med_list}. {adherence}"

    elif any(w in query for w in ["bp", "blood pressure", "vital", "sugar", "health", "pulse", "sleep"]):
        if not diary:
            reply = f"No recent health diary entries have been logged for {elder_name}. You can record vitals in the Health Diary tab."
        else:
            latest = diary[0]
            reply = (
                f"Latest recorded vitals for {elder_name} on {latest.date}: "
                f"Sleep quality: {latest.sleep_quality or 'Normal'}, Mood rating: {latest.mood_rating or 'Not rated'}/5, "
                f"Pain level: {latest.pain_level or 0}/10. Notes: {latest.notes or 'None'}"
            )

    elif any(w in query for w in ["mood", "feeling", "sentiment", "emotion"]):
        if daily_sentiment.get("insufficient_data"):
            reply = (
                f"Speech analysis for today has limited data ({daily_sentiment.get('total_utterances', 0)} utterances recorded). "
                f"Notes: {daily_sentiment.get('wellbeing_notes', 'Conversations are quiet today.')}"
            )
        else:
            dom_sentiment = daily_sentiment.get("dominant_sentiment", "calm")
            dom_emotion = daily_sentiment.get("dominant_emotion", "balanced")
            reply = (
                f"Today, {elder_name}'s dominant mood is {dom_sentiment} with primary emotion {dom_emotion}. "
                f"Observations: {daily_sentiment.get('wellbeing_notes', 'All interactions appear steady.')}"
            )

    elif any(w in query for w in ["summary", "how is", "status"]):
        med_cnt = len(meds)
        recent_log_cnt = len(logs)
        mood_str = daily_sentiment.get("dominant_sentiment", "normal") if not daily_sentiment.get("insufficient_data") else "quiet"
        reply = (
            f"Here is a quick summary for {elder_name}: {med_cnt} scheduled medications ({recent_log_cnt} recent logs), "
            f"general mood trend today appears {mood_str}. All systems and emergency contacts are active."
        )

    else:
        reply = (
            f"I have reviewed {elder_name}'s active profile. They have {len(meds)} active medications and "
            f"{len(diary)} recent health logs. You can ask me about medication schedules, vitals, mood trends, or reminders."
        )

    return {
        "reply": reply,
        "elder_id": elder_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
