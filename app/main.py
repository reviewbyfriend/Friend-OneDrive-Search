
import asyncio
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .db import (
    get_state,
    init_db,
    search_files,
    set_state,
    stats,
    sync_state,
)
from .graph import configured, exchange_code, get_access_token, get_auth_url
from .sync import reset_delta, sync_drive

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

AUTO_SYNC_MINUTES = max(1, int(os.getenv("AUTO_SYNC_MINUTES", "10")))
SYNC_LOCK = threading.Lock()

app = FastAPI(title="Friend OneDrive Search v0.5")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "temporary-session-secret-change-me"),
    same_site="lax",
    https_only=True,
)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

@app.on_event("startup")
async def startup():
    init_db()
    # If a previous deployment stopped during sync, clear the stale flag.
    set_state("sync_running", "0")
    asyncio.create_task(periodic_sync_loop())

def get_redirect_uri(request):
    configured_uri = os.getenv("REDIRECT_URI", "").strip()
    return configured_uri or str(request.url_for("auth_callback"))

def seconds_since(iso_value):
    if not iso_value:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_value)).total_seconds()
    except Exception:
        return None

def should_auto_sync():
    if not configured() or not get_access_token():
        return False
    state = sync_state()
    if state["sync_running"]:
        return False
    age = seconds_since(state["last_completed"])
    return age is None or age >= AUTO_SYNC_MINUTES * 60

def execute_sync(full_scan=False):
    if not SYNC_LOCK.acquire(blocking=False):
        return {"status": "already_running"}

    try:
        set_state("sync_running", "1")
        set_state("last_sync_started", utc_now_iso())

        token = get_access_token()
        if not token:
            result = {"status": "not_connected"}
            set_state("last_sync_result", json.dumps(result, ensure_ascii=False))
            return result

        if full_scan:
            reset_delta()

        result = sync_drive(token)
        result["status"] = "completed"
        result["mode"] = "full" if full_scan else "delta"

        set_state("last_sync_completed", utc_now_iso())
        set_state("last_sync_result", json.dumps(result, ensure_ascii=False))
        return result
    except Exception as exc:
        result = {"status": "error", "error": str(exc)[:500]}
        set_state("last_sync_result", json.dumps(result, ensure_ascii=False))
        return result
    finally:
        set_state("sync_running", "0")
        SYNC_LOCK.release()

async def periodic_sync_loop():
    # Give the app time to finish startup.
    await asyncio.sleep(15)
    while True:
        try:
            if should_auto_sync():
                await asyncio.to_thread(execute_sync, False)
        except Exception:
            pass
        await asyncio.sleep(AUTO_SYNC_MINUTES * 60)

def trigger_auto_sync(background_tasks: BackgroundTasks):
    if should_auto_sync():
        background_tasks.add_task(execute_sync, False)

@app.get("/", response_class=HTMLResponse)
def home(request: Request, background_tasks: BackgroundTasks, q: str = ""):
    token = get_access_token()
    if token:
        trigger_auto_sync(background_tasks)

    results = search_files(q) if q else []
    return templates.TemplateResponse("index.html", {
        "request": request,
        "q": q,
        "results": results,
        "stats": stats(),
        "sync": sync_state(),
        "auto_sync_minutes": AUTO_SYNC_MINUTES,
        "ms_configured": configured(),
        "connected": bool(token),
    })

@app.get("/login")
def login(request: Request):
    if not configured():
        return RedirectResponse("/?error=missing_config", status_code=303)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(get_auth_url(get_redirect_uri(request), state))

@app.get("/auth/callback", name="auth_callback")
def auth_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if error:
        return HTMLResponse(
            f"<h3>Microsoft login error</h3><pre>{error}: {error_description}</pre>",
            400,
        )
    if not state or state != request.session.get("oauth_state"):
        return HTMLResponse(
            "<h3>OAuth state ไม่ถูกต้อง กรุณาเริ่มเชื่อมใหม่</h3>", 400
        )

    result = exchange_code(code, get_redirect_uri(request))
    if "access_token" not in result:
        return HTMLResponse(
            f"<h3>รับ token ไม่สำเร็จ</h3><pre>{result}</pre>", 400
        )

    # First sync begins automatically after successful connection.
    background_tasks.add_task(execute_sync, False)
    return RedirectResponse("/?message=connected", status_code=303)

@app.post("/sync")
def run_sync(background_tasks: BackgroundTasks):
    if not get_access_token():
        return RedirectResponse("/login", status_code=303)
    background_tasks.add_task(execute_sync, False)
    return RedirectResponse("/?message=sync_started", status_code=303)

@app.post("/rescan")
def rescan(background_tasks: BackgroundTasks):
    if not get_access_token():
        return RedirectResponse("/login", status_code=303)
    background_tasks.add_task(execute_sync, True)
    return RedirectResponse("/?message=rescan_started", status_code=303)

@app.get("/sync-status")
def get_sync_status():
    return {
        "sync": sync_state(),
        "stats": stats(),
        "auto_sync_minutes": AUTO_SYNC_MINUTES,
    }

@app.get("/health")
def health():
    return {
        "ok": True,
        "microsoft_configured": configured(),
        "onedrive_connected": bool(get_access_token()),
        "sync": sync_state(),
        "stats": stats(),
    }
