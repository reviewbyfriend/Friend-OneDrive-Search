import os
import time
from pathlib import Path

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
SCOPES = ["User.Read", "Files.Read"]


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
        code,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
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


def graph_get(url, token, retries=6):
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=90,
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                wait = int(response.headers.get("Retry-After", "0") or 0)
                time.sleep(wait if wait > 0 else min(2 ** attempt, 30))
                last_error = RuntimeError(
                    f"Microsoft Graph temporary error {response.status_code}"
                )
                continue
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            time.sleep(min(2 ** attempt, 30))
    raise last_error or RuntimeError("Microsoft Graph request failed")


def download_item(item_id, token):
    response = requests.get(
        f"{GRAPH}/me/drive/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    response.raise_for_status()
    return response.content


def delta_start_url():
    return (
        f"{GRAPH}/me/drive/root/delta"
        "?$select=id,name,size,lastModifiedDateTime,webUrl,"
        "file,folder,parentReference,deleted"
        "&$top=200"
    )


def iter_delta_pages(token, start_url=None):
    """Yield (items, next_link, delta_link) one Graph page at a time."""
    url = start_url or delta_start_url()
    while url:
        payload = graph_get(url, token)
        items = payload.get("value", [])
        next_link = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink")
        yield items, next_link, delta_link
        url = next_link
