"""
DriveX - AWS S3 File Manager
Main application entry point
"""
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

# Ensure logs directory exists
Path("logs").mkdir(exist_ok=True)

SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 DriveX starting up...")
    yield
    # Shutdown
    print("🛑 DriveX shutting down...")


app = FastAPI(
    title="DriveX",
    description="AWS S3 File Manager",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable in production
    redoc_url=None,
)

# Session middleware (must be added before routes)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="drivex_session",
    max_age=int(os.getenv("SESSION_TIMEOUT", 3600)),
    same_site="lax",
    https_only=os.getenv("HTTPS_ONLY", "false").lower() == "true",
)

# CORS - restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Import and register routers
from app.routes.auth import router as auth_router
from app.routes.s3 import router as s3_router
from app.routes.dashboard import router as dashboard_router

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(s3_router, prefix="/api/s3", tags=["s3"])
app.include_router(dashboard_router, tags=["dashboard"])


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to login or dashboard"""
    if request.session.get("authenticated"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth/login")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("DEBUG", "false").lower() == "true",
        log_level="info",
    )
