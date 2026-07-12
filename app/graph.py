import os
from pathlib import Path
import msal, requests

GRAPH = "https://graph.microsoft.com/v1.0"
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "token_cache.bin"

CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
TENANT = os.getenv("MICROSOFT_TENANT", "consumers")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"
SCOPES = ["User.Read", "Files.Read", "offline_access"]

def _load_cache():
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text(encoding="utf-8"))
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")

def build_app(cache=None):
    return msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY,
        client_credential=CLIENT_SECRET, token_cache=cache
    )

def get_auth_url(redirect_uri, state):
    cache = _load_cache()
    app = build_app(cache)
    url = app.get_authorization_request_url(
        SCOPES, redirect_uri=redirect_uri, state=state, prompt="select_account"
    )
    _save_cache(cache)
    return url

def exchange_code(code, redirect_uri):
    cache = _load_cache()
    app = build_app(cache)
    result = app.acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=redirect_uri
    )
    _save_cache(cache)
    return result

def get_access_token():
    cache = _load_cache()
    app = build_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    return result.get("access_token") if result else None

def logout_local():
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()

def graph_get(url, token):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=90)
    r.raise_for_status()
    return r.json()

def download_item(item_id, token):
    r = requests.get(
        f"{GRAPH}/me/drive/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=180
    )
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")

def iter_delta(token, delta_url=None):
    url = delta_url or (
        f"{GRAPH}/me/drive/root/delta"
        "?$select=id,name,size,lastModifiedDateTime,webUrl,file,folder,parentReference,deleted"
    )
    while url:
        payload = graph_get(url, token)
        for item in payload.get("value", []):
            yield item
        if payload.get("@odata.nextLink"):
            url = payload["@odata.nextLink"]
        else:
            yield {"__delta_link__": payload.get("@odata.deltaLink")}
            break
