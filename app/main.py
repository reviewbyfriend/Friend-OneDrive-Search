import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .db import init_db, search_files, stats
from .graph import exchange_code, get_access_token, get_auth_url, logout_local
from .sync import sync_drive, reset_delta

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="Friend OneDrive Search")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "change-me-now"),
    same_site="lax",
    https_only=os.getenv("REDIRECT_URI", "").startswith("https://"),
)

@app.on_event("startup")
def startup():
    init_db()

def redirect_uri(request: Request):
    env_uri = os.getenv("REDIRECT_URI")
    return env_uri or str(request.url_for("auth_callback"))

@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = ""):
    connected = bool(get_access_token())
    results = search_files(q) if q and connected else []
    return templates.TemplateResponse("index.html", {
        "request": request,
        "q": q,
        "results": results,
        "connected": connected,
        "stats": stats(),
    })

@app.get("/login")
def login(request: Request):
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(get_auth_url(redirect_uri(request), state))

@app.get("/auth/callback", name="auth_callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return HTMLResponse(f"<h3>เชื่อมบัญชีไม่สำเร็จ</h3><pre>{error}: {error_description}</pre>", 400)
    if not state or state != request.session.get("oauth_state"):
        return HTMLResponse("<h3>OAuth state ไม่ถูกต้อง กรุณาเริ่มเชื่อมบัญชีใหม่</h3>", 400)
    result = exchange_code(code, redirect_uri(request))
    if "access_token" not in result:
        return HTMLResponse(f"<h3>รับ token ไม่สำเร็จ</h3><pre>{result}</pre>", 400)
    return RedirectResponse("/?connected=1", status_code=303)

@app.post("/sync")
def run_sync():
    token = get_access_token()
    if not token:
        return RedirectResponse("/login", status_code=303)
    result = sync_drive(token)
    summary = (
        f"อ่าน {result['processed']} รายการ | ทำดัชนี {result['indexed']} | "
        f"ข้าม {result['skipped']} | ลบจากดัชนี {result['deleted']} | ผิดพลาด {result['errors']}"
    )
    return RedirectResponse(f"/?message={summary}", status_code=303)

@app.post("/reset-and-sync")
def reset_and_sync():
    token = get_access_token()
    if not token:
        return RedirectResponse("/login", status_code=303)
    reset_delta()
    result = sync_drive(token)
    return RedirectResponse(f"/?message=สแกนใหม่แล้ว ทำดัชนี {result['indexed']} ไฟล์", status_code=303)

@app.post("/logout")
def logout():
    logout_local()
    return RedirectResponse("/", status_code=303)

@app.get("/health")
def health():
    try:
        connected = bool(get_access_token())
    except Exception:
        connected = False
    return {"ok": True, "connected": connected, "stats": stats()}
