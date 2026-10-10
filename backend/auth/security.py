"""
Authentication & Cryptographic Security Module.
Provides:
- Salted password hashing (bcrypt)
- Secure JWT token creation and verification (HMAC-SHA256)
- Caregiver linking code generation (cryptographically random)
"""

import os
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import bcrypt
import jwt
from fastapi import HTTPException, status, Header

JWT_SECRET = os.getenv("JWT_SECRET", "eldercare-conversational-copilot-secure-jwt-secret-key-2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = int(os.getenv("JWT_EXPIRATION_DAYS", "30"))


def hash_password(password: str) -> str:
    """Hashes a plaintext password using salted bcrypt."""
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def generate_caregiver_linking_code() -> str:
    """Generates an elderly-friendly, readable 6-character caregiver linking code (e.g., CARE-4892)."""
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"CARE-{digits}"


def generate_elder_id() -> str:
    """Generates an immutable unique elder identifier."""
    token = secrets.token_hex(4)
    return f"elder_{datetime.now(timezone.utc).strftime('%Y%m')}_{token}"


def create_access_token(elder_id: str, login_identifier: str, name: str) -> str:
    """Generates an authenticated JWT bearer token valid for 30 days."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=JWT_EXPIRATION_DAYS)
    payload = {
        "sub": elder_id,
        "login_identifier": login_identifier,
        "name": name,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": "eldercare-copilot-auth",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decodes and validates an authenticated JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please log in again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )


def get_current_elder_id(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI Dependency: extracts and verifies elder_id from Authorization: Bearer <token>."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'.",
        )
    token = parts[1]
    payload = decode_access_token(token)
    elder_id = payload.get("sub")
    if not elder_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing elder identifier.",
        )
    return str(elder_id)


def create_caregiver_access_token(caregiver_id: str, email: str, full_name: str, role: str = "primary") -> str:
    """Generates an authenticated JWT bearer token for caregivers valid for 30 days."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=JWT_EXPIRATION_DAYS)
    payload = {
        "sub": caregiver_id,
        "email": email,
        "name": full_name,
        "role": role,
        "user_type": "caregiver",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": "eldercare-copilot-caregiver-auth",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_caregiver_id(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI Dependency: extracts and verifies caregiver_id from Authorization: Bearer <token>."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Caregiver login required.",
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'.",
        )
    token = parts[1]
    payload = decode_access_token(token)
    user_type = payload.get("user_type")
    if user_type != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caregiver authorization required for this operation.",
        )
    caregiver_id = payload.get("sub")
    if not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing caregiver identifier.",
        )
    return str(caregiver_id)

