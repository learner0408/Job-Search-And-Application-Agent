import os
import sys
import hmac
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import jwt
import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Request, HTTPException
from fastapi.responses import Response, RedirectResponse

from database import (
    create_user, get_user_by_email, get_user_by_provider, get_user_by_id,
    count_active_users, admit_user, link_oauth_identity, set_current_user,
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
SESSION_TTL_HOURS = 24
MAX_USERS = int(os.getenv("MAX_USERS", "2"))
OWNER_EMAIL = (os.getenv("OWNER_EMAIL") or "").strip().lower()
SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "")

COOKIE_NAME = "session"
_ph = PasswordHasher()

# Per-email login attempt tracking (Decision 29: per-email rate limiting)
_LOGIN_WINDOW_SECONDS = 600
_LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, dict] = {}


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2.

    Args:
        password: The plaintext password.

    Returns:
        Argon2 hash string.
    """
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2 hash.

    Args:
        password: The candidate plaintext password.
        password_hash: The stored Argon2 hash.

    Returns:
        True if the password matches the hash.
    """
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(user: dict) -> str:
    """Create a signed JWT session token for a user.

    Args:
        user: User dict with id and email.

    Returns:
        Signed JWT string valid for SESSION_TTL_HOURS.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "iat": now,
        "exp": now + timedelta(hours=SESSION_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT session token.

    Args:
        token: The JWT string.

    Returns:
        Payload dict if valid, else None.
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def _secure_cookie(request: Request) -> bool:
    """Determine whether cookies should be marked Secure.

    Args:
        request: The incoming request.

    Returns:
        True if the request arrived over HTTPS.
    """
    proto = request.headers.get("x-forwarded-proto", "http")
    return proto.split(",")[0].strip() == "https"


def set_session_cookie(response: Response, user: dict, request: Request) -> None:
    """Attach the session cookie to a response.

    Args:
        response: The FastAPI response to set the cookie on.
        user: User dict whose identity is encoded in the token.
        request: The incoming request (used to detect HTTPS).
    """
    token = create_token(user)
    max_age = SESSION_TTL_HOURS * 3600
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie on a response.

    Args:
        response: The FastAPI response to clear the cookie on.
    """
    response.delete_cookie(COOKIE_NAME, path="/")


def is_service_token_valid(token: str) -> bool:
    """Check a bearer token against the configured SERVICE_TOKEN.

    Args:
        token: The bearer token supplied by the caller.

    Returns:
        True if it matches the configured service token.
    """
    return bool(SERVICE_TOKEN) and hmac.compare_digest(token, SERVICE_TOKEN)


def get_current_user(request: Request) -> dict:
    """Resolve the authenticated user from the session cookie.

    Args:
        request: The incoming request.

    Returns:
        Dict with user_id, email, is_admin.

    Raises:
        HTTPException: 401 if the cookie is missing or invalid, or the user no longer exists.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = get_user_by_id(int(payload["sub"]))
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="Not authenticated")
    set_current_user(user["id"])
    return {"user_id": user["id"], "email": user["email"], "is_admin": user["is_admin"]}


def get_service_or_user(request: Request) -> dict:
    """Resolve a user from the session cookie or a valid SERVICE_TOKEN.

    The SERVICE_TOKEN grants owner/admin-level access for the local auto-apply agent
    (Decision 30); it resolves to the owner account.

    Args:
        request: The incoming request.

    Returns:
        Dict with user_id, email, is_admin.

    Raises:
        HTTPException: 401 if neither a valid cookie nor service token is present.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer ") and is_service_token_valid(auth[7:].strip()):
        user = get_owner_user()
        if user:
            set_current_user(user["id"])
            return {"user_id": user["id"], "email": user["email"], "is_admin": True}
        raise HTTPException(status_code=401, detail="Invalid service token")
    return get_current_user(request)


def get_owner_user() -> Optional[dict]:
    """Resolve the reserved owner/admin account.

    Looks up by OWNER_EMAIL first, then falls back to the first admin user.

    Returns:
        Owner user dict, or None if none exists.
    """
    if OWNER_EMAIL:
        user = get_user_by_email(OWNER_EMAIL)
        if user:
            return user
    conn = None
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import database
        conn = psycopg2.connect(database.DATABASE_URL, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE is_admin = TRUE ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()


def _is_owner(email: str) -> bool:
    """Check whether an email matches the reserved owner slot.

    Args:
        email: The email to check.

    Returns:
        True if it is the owner email.
    """
    return bool(OWNER_EMAIL) and email.strip().lower() == OWNER_EMAIL


def _check_capacity(email: str) -> tuple[str, bool]:
    """Determine registration status against the MAX_USERS cap.

    The owner slot is reserved and never blocked; otherwise the account is
    placed in the waitlist when the cap is full.

    Args:
        email: The email attempting to register.

    Returns:
        (status, is_admin) pair: status is 'active' or 'pending'.
    """
    is_owner = _is_owner(email)
    if is_owner:
        return "active", True
    if count_active_users() >= MAX_USERS:
        return "pending", False
    return "active", False


def _register_user(email: str, password_hash: Optional[str] = None, provider: str = "email", provider_id: Optional[str] = None) -> dict:
    """Create or fetch a user, applying the capacity/waitlist rules.

    Args:
        email: Canonical email.
        password_hash: Argon2 hash for password-based accounts.
        provider: Account provider ('email', 'google', 'github').
        provider_id: Provider identity when registering via OAuth.

    Returns:
        Dict with 'user' and 'status' ('created' | 'linked' | 'admitted').
    """
    existing = get_user_by_email(email)
    if existing:
        if existing["status"] == "pending" and count_active_users() < MAX_USERS:
            existing = admit_user(existing["id"])
            return {"user": existing, "status": "admitted"}
        return {"user": existing, "status": "linked"}

    status, is_admin = _check_capacity(email)
    user = create_user(
        email=email,
        password_hash=password_hash,
        provider=provider,
        provider_id=provider_id,
        is_admin=is_admin,
        status=status,
    )
    return {"user": user, "status": "created"}


# ── OAuth helpers ─────────────────────────────────────────────

def _public_base_url(request: Request) -> str:
    """Build the public base URL for the request (respecting proxy headers).

    Prefers X-Forwarded-Host so the callback URL uses the user-facing domain
    even when the proxy rewrites the Host header (changeOrigin).

    Args:
        request: The incoming request.

    Returns:
        Base URL like https://jobs4today.com.
    """
    proto = request.headers.get("x-forwarded-proto", "http").split(",")[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host")
    host = (forwarded_host or request.headers.get("host") or "localhost:8000").split(",")[0].strip()
    return f"{proto}://{host}"


def google_authorize_url(request: Request) -> str:
    """Build the Google OAuth authorize URL.

    Args:
        request: The incoming request (for the callback origin).

    Returns:
        Google authorization URL.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    redirect_uri = f"{_public_base_url(request)}/api/auth/google/callback"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    import urllib.parse
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def github_authorize_url(request: Request) -> str:
    """Build the GitHub OAuth authorize URL.

    Args:
        request: The incoming request (for the callback origin).

    Returns:
        GitHub authorization URL.
    """
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    redirect_uri = f"{_public_base_url(request)}/api/auth/github/callback"
    import urllib.parse
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
    }
    return "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params)


async def exchange_google_code(request: Request, code: str) -> dict:
    """Exchange an OAuth code for Google user info.

    Args:
        request: The incoming callback request (for redirect_uri).
        code: The authorization code from Google.

    Returns:
        Dict with email and google sub.

    Raises:
        HTTPException: 401 if the token exchange or userinfo call fails.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = f"{_public_base_url(request)}/api/auth/google/callback"
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            logger.error("Google token exchange failed: %s", token_resp.text[:300])
            raise HTTPException(status_code=401, detail="OAuth token exchange failed")
        tokens = token_resp.json()
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user info")
        info = userinfo_resp.json()
    return {"email": info.get("email", "").lower(), "provider_id": info.get("sub")}


async def exchange_github_code(request: Request, code: str) -> dict:
    """Exchange an OAuth code for GitHub user info.

    Args:
        request: The incoming callback request (for redirect_uri).
        code: The authorization code from GitHub.

    Returns:
        Dict with email and github id.

    Raises:
        HTTPException: 401 if the token exchange or userinfo call fails.
    """
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    redirect_uri = f"{_public_base_url(request)}/api/auth/github/callback"
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=401, detail="OAuth token exchange failed")
        tokens = token_resp.json()
        userinfo_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {tokens['access_token']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user info")
        info = userinfo_resp.json()
    email = (info.get("email") or "").lower()
    if not email:
        email = f"{info.get('login', 'user')}@users.noreply.github.com"
    return {"email": email, "provider_id": str(info.get("id"))}


def _check_login_rate(email: str) -> bool:
    """Check whether the email is currently rate-limited after too many failures.

    Args:
        email: The email attempting to log in.

    Returns:
        True if the attempt is allowed, False if temporarily locked out.
    """
    now = time.time()
    entry = _login_attempts.get(email)
    if not entry:
        return True
    if now - entry["window_start"] > _LOGIN_WINDOW_SECONDS:
        _login_attempts.pop(email, None)
        return True
    return entry["count"] < _LOGIN_MAX_ATTEMPTS


def _record_login_failure(email: str) -> None:
    """Record a failed login attempt per email.

    Args:
        email: The email that failed to log in.
    """
    now = time.time()
    entry = _login_attempts.get(email)
    if entry and now - entry["window_start"] <= _LOGIN_WINDOW_SECONDS:
        entry["count"] += 1
    else:
        _login_attempts[email] = {"count": 1, "window_start": now}


def _reset_login_attempts(email: str) -> None:
    """Clear the failed-attempt counter for an email after a successful login.

    Args:
        email: The email that logged in successfully.
    """
    _login_attempts.pop(email, None)