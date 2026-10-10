"""
FastAPI Authentication & Elder Profile Router.
Exposes endpoints for:
- POST /api/auth/signup (Elder account creation)
- POST /api/auth/login (Elder authentication)
- GET  /api/auth/me (Session restoration & profile check)
- POST /api/auth/logout (Session termination)
- GET  /api/elders/{elder_id}/profile (Profile retrieval)
- POST /api/sync (Offline queue synchronization)
"""

from fastapi import APIRouter, HTTPException, Depends, status
from typing import Optional, List

from database.schemas import (
    ElderSignupRequest,
    ElderLoginRequest,
    AuthTokenResponse,
    ElderProfile,
    ElderInDB,
    SyncBatchRequest,
    SyncBatchResponse,
    ChatMessageRecord,
)
from database.mongodb import get_central_db
from .security import (
    hash_password,
    verify_password,
    generate_caregiver_linking_code,
    generate_elder_id,
    create_access_token,
    get_current_elder_id,
)

router = APIRouter(prefix="/api", tags=["Authentication & Elder Profiles"])


@router.post(
    "/auth/signup",
    response_model=AuthTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Elder account",
    description="Registers an elderly user, hashes password, assigns an immutable elder_id and a caregiver linking code.",
)
async def signup(payload: ElderSignupRequest):
    db = get_central_db()

    # Check if login identifier is already registered
    existing = db.get_elder_by_identifier(payload.login_identifier)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with identifier '{payload.login_identifier}' already exists. Please log in instead.",
        )

    # Generate unique IDs
    elder_id = generate_elder_id()
    linking_code = generate_caregiver_linking_code()

    # Hash password securely
    hashed_pw = hash_password(payload.password)

    elder_in_db = ElderInDB(
        elder_id=elder_id,
        login_identifier=payload.login_identifier.strip().lower(),
        hashed_password=hashed_pw,
        name=payload.name.strip(),
        preferred_language=payload.preferred_language,
        emergency_contact=payload.emergency_contact,
        caregiver_name=payload.caregiver_name,
        caregiver_linking_code=linking_code,
    )

    saved_profile = db.save_elder(elder_in_db)
    token = create_access_token(
        elder_id=saved_profile.elder_id,
        login_identifier=saved_profile.login_identifier,
        name=saved_profile.name,
    )

    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        elder=saved_profile,
    )


@router.post(
    "/auth/login",
    response_model=AuthTokenResponse,
    summary="Log in to existing Elder account",
    description="Verifies elder credentials and returns an authenticated JWT token.",
)
async def login(payload: ElderLoginRequest):
    db = get_central_db()
    elder = db.get_elder_by_identifier(payload.login_identifier)

    if not elder:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username/phone number or password.",
        )

    if not verify_password(payload.password, elder.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username/phone number or password.",
        )

    if not elder.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please consult your caregiver.",
        )

    token = create_access_token(
        elder_id=elder.elder_id,
        login_identifier=elder.login_identifier,
        name=elder.name,
    )

    profile = ElderProfile(
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

    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        elder=profile,
    )


@router.get(
    "/auth/me",
    response_model=ElderProfile,
    summary="Restore active session and profile",
    description="Restores an active session from the JWT token so the elder does not need to log in repeatedly.",
)
async def get_me(current_elder_id: str = Depends(get_current_elder_id)):
    db = get_central_db()
    profile = db.get_elder_by_id(current_elder_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Elder profile not found.",
        )
    return profile


@router.post(
    "/auth/logout",
    summary="Log out of session",
    description="Logs out the current session.",
)
async def logout(current_elder_id: str = Depends(get_current_elder_id)):
    return {
        "status": "success",
        "message": "Logged out successfully.",
        "elder_id": current_elder_id,
    }


@router.get(
    "/elders/{elder_id}/profile",
    response_model=ElderProfile,
    summary="Get elder profile by ID",
    description="Fetches an elder profile with authorization check ensuring caller has access.",
)
async def get_elder_profile(
    elder_id: str,
    current_elder_id: str = Depends(get_current_elder_id),
):
    # Enforce data isolation: elder can only view their own profile
    if current_elder_id != elder_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: cannot access another elder's profile.",
        )

    db = get_central_db()
    profile = db.get_elder_by_id(elder_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Elder profile not found.",
        )
    return profile


@router.post(
    "/sync",
    response_model=SyncBatchResponse,
    summary="Sync offline operations queue",
    description="Idempotent, duplicate-safe synchronization endpoint for offline chat messages, medication logs, and health diary.",
)
async def sync_offline_operations(
    payload: SyncBatchRequest,
    current_elder_id: str = Depends(get_current_elder_id),
):
    if current_elder_id != payload.elder_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot sync records for a different elder account.",
        )

    db = get_central_db()
    response = db.process_sync_batch(payload.elder_id, payload.operations)
    return response


@router.get(
    "/elders/{elder_id}/chat-history",
    response_model=List[ChatMessageRecord],
    summary="Get elder chat history",
    description="Retrieves chat history for an elder with strict authorization check.",
)
async def get_elder_chat_history(
    elder_id: str,
    limit: int = 50,
    current_elder_id: str = Depends(get_current_elder_id),
):
    if current_elder_id != elder_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: cannot view another elder's chat history.",
        )

    db = get_central_db()
    return db.get_chat_history(elder_id=elder_id, limit=limit)

