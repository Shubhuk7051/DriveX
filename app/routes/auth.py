"""
DriveX Auth Routes
Handles login, logout, and credential validation.
"""
import time

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security import (
    encrypt_credentials,
    generate_csrf_token,
    validate_csrf_token,
    audit_log,
)
from app.services.s3_service import validate_credentials, validate_bucket_access

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# All AWS regions
AWS_REGIONS = [
    ("us-east-1", "US East (N. Virginia)"),
    ("us-east-2", "US East (Ohio)"),
    ("us-west-1", "US West (N. California)"),
    ("us-west-2", "US West (Oregon)"),
    ("af-south-1", "Africa (Cape Town)"),
    ("ap-east-1", "Asia Pacific (Hong Kong)"),
    ("ap-south-1", "Asia Pacific (Mumbai)"),
    ("ap-south-2", "Asia Pacific (Hyderabad)"),
    ("ap-northeast-1", "Asia Pacific (Tokyo)"),
    ("ap-northeast-2", "Asia Pacific (Seoul)"),
    ("ap-northeast-3", "Asia Pacific (Osaka)"),
    ("ap-southeast-1", "Asia Pacific (Singapore)"),
    ("ap-southeast-2", "Asia Pacific (Sydney)"),
    ("ap-southeast-3", "Asia Pacific (Jakarta)"),
    ("ap-southeast-4", "Asia Pacific (Melbourne)"),
    ("ca-central-1", "Canada (Central)"),
    ("ca-west-1", "Canada West (Calgary)"),
    ("eu-central-1", "Europe (Frankfurt)"),
    ("eu-central-2", "Europe (Zurich)"),
    ("eu-west-1", "Europe (Ireland)"),
    ("eu-west-2", "Europe (London)"),
    ("eu-west-3", "Europe (Paris)"),
    ("eu-north-1", "Europe (Stockholm)"),
    ("eu-south-1", "Europe (Milan)"),
    ("eu-south-2", "Europe (Spain)"),
    ("il-central-1", "Israel (Tel Aviv)"),
    ("me-central-1", "Middle East (UAE)"),
    ("me-south-1", "Middle East (Bahrain)"),
    ("sa-east-1", "South America (São Paulo)"),
]


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page."""
    if request.session.get("authenticated"):
        return RedirectResponse(url="/dashboard", status_code=302)
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "regions": AWS_REGIONS,
            "csrf_token": csrf_token,
            "error": request.session.pop("login_error", None),
        },
    )


@router.post("/connect")
async def connect(
    request: Request,
    access_key: str = Form(...),
    secret_key: str = Form(...),
    region: str = Form(...),
    buckets: str = Form(...),
    csrf_token: str = Form(...),
):
    """Validate AWS credentials and establish session."""
    # CSRF validation
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # Validate region is in allowed list
    valid_regions = [r[0] for r in AWS_REGIONS]
    if region not in valid_regions:
        raise HTTPException(status_code=400, detail="Invalid AWS region")

    # Parse and clean bucket names
    bucket_list = [b.strip() for b in buckets.split(",") if b.strip()]
    if not bucket_list:
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "regions": AWS_REGIONS,
                "csrf_token": generate_csrf_token(request),
                "error": "Please enter at least one bucket name.",
            },
            status_code=400,
        )

    creds = {
        "access_key": access_key,
        "secret_key": secret_key,
        "region": region,
        "buckets": bucket_list,
    }

    # Step 1: Validate credentials via STS
    try:
        identity = validate_credentials(creds)
    except ValueError as e:
        audit_log(request, "LOGIN_FAILED", status="FAILURE", detail=str(e))
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "regions": AWS_REGIONS,
                "csrf_token": generate_csrf_token(request),
                "error": str(e),
            },
            status_code=401,
        )

    # Step 2: Validate bucket access for each bucket
    bucket_errors = []
    accessible_buckets = []
    for bucket in bucket_list:
        try:
            validate_bucket_access(creds, bucket)
            accessible_buckets.append(bucket)
        except ValueError as e:
            bucket_errors.append(str(e))

    if not accessible_buckets:
        error_msg = "Cannot access any of the provided buckets. " + " ".join(bucket_errors)
        audit_log(request, "LOGIN_FAILED", status="FAILURE", detail=error_msg)
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "regions": AWS_REGIONS,
                "csrf_token": generate_csrf_token(request),
                "error": error_msg,
            },
            status_code=403,
        )

    # Encrypt and store credentials (only accessible buckets)
    creds["buckets"] = accessible_buckets
    encrypted = encrypt_credentials(creds)

    # Set session
    request.session["authenticated"] = True
    request.session["credentials"] = encrypted
    request.session["created_at"] = time.time()
    request.session["user_identity"] = identity["arn"]
    request.session["region"] = region
    request.session["buckets"] = accessible_buckets

    audit_log(
        request,
        "LOGIN_SUCCESS",
        detail=f"Buckets: {','.join(accessible_buckets)} | Skipped: {len(bucket_errors)}",
    )

    # Warn about inaccessible buckets via session flash
    if bucket_errors:
        request.session["warning"] = f"Connected but {len(bucket_errors)} bucket(s) were inaccessible: {'; '.join(bucket_errors)}"

    return RedirectResponse(url="/dashboard", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    """Clear session and log out."""
    audit_log(request, "LOGOUT")
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)


@router.get("/logout")
async def logout_get(request: Request):
    """Handle GET logout (fallback)."""
    audit_log(request, "LOGOUT")
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=302)
