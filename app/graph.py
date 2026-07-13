import html
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

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

# These delegated scopes cover the signed-in user's OneDrive and SharePoint
# content. MSAL handles the reserved offline_access scope automatically.
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


def _request(method, url, token, *, json_body=None, timeout=90, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=json_body,
                timeout=timeout,
            )
            if response.status_code == 429 or response.status_code >= 500:
                wait = int(response.headers.get("Retry-After", "0") or 0)
                time.sleep(wait if wait > 0 else min(2 ** attempt, 8))
                last_error = requests.HTTPError(
                    f"Microsoft Graph {response.status_code}: {response.text[:300]}"
                )
                continue
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
    raise last_error or RuntimeError("Microsoft Graph request failed")


def graph_get(url, token):
    return _request("GET", url, token).json()


def graph_post(url, token, body):
    return _request("POST", url, token, json_body=body).json()


def get_account(token):
    payload = graph_get(f"{GRAPH}/me?$select=displayName,mail,userPrincipalName", token)
    return {
        "name": payload.get("displayName") or "",
        "email": payload.get("mail") or payload.get("userPrincipalName") or "",
    }


def download_item(item_id, token, drive_id=None):
    if drive_id:
        url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/content"
    else:
        url = f"{GRAPH}/me/drive/items/{item_id}/content"
    return _request("GET", url, token, timeout=180).content


def _clean_summary(summary):
    """Keep Microsoft hit highlighting while escaping all other HTML."""
    summary = summary or ""
    summary = summary.replace("<c0>", "[[[MARK]]]").replace("</c0>", "[[[/MARK]]]")
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = html.escape(summary)
    return (
        summary.replace("[[[MARK]]]", "<mark>")
        .replace("[[[/MARK]]]", "</mark>")
        .strip()
    )


def _resource_to_result(resource, hit=None):
    parent = resource.get("parentReference") or {}
    drive_id = parent.get("driveId") or ""
    item_id = resource.get("id") or (hit or {}).get("hitId") or ""
    name = resource.get("name") or "ไม่ทราบชื่อไฟล์"
    path = parent.get("path") or ""
    file_info = resource.get("file") or {}
    return {
        "item_id": item_id,
        "drive_id": drive_id,
        "name": name,
        "path": path,
        "web_url": resource.get("webUrl") or "",
        "mime_type": file_info.get("mimeType") or "",
        "extension": Path(name).suffix.lower(),
        "modified_at": resource.get("lastModifiedDateTime") or "",
        "size": int(resource.get("size") or 0),
        "status": "microsoft_search",
        "source": "Microsoft Search",
        "snippet": _clean_summary((hit or {}).get("summary") or ""),
        "rank": (hit or {}).get("rank") or 0,
    }


def microsoft_search(token, query, *, page_size=50, max_results=200):
    """Search Microsoft 365's existing index without crawling every file.

    Uses POST /search/query first. If that endpoint is unavailable for the
    connected account, falls back to the OneDrive driveItem search endpoint.
    """
    query = (query or "").strip()
    if not query:
        return {"results": [], "total": 0, "provider": "none"}

    results = []
    total = 0
    try:
        for offset in range(0, max_results, page_size):
            body = {
                "requests": [
                    {
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": query},
                        "from": offset,
                        "size": min(page_size, max_results - offset),
                        "fields": [
                            "id",
                            "name",
                            "webUrl",
                            "size",
                            "lastModifiedDateTime",
                            "file",
                            "folder",
                            "parentReference",
                        ],
                    }
                ]
            }
            payload = graph_post(f"{GRAPH}/search/query", token, body)
            containers = []
            for response in payload.get("value", []):
                containers.extend(response.get("hitsContainers") or [])
            page_hits = []
            more = False
            for container in containers:
                total = max(total, int(container.get("total") or 0))
                page_hits.extend(container.get("hits") or [])
                more = more or bool(container.get("moreResultsAvailable"))
            for hit in page_hits:
                resource = hit.get("resource") or {}
                if resource.get("folder") is not None:
                    continue
                results.append(_resource_to_result(resource, hit))
            if not more or not page_hits:
                break
        return {"results": results[:max_results], "total": total or len(results), "provider": "Microsoft Search"}
    except Exception as search_error:
        # Personal Microsoft accounts can behave differently. This endpoint
        # still searches the signed-in user's drive hierarchy and is a useful
        # fallback for names and Microsoft-indexed content.
        escaped = query.replace("'", "''")
        url = (
            f"{GRAPH}/me/drive/root/search(q='{quote(escaped)}')"
            "?$top=200&$select=id,name,webUrl,size,lastModifiedDateTime,file,folder,parentReference"
        )
        try:
            payload = graph_get(url, token)
            for resource in payload.get("value", []):
                if resource.get("folder") is not None:
                    continue
                results.append(_resource_to_result(resource))
            return {
                "results": results[:max_results],
                "total": len(results),
                "provider": "OneDrive Search",
                "warning": f"Microsoft Search API ใช้ไม่ได้ จึงใช้ OneDrive Search แทน: {str(search_error)[:180]}",
            }
        except Exception as fallback_error:
            raise RuntimeError(
                "ค้นสดจาก Microsoft ไม่สำเร็จ: "
                f"{str(search_error)[:180]} | fallback: {str(fallback_error)[:180]}"
            )


def iter_delta(token, delta_url=None):
    url = delta_url or (
        f"{GRAPH}/me/drive/root/delta"
        "?$select=id,name,size,lastModifiedDateTime,webUrl,file,folder,parentReference,deleted"
    )
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
