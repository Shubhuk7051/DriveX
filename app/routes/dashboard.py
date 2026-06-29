"""
DriveX Dashboard Routes
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security import require_auth, generate_csrf_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
@require_auth
async def dashboard(request: Request):
    """Main dashboard page."""
    buckets = request.session.get("buckets", [])
    warning = request.session.pop("warning", None)
    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "buckets": buckets,
            "region": request.session.get("region", ""),
            "identity": request.session.get("user_identity", ""),
            "csrf_token": csrf_token,
            "warning": warning,
        },
    )
