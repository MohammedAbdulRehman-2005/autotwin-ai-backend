"""
core/security.py
─────────────────
Production-ready security layer for AutoTwin AI.

Provides:
  - JWT creation & verification (python-jose)
  - Password hashing / verification (passlib bcrypt)
  - FastAPI dependency: get_current_user
  - Role-based access control: require_role(role)
  - Built-in demo user for out-of-the-box testing
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from core.config import settings

logger = logging.getLogger("autotwin_ai.security")

# ── OAuth2 scheme (token URL matches the login endpoint in routes.py) ─
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# ── Passlib bcrypt context ────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Built-in demo user (hashed at module load time) ──────────────────
_DEMO_USER_PLAIN_PASSWORD = "demo123"
_DEMO_USERS: Dict[str, Dict[str, Any]] = {
    "demo": {
        "username": "demo",
        "hashed_password": _pwd_context.hash(_DEMO_USER_PLAIN_PASSWORD),
        "role": "admin",
        "full_name": "Demo Administrator",
        "email": "demo@autotwin.ai",
        "disabled": False,
    }
}

logger.info("[Security] Demo user 'demo' registered (role=admin).")


# ══════════════════════════════════════════════════════════════
# Password utilities
# ══════════════════════════════════════════════════════════════

def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash of *plain_password*."""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if *plain_password* matches *hashed_password*."""
    return _pwd_context.verify(plain_password, hashed_password)


# ══════════════════════════════════════════════════════════════
# JWT utilities
# ══════════════════════════════════════════════════════════════

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Encode *data* into a signed JWT.

    Args:
        data:          Payload dict — typically {"sub": username, "role": role}.
        expires_delta: Override the default expiry window.

    Returns:
        Signed JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})

    token = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    logger.debug("[Security] JWT created for sub=%s exp=%s", data.get("sub"), expire)
    return token


def verify_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT.

    Returns:
        Decoded payload dict.

    Raises:
        HTTPException 401 if the token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise credentials_exception
        logger.debug("[Security] Token verified for sub=%s", username)
        return payload
    except JWTError as exc:
        logger.warning("[Security] Token verification failed: %s", exc)
        raise credentials_exception from exc


# ══════════════════════════════════════════════════════════════
# User helpers
# ══════════════════════════════════════════════════════════════

def get_user(username: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by username.
    In production replace this with an async DB call.
    """
    return _DEMO_USERS.get(username)


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Verify username + password.
    Returns the user dict on success, None on failure.
    """
    user = get_user(username)
    if not user:
        logger.warning("[Security] Auth failed — unknown user: %r", username)
        return None
    if not verify_password(password, user["hashed_password"]):
        logger.warning("[Security] Auth failed — wrong password for user: %r", username)
        return None
    logger.info("[Security] User authenticated: %r (role=%s)", username, user["role"])
    return user


# ══════════════════════════════════════════════════════════════
# FastAPI dependencies
# ══════════════════════════════════════════════════════════════

async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> Dict[str, Any]:
    """
    FastAPI dependency — resolves the Bearer token to a user dict.

    Usage::
        @router.get("/protected")
        async def endpoint(user = Depends(get_current_user)):
            ...
    """
    payload = verify_token(token)
    username: str = payload.get("sub", "")
    user = get_user(username)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )
    return user


def require_role(role: str):
    """
    FastAPI dependency factory — enforces role-based access control.

    Usage::
        @router.delete("/admin-only", dependencies=[Depends(require_role("admin"))])
        async def admin_endpoint(): ...
    """
    async def _check(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        user_role = current_user.get("role", "")
        if user_role != role:
            logger.warning(
                "[Security] RBAC rejected: user=%r has role=%r, required=%r",
                current_user.get("username"), user_role, role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: '{role}', your role: '{user_role}'.",
            )
        return current_user
    return _check


# ── Optional: token payload helper for route handlers ─────────
def decode_token_payload(token: str) -> Dict[str, Any]:
    """Thin wrapper around verify_token for explicit use in route handlers."""
    return verify_token(token)
