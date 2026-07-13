import os
import time
from pathlib import Path
from urllib.parse import urlencode

import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("./data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE = DATA_DIR / "token_cache.bin"

CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
TENANT = os.getenv("MICROSOFT_TENANT", "consumers").strip() or "consumers"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"

# Files.Read.All is required for shared items. Sites.Read.All covers files that
# are actually stored in SharePoint/Teams document libraries.
SCOPES = ["User.Read", "Files.Read.All", "Sites.Read.All"]


def configured():
    return bool(CLIENT_ID and CLIENT_SECRET)


def _load_cache():
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        try:
            cache.deserialize(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")


def _build_app(cache):
    if not configured():
        raise RuntimeError("Microsoft credentials are not configured")
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
        token_cache=cache,
    )


def get_auth_url(redirect_uri, state):
    cache = _load_cache()
    app = _build_app(cache)
    url = app.get_authorization_request_url(
        SCOPES,
        redirect_uri=redirect_uri,
        state=state,
        prompt="select_account",
    )
    _save_cache(cache)
    return url


def exchange_code(code, redirect_uri):
    cache = _load_cache()
    app = _build_app(cache)
    result = app.acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=redirect_uri
    )
    _save_cache(cache)
    return result


def get_access_token():
    if not configured():
        return None
    cache = _load_cache()
    app = _build_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    return result.get("access_token") if result else None


def _request(method, url, token, *, timeout=90, max_attempts=7):
    headers = {"Authorization": f"Bearer {token}"}
    last_error = None
    for attempt in range(max_attempts):
        try:
            response = requests.request(method, url, headers=headers, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise last_error or RuntimeError("Microsoft Graph request failed")


def graph_get(url, token):
    return _request("GET", url, token, timeout=90).json()


def iter_collection(url, token):
    while url:
        payload = graph_get(url, token)
        for item in payload.get("value", []):
            yield item
        url = payload.get("@odata.nextLink")


def get_me(token):
    return graph_get(f"{GRAPH}/me?$select=displayName,mail,userPrincipalName", token)


def get_main_drive(token):
    return graph_get(f"{GRAPH}/me/drive?$select=id,name,driveType,webUrl,owner,quota", token)


def download_item(drive_id, item_id, token):
    return _request(
        "GET",
        f"{GRAPH}/drives/{drive_id}/items/{item_id}/content",
        token,
        timeout=180,
    ).content


def iter_delta(token, delta_url=None):
    select = (
        "id,name,size,lastModifiedDateTime,webUrl,file,folder,parentReference,"
        "deleted,remoteItem,package"
    )
    url = delta_url or f"{GRAPH}/me/drive/root/delta?{urlencode({'$select': select, '$top': '200'})}"
    while url:
        payload = graph_get(url, token)
        for item in payload.get("value", []):
            yield item
        next_link = payload.get("@odata.nextLink")
        if next_link:
            url = next_link
            continue
        yield {"__delta_link__": payload.get("@odata.deltaLink")}
        break


def iter_shared_with_me(token):
    select = (
        "id,name,size,lastModifiedDateTime,webUrl,file,folder,parentReference,"
        "remoteItem,package"
    )
    url = f"{GRAPH}/me/drive/sharedWithMe?{urlencode({'$select': select, '$top': '200', 'allowexternal': 'true'})}"
    yield from iter_collection(url, token)


def iter_children(drive_id, item_id, token):
    select = (
        "id,name,size,lastModifiedDateTime,webUrl,file,folder,parentReference,"
        "remoteItem,package"
    )
    url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/children?{urlencode({'$select': select, '$top': '200'})}"
    yield from iter_collection(url, token)
