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


# S3 user-defined metadata keys must be valid HTTP header token chars.
METADATA_KEY_PATTERN = re.compile(r'^[a-zA-Z0-9\-_]{1,128}$')
MAX_METADATA_VALUE_LEN = 1024       # generous per-value cap
MAX_METADATA_TOTAL_BYTES = 2048     # S3 hard limit on combined user metadata size

# System-defined metadata keys that map to boto3 ExtraArgs (not Metadata dict).
# Keys here are the canonical S3 header names as shown in the UI.
SYSTEM_METADATA_KEYS = {
    "Content-Type",
    "Cache-Control",
    "Content-Disposition",
    "Content-Encoding",
    "Content-Language",
    "Expires",
    "Website-Redirect-Location",
}

# Mapping from UI key name → boto3 ExtraArgs key name
SYSTEM_KEY_TO_BOTO3 = {
    "Content-Type":              "ContentType",
    "Cache-Control":             "CacheControl",
    "Content-Disposition":       "ContentDisposition",
    "Content-Encoding":          "ContentEncoding",
    "Content-Language":          "ContentLanguage",
    "Expires":                   "Expires",
    "Website-Redirect-Location": "WebsiteRedirectLocation",
}


def validate_system_metadata(raw: Optional[str]) -> dict:
    """
    Parse and validate system-defined metadata JSON (list of {key, value} objects).
    Returns a dict of boto3 ExtraArgs keys → values  (e.g. {"ContentType": "image/png"}).
    Content-Type is returned separately so the caller can override the auto-detected type.
    """
    if not raw or not raw.strip():
        return {}

    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="System metadata must be valid JSON")

    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="System metadata must be a JSON array")

    boto3_args = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key   = str(entry.get("key",   "")).strip()
        value = str(entry.get("value", "")).strip()

        if not key:
            continue
        if key not in SYSTEM_METADATA_KEYS:
            raise HTTPException(status_code=400,
                detail=f"'{key}' is not a supported system-defined metadata key.")
        if not value:
            raise HTTPException(status_code=400,
                detail=f"System metadata value for '{key}' cannot be empty.")
        if len(value) > MAX_METADATA_VALUE_LEN:
            raise HTTPException(status_code=400,
                detail=f"System metadata value for '{key}' is too long (max {MAX_METADATA_VALUE_LEN} chars).")
        if not value.isascii():
            raise HTTPException(status_code=400,
                detail=f"System metadata value for '{key}' must be ASCII text.")
        if DANGEROUS_PATH_PATTERNS.search(value):
            raise HTTPException(status_code=400,
                detail=f"Invalid characters in system metadata value for '{key}'.")

        boto3_key = SYSTEM_KEY_TO_BOTO3[key]
        boto3_args[boto3_key] = value

    return boto3_args


def validate_user_metadata(raw: Optional[str]) -> dict:
    """
    Parse and validate user-defined metadata JSON (list of {key, value} objects).
    Returns a clean {str: str} dict for boto3's ExtraArgs["Metadata"].
    Keys are lowercased to match S3's normalisation behaviour.
    """
    if not raw or not raw.strip():
        return {}

    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="User metadata must be valid JSON")

    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="User metadata must be a JSON array")

    if len(entries) > 20:
        raise HTTPException(status_code=400, detail="Too many user metadata fields (max 20)")

    clean = {}
    total_size = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key   = str(entry.get("key",   "")).strip()
        value = str(entry.get("value", "")).strip()

        if not key and not value:
            continue  # silently skip fully empty rows from the UI

        if not key:
            raise HTTPException(status_code=400, detail="User metadata key cannot be empty.")

        if not METADATA_KEY_PATTERN.match(key):
            raise HTTPException(status_code=400,
                detail=f"Invalid user metadata key '{key}'. "
                       f"Use only letters, numbers, hyphens, and underscores (max 128 chars).")
        if not value:
            raise HTTPException(status_code=400,
                detail=f"User metadata value for '{key}' cannot be empty.")
        if len(value) > MAX_METADATA_VALUE_LEN:
            raise HTTPException(status_code=400,
                detail=f"User metadata value for '{key}' is too long (max {MAX_METADATA_VALUE_LEN} chars).")
        if not value.isascii():
            raise HTTPException(status_code=400,
                detail=f"User metadata value for '{key}' must be ASCII text.")
        if DANGEROUS_PATH_PATTERNS.search(value):
            raise HTTPException(status_code=400,
                detail=f"Invalid characters in user metadata value for '{key}'.")

        total_size += len(key) + len(value)
        if total_size > MAX_METADATA_TOTAL_BYTES:
            raise HTTPException(status_code=400,
                detail="Combined user metadata exceeds the 2 KB S3 limit.")

        lowered = key.lower()
        if lowered in clean:
            raise HTTPException(status_code=400,
                detail=f"Duplicate user metadata key '{key}'. Each key must appear only once.")
        clean[lowered] = value   # S3 normalises metadata keys to lowercase

    return clean


# Keep the old name as an alias so existing call-sites don't break while we migrate.
def validate_object_metadata(raw: Optional[str]) -> dict:
    """Legacy alias → delegates to validate_user_metadata."""
    return validate_user_metadata(raw)


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