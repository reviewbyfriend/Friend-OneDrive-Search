import asyncio
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .db import (
    init_db,
    search_files,
    set_state,
    stats,
    sync_state,
    list_problem_files,
    suggestions
)
from .graph import (
    configured,
    exchange_code,
    get_access_token,
    get_auth_url
)
from .sync_service import sync_drive

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

AUTO_SYNC_MINUTES = max(1, int(os.getenv("AUTO_SYNC_MINUTES", "10")))

app = FastAPI(title="Friend OneDrive Search v1.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv(
        "SESSION_SECRET",
        "temporary-session-secret-change-this"
    ),
    same_site="lax",
    https_only=True
)

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def get_redirect_uri(request):
    return (
        os.getenv("REDIRECT_URI", "").strip()
        or str(request.url_for("auth_callback"))
    )

def run_sync(full_scan=False):
    set_state("sync_running", "1")
    set_state("last_sync_started", utc_now())

    try:
        token = get_access_token()
        if not token:
            result = {"status": "not_connected"}
        else:
            result = sync_drive(token, full_scan=full_scan)

        set_state(
            "last_sync_result",
            json.dumps(result, ensure_ascii=False)
        )

        if result.get("status") == "completed":
            set_state("last_sync_completed", utc_now())

        return result

    except Exception as exc:
        result = {"status": "error", "error": str(exc)[:500]}
        set_state(
            "last_sync_result",
            json.dumps(result, ensure_ascii=False)
        )
        return result

    finally:
        set_state("sync_running", "0")

async def periodic_sync_loop():
    await asyncio.sleep(20)

    while True:
        try:
            if get_access_token() and not sync_state()["sync_running"]:
                await asyncio.to_thread(run_sync, False)
        except Exception:
            pass

        await asyncio.sleep(AUTO_SYNC_MINUTES * 60)

@app.on_event("startup")
async def startup():
    init_db()
    set_state("sync_running", "0")
    asyncio.create_task(periodic_sync_loop())

@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = ""):
    connected = bool(get_access_token())
    results = search_files(q) if q else []

    return templates.TemplateResponse("index.html", {
        "request": request,
        "q": q,
        "results": results,
        "stats": stats(),
        "sync": sync_state(),
        "auto_sync_minutes": AUTO_SYNC_MINUTES,
        "ms_configured": configured(),
        "connected": connected
    })

@app.get("/login")
def login(request: Request):
    if not configured():
        return RedirectResponse(
            "/?error=missing_config",
            status_code=303
        )

    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(
        get_auth_url(get_redirect_uri(request), state)
    )

@app.get("/auth/callback", name="auth_callback")
def auth_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = ""
):
    if error:
        return HTMLResponse(
            f"<h3>Microsoft login error</h3>"
            f"<pre>{error}: {error_description}</pre>",
            status_code=400
        )

    if not state or state != request.session.get("oauth_state"):
        return HTMLResponse(
            "<h3>OAuth state ไม่ถูกต้อง กรุณาเชื่อมบัญชีใหม่</h3>",
            status_code=400
        )

    result = exchange_code(code, get_redirect_uri(request))

    if "access_token" not in result:
        return HTMLResponse(
            f"<h3>รับ token ไม่สำเร็จ</h3><pre>{result}</pre>",
            status_code=400
        )

    background_tasks.add_task(run_sync, False)
    return RedirectResponse(
        "/?message=connected",
        status_code=303
    )

@app.post("/sync")
def manual_sync(background_tasks: BackgroundTasks):
    if not get_access_token():
        return RedirectResponse("/login", status_code=303)

    background_tasks.add_task(run_sync, False)
    return RedirectResponse(
        "/?message=sync_started",
        status_code=303
    )

@app.post("/rescan")
def full_rescan(background_tasks: BackgroundTasks):
    if not get_access_token():
        return RedirectResponse("/login", status_code=303)

    background_tasks.add_task(run_sync, True)
    return RedirectResponse(
        "/?message=rescan_started",
        status_code=303
    )

@app.get("/problems", response_class=HTMLResponse)
def problems(request: Request):
    return templates.TemplateResponse("problems.html", {"request": request, "files": list_problem_files(), "stats": stats()})


@app.get("/suggest")
def suggest(q: str = ""):
    return {"suggestions": suggestions(q, limit=10)}

@app.get("/sync-status")
def sync_status():
    return {
        "sync": sync_state(),
        "stats": stats(),
        "auto_sync_minutes": AUTO_SYNC_MINUTES
    }

@app.get("/health")
def health():
    return {
        "ok": True,
        "microsoft_configured": configured(),
        "onedrive_connected": bool(get_access_token()),
        "sync": sync_state(),
        "stats": stats()
    }
