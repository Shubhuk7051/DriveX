"""
DriveX Security Utilities
Handles credential encryption, session management, CSRF, and audit logging
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, status

# Configure audit logger (never logs credentials)
audit_logger = logging.getLogger("drivex.audit")
audit_logger.setLevel(logging.INFO)

log_path = os.getenv("AUDIT_LOG_PATH", "logs/audit.log")
Path(log_path).parent.mkdir(parents=True, exist_ok=True)

file_handler = logging.FileHandler(log_path)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
)
audit_logger.addHandler(file_handler)

# Fernet encryption for session credential storage
_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not _ENCRYPTION_KEY:
    # Generate ephemeral key (credentials lost on restart — acceptable for dev)
    _ENCRYPTION_KEY = Fernet.generate_key().decode()
    audit_logger.warning("No ENCRYPTION_KEY set. Using ephemeral key (sessions will not survive restarts).")

try:
    _fernet = Fernet(_ENCRYPTION_KEY.encode() if isinstance(_ENCRYPTION_KEY, str) else _ENCRYPTION_KEY)
except Exception:
    _fernet = Fernet(Fernet.generate_key())
    audit_logger.error("Invalid ENCRYPTION_KEY format. Using ephemeral key.")


def encrypt_credentials(data: dict) -> str:
    """Encrypt credential dict to a safe string for session storage."""
    payload = json.dumps(data).encode()
    return _fernet.encrypt(payload).decode()


def decrypt_credentials(token: str) -> Optional[dict]:
    """Decrypt credential string from session storage."""
    try:
        payload = _fernet.decrypt(token.encode())
        return json.loads(payload)
    except (InvalidToken, Exception):
        return None


def generate_csrf_token(request: Request) -> str:
    """Generate a CSRF token tied to the session."""
    session_id = request.session.get("session_id", secrets.token_hex(16))
    request.session["session_id"] = session_id
    secret = os.getenv("SECRET_KEY", "drivex-secret")
    token = hmac.new(
        secret.encode(),
        session_id.encode(),
        hashlib.sha256
    ).hexdigest()
    request.session["csrf_token"] = token
    return token


def validate_csrf_token(request: Request, token: str) -> bool:
    """Validate the CSRF token from the request."""
    stored = request.session.get("csrf_token")
    if not stored or not token:
        return False
    return hmac.compare_digest(stored, token)


def get_session_credentials(request: Request) -> Optional[dict]:
    """Retrieve and decrypt AWS credentials from session."""
    encrypted = request.session.get("credentials")
    if not encrypted:
        return None
    creds = decrypt_credentials(encrypted)
    # Validate session hasn't expired
    created_at = request.session.get("created_at", 0)
    timeout = int(os.getenv("SESSION_TIMEOUT", 3600))
    if time.time() - created_at > timeout:
        request.session.clear()
        return None
    return creds


def require_auth(func):
    """Decorator to require authentication on route handlers."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("authenticated"):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/auth/login", status_code=302)
        creds = get_session_credentials(request)
        if not creds:
            request.session.clear()
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/auth/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper


def require_auth_api(request: Request) -> dict:
    """Dependency for API routes requiring authentication."""
    if not request.session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    creds = get_session_credentials(request)
    if not creds:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired"
        )
    return creds


# --- Path / Key Validation ---

DANGEROUS_PATH_PATTERNS = re.compile(
    r'(\.\./|\.\.\\|%2e%2e|%252e%252e|/etc/|/proc/|\\windows\\)',
    re.IGNORECASE
)

VALID_S3_KEY_PATTERN = re.compile(r'^[a-zA-Z0-9!_.*\'()\-/@# ]+$')


def validate_s3_key(key: str) -> str:
    """Validate and sanitize an S3 object key. Raises on invalid input."""
    if not key:
        raise HTTPException(status_code=400, detail="Object key cannot be empty")
    if len(key) > 1024:
        raise HTTPException(status_code=400, detail="Object key too long (max 1024 chars)")
    if DANGEROUS_PATH_PATTERNS.search(key):
        raise HTTPException(status_code=400, detail="Invalid characters in object key")
    # Prevent absolute paths
    if key.startswith('/'):
        key = key.lstrip('/')
    return key


def validate_bucket_name(bucket: str, allowed_buckets: list) -> str:
    """Ensure the requested bucket is in the user's allowed list."""
    bucket = bucket.strip()
    if bucket not in allowed_buckets:
        raise HTTPException(
            status_code=403,
            detail=f"Access to bucket '{bucket}' is not permitted in this session"
        )
    return bucket


def sanitize_filename(filename: str) -> str:
    """Remove dangerous characters from uploaded filenames."""
    # Remove path separators and null bytes
    filename = re.sub(r'[/\\:\*\?"<>\|]', '_', filename)
    filename = filename.replace('\x00', '')
    # Limit length
    if len(filename) > 255:
        ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''
        filename = filename[:250] + ('.' + ext if ext else '')
    return filename or 'unnamed_file'


# --- Audit Logging ---

def audit_log(request: Request, action: str, bucket: str = None,
              key: str = None, status: str = "SUCCESS", detail: str = None):
    """
    Write an audit log entry. Never logs AWS credentials.
    """
    user_identity = request.session.get("user_identity", "unknown")
    ip = request.client.host if request.client else "unknown"
    entry = {
        "action": action,
        "identity": user_identity,  # ARN from STS, not keys
        "ip": ip,
        "bucket": bucket or "-",
        "key": key or "-",
        "status": status,
        "detail": detail or "-",
        "timestamp": datetime.utcnow().isoformat(),
    }
    log_line = " | ".join(f"{k}={v}" for k, v in entry.items())
    if status == "SUCCESS":
        audit_logger.info(log_line)
    else:
        audit_logger.warning(log_line)
