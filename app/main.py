import io, os, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.context import CryptContext

from .db import (
    init_db, search_files, stats, list_files, get_file,
    create_access_code, list_access_codes, get_access_code, active_access_codes,
    touch_access_code, set_access_code_active, increment_download,
    add_audit, list_audit_logs
)
from .graph import exchange_code, get_access_token, get_auth_url, logout_local, download_item
from .sync import sync_drive, reset_delta

BASE = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Friend OneDrive Secure Portal")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET","change-me"),
    same_site="lax",
    https_only=os.getenv("REDIRECT_URI","").startswith("https://")
)

@app.on_event("startup")
def startup():
    init_db()

def now_utc():
    return datetime.now(timezone.utc)

def redirect_uri(request):
    return os.getenv("REDIRECT_URI") or str(request.url_for("auth_callback"))

def is_owner(request):
    return request.session.get("owner") is True

def require_owner(request):
    if not is_owner(request):
        raise HTTPException(403, "Owner login required")

def current_guest(request):
    code_id = request.session.get("access_code_id")
    if not code_id:
        return None
    code = get_access_code(int(code_id))
    if not code or not code["is_active"]:
        request.session.pop("access_code_id", None)
        return None
    if datetime.fromisoformat(code["expires_at"]) <= now_utc():
        request.session.pop("access_code_id", None)
        return None
    return code

def allowed_for_file(code, file):
    if code["scope_type"] == "all":
        return True
    if code["scope_type"] == "folder":
        prefix = (code["scope_value"] or "").rstrip("/") + "/"
        return (file["path"] or "").startswith(prefix)
    if code["scope_type"] == "file":
        return file["item_id"] == code["scope_value"]
    return False

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    if is_owner(request):
        return RedirectResponse("/admin")
    if current_guest(request):
        return RedirectResponse("/portal")
    return templates.TemplateResponse("login.html", {"request":request, "error":None})

@app.post("/owner/login")
def owner_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if secrets.compare_digest(username, os.getenv("OWNER_USERNAME","friend")) and \
       secrets.compare_digest(password, os.getenv("OWNER_PASSWORD","change-this")):
        request.session.clear()
        request.session["owner"] = True
        return RedirectResponse("/admin",303)
    return templates.TemplateResponse("login.html", {
        "request":request, "error":"ชื่อผู้ใช้หรือรหัสผ่านเจ้าของไม่ถูกต้อง"
    }, status_code=401)

@app.post("/access/login")
def access_login(request: Request, code: str = Form(...)):
    entered = code.strip()
    match = None
    for row in active_access_codes():
        if pwd.verify(entered, row["code_hash"]):
            match = row
            break
    if not match:
        return templates.TemplateResponse("login.html", {
            "request":request, "error":"รหัสไม่ถูกต้องหรือหมดอายุแล้ว"
        }, status_code=401)
    request.session.clear()
    request.session["access_code_id"] = match["id"]
    touch_access_code(match["id"])
    add_audit(match["id"],"login",ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
    return RedirectResponse("/portal",303)

@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/",303)

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    require_owner(request)
    return templates.TemplateResponse("admin.html", {
        "request":request,
        "connected":bool(get_access_token()),
        "stats":stats(),
        "codes":list_access_codes(),
        "files":list_files(300),
        "logs":list_audit_logs(100)
    })

@app.get("/login/microsoft")
def login_ms(request: Request):
    require_owner(request)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(get_auth_url(redirect_uri(request),state))

@app.get("/auth/callback", name="auth_callback")
def auth_callback(request: Request, code: str="", state: str="", error: str="", error_description: str=""):
    require_owner(request)
    if error:
        return HTMLResponse(f"<h3>เชื่อมไม่สำเร็จ</h3><pre>{error}: {error_description}</pre>",400)
    if state != request.session.get("oauth_state"):
        return HTMLResponse("<h3>OAuth state ไม่ถูกต้อง</h3>",400)
    result = exchange_code(code, redirect_uri(request))
    if "access_token" not in result:
        return HTMLResponse(f"<pre>{result}</pre>",400)
    return RedirectResponse("/admin?message=เชื่อม OneDrive สำเร็จ",303)

@app.post("/admin/sync")
def admin_sync(request: Request):
    require_owner(request)
    token = get_access_token()
    if not token:
        return RedirectResponse("/login/microsoft",303)
    r = sync_drive(token)
    msg = f"ทำดัชนี {r['indexed']} ข้าม {r['skipped']} ผิดพลาด {r['errors']}"
    return RedirectResponse("/admin?message="+quote(msg),303)

@app.post("/admin/rescan")
def admin_rescan(request: Request):
    require_owner(request)
    token = get_access_token()
    if not token:
        return RedirectResponse("/login/microsoft",303)
    reset_delta()
    r = sync_drive(token)
    return RedirectResponse("/admin?message="+quote(f"สแกนใหม่ ทำดัชนี {r['indexed']} ไฟล์"),303)

@app.post("/admin/codes")
def create_code(
    request: Request,
    label: str = Form(...),
    duration_value: int = Form(...),
    duration_unit: str = Form(...),
    scope_type: str = Form("all"),
    scope_value: str = Form(""),
    can_search: str | None = Form(None),
    can_download: str | None = Form(None),
    max_downloads: str = Form("")
):
    require_owner(request)
    duration = timedelta(hours=duration_value) if duration_unit=="hours" else timedelta(days=duration_value)
    expires_at = (now_utc()+duration).isoformat()
    raw_code = f"{secrets.randbelow(1000000):06d}"
    max_dl = int(max_downloads) if max_downloads.strip() else None
    code_id = create_access_code(
        label.strip(), pwd.hash(raw_code), raw_code[-2:],
        scope_type, scope_value.strip() or None,
        bool(can_search), bool(can_download), max_dl, expires_at
    )
    request.session["last_created_code"] = raw_code
    request.session["last_created_id"] = code_id
    return RedirectResponse("/admin?created=1",303)

@app.post("/admin/codes/{code_id}/toggle")
def toggle_code(request: Request, code_id: int):
    require_owner(request)
    row = get_access_code(code_id)
    if not row:
        raise HTTPException(404)
    set_access_code_active(code_id, not bool(row["is_active"]))
    return RedirectResponse("/admin",303)

@app.get("/portal", response_class=HTMLResponse)
def portal(request: Request, q: str=""):
    code = current_guest(request)
    if not code:
        return RedirectResponse("/",303)
    results = []
    if q and code["can_search"]:
        results = search_files(q, code["scope_type"], code["scope_value"], 100)
        add_audit(code["id"],"search",file_name=q,
                  ip=request.client.host if request.client else None,
                  user_agent=request.headers.get("user-agent"))
    return templates.TemplateResponse("portal.html", {
        "request":request,"code":code,"q":q,"results":results
    })

@app.get("/download/{item_id}")
def download(request: Request, item_id: str):
    code = current_guest(request)
    if not code:
        return RedirectResponse("/",303)
    if not code["can_download"]:
        raise HTTPException(403,"รหัสนี้ไม่มีสิทธิ์ดาวน์โหลด")
    if code["max_downloads"] is not None and code["download_count"] >= code["max_downloads"]:
        raise HTTPException(403,"ใช้สิทธิ์ดาวน์โหลดครบแล้ว")
    file = get_file(item_id)
    if not file or not allowed_for_file(code,file):
        raise HTTPException(404)
    token = get_access_token()
    if not token:
        raise HTTPException(503,"OneDrive ยังไม่ได้เชื่อม หรือ token หมดอายุ")
    data, content_type = download_item(item_id, token)
    increment_download(code["id"])
    add_audit(code["id"],"download",item_id=item_id,file_name=file["name"],
              ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
    filename = quote(file["name"])
    headers = {"Content-Disposition":f"attachment; filename*=UTF-8''{filename}"}
    return StreamingResponse(io.BytesIO(data),media_type=content_type,headers=headers)

@app.get("/health")
def health():
    return {"ok":True,"connected":bool(get_access_token()),"stats":stats()}
