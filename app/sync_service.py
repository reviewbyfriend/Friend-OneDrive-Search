import os
import threading
from collections import deque
from pathlib import Path

from .db import (
    delete_file,
    get_state,
    pending_files,
    set_state,
    upsert_file,
)
from .extractors import CONTENT_EXTENSIONS, extract_text
from .graph import (
    download_item,
    get_main_drive,
    get_me,
    iter_children,
    iter_delta,
    iter_shared_with_me,
)

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))
CONTENT_BATCH_SIZE = max(1, int(os.getenv("CONTENT_BATCH_SIZE", "25")))
LOCK = threading.Lock()


def _composite_id(drive_id, item_id):
    return f"{drive_id}:{item_id}"


def _remote_target(item, fallback_drive_id):
    remote = item.get("remoteItem") or {}
    if remote:
        parent = remote.get("parentReference") or {}
        return parent.get("driveId") or fallback_drive_id, remote.get("id") or item.get("id"), remote
    parent = item.get("parentReference") or {}
    return parent.get("driveId") or fallback_drive_id, item.get("id"), item


def _path_from_item(item, name, parent_path=""):
    if parent_path:
        return f"{parent_path.rstrip('/')}/{name}"
    parent = item.get("parentReference") or {}
    p = (parent.get("path") or "").replace("/drive/root:", "").replace("/root:", "")
    return f"{p}/{name}".replace("//", "/")


def _metadata(item, drive_id, item_id, source, path_override=None):
    remote = item.get("remoteItem") or {}
    actual = remote or item
    name = actual.get("name") or item.get("name") or ""
    ext = Path(name).suffix.lower()
    return {
        "item_id": _composite_id(drive_id, item_id),
        "drive_id": drive_id,
        "source": source,
        "name": name,
        "path": path_override or _path_from_item(actual, name),
        "web_url": actual.get("webUrl") or item.get("webUrl"),
        "mime_type": (actual.get("file") or {}).get("mimeType"),
        "extension": ext,
        "modified_at": actual.get("lastModifiedDateTime") or item.get("lastModifiedDateTime"),
        "size": int(actual.get("size") or item.get("size") or 0),
    }


def _store_file(item, fallback_drive_id, source, path_override=None):
    drive_id, item_id, actual = _remote_target(item, fallback_drive_id)
    if not drive_id or not item_id:
        return None
    meta = _metadata(item, drive_id, item_id, source, path_override)
    ext = meta["extension"]
    size = meta["size"]
    if ext not in CONTENT_EXTENSIONS:
        upsert_file(meta, "", "metadata_only", "ยังไม่มีตัวอ่านข้อความชนิดนี้")
        return "metadata_only"
    if size > MAX_FILE_MB * 1024 * 1024:
        upsert_file(meta, "", "metadata_only", f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
        return "metadata_only"
    # Metadata first. Content is extracted separately in small resumable batches.
    upsert_file(meta, "", "pending_content", None)
    return "pending_content"


def _scan_shared_tree(token, main_drive_id, out):
    queue = deque()
    visited_folders = set()

    for item in iter_shared_with_me(token):
        drive_id, item_id, actual = _remote_target(item, main_drive_id)
        if not drive_id or not item_id:
            continue
        name = actual.get("name") or item.get("name") or ""
        if actual.get("folder") is not None:
            queue.append((drive_id, item_id, f"/Shared/{name}"))
        elif actual.get("file") is not None:
            result = _store_file(item, drive_id, "shared", f"/Shared/{name}")
            out["processed"] += 1
            if result:
                out[result] += 1

    while queue:
        drive_id, folder_id, folder_path = queue.popleft()
        key = (drive_id, folder_id)
        if key in visited_folders:
            continue
        visited_folders.add(key)
        try:
            for child in iter_children(drive_id, folder_id, token):
                child_drive_id, child_id, actual = _remote_target(child, drive_id)
                if not child_drive_id or not child_id:
                    continue
                name = actual.get("name") or child.get("name") or ""
                child_path = f"{folder_path.rstrip('/')}/{name}"
                if actual.get("folder") is not None:
                    queue.append((child_drive_id, child_id, child_path))
                elif actual.get("file") is not None:
                    result = _store_file(child, child_drive_id, "shared", child_path)
                    out["processed"] += 1
                    if result:
                        out[result] += 1
        except Exception as exc:
            out["errors"] += 1
            set_state("last_shared_error", str(exc)[:500])


def _extract_pending(token, out, limit=CONTENT_BATCH_SIZE):
    for row in pending_files(limit):
        try:
            raw = download_item(row["drive_id"], row["item_id"].split(":", 1)[1], token)
            text = extract_text(raw, row["extension"]).strip()
            meta = dict(row)
            if text:
                upsert_file(meta, text, "content_indexed", None)
                out["content_indexed"] += 1
            else:
                upsert_file(meta, "", "metadata_only", "ไม่พบข้อความในไฟล์")
                out["metadata_only"] += 1
        except Exception as exc:
            meta = dict(row)
            upsert_file(meta, "", "error", str(exc)[:500])
            out["errors"] += 1


def sync_drive(token, full_scan=False):
    if not LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        me = get_me(token)
        main_drive = get_main_drive(token)
        main_drive_id = main_drive.get("id")
        set_state("connected_account", me.get("mail") or me.get("userPrincipalName") or me.get("displayName") or "")
        set_state("main_drive_id", main_drive_id or "")
        set_state("main_drive_name", main_drive.get("name") or "OneDrive")

        if full_scan:
            set_state("delta_url", "")
            set_state("shared_scan_done", "0")

        delta = get_state("delta_url") or None
        out = {
            "status": "completed",
            "mode": "full" if full_scan else "delta",
            "processed": 0,
            "pending_content": 0,
            "content_indexed": 0,
            "metadata_only": 0,
            "deleted": 0,
            "errors": 0,
        }
        new_delta = None

        # Main OneDrive metadata / delta.
        for item in iter_delta(token, delta):
            if "__delta_link__" in item:
                new_delta = item["__delta_link__"]
                continue
            out["processed"] += 1
            item_id = item.get("id")
            if not item_id:
                continue
            if item.get("deleted") is not None:
                delete_file(_composite_id(main_drive_id, item_id))
                out["deleted"] += 1
                continue
            actual = item.get("remoteItem") or item
            if actual.get("folder") is not None:
                # A shortcut to a shared folder needs explicit traversal.
                if item.get("remoteItem"):
                    drive_id, remote_id, remote = _remote_target(item, main_drive_id)
                    name = remote.get("name") or item.get("name") or "Shared"
                    try:
                        queue = deque([(drive_id, remote_id, f"/Shared shortcuts/{name}")])
                        seen = set()
                        while queue:
                            d_id, f_id, f_path = queue.popleft()
                            if (d_id, f_id) in seen:
                                continue
                            seen.add((d_id, f_id))
                            for child in iter_children(d_id, f_id, token):
                                c_drive, c_id, c_actual = _remote_target(child, d_id)
                                c_name = c_actual.get("name") or child.get("name") or ""
                                c_path = f"{f_path.rstrip('/')}/{c_name}"
                                if c_actual.get("folder") is not None:
                                    queue.append((c_drive, c_id, c_path))
                                elif c_actual.get("file") is not None:
                                    result = _store_file(child, c_drive, "shortcut", c_path)
                                    out["processed"] += 1
                                    if result:
                                        out[result] += 1
                    except Exception as exc:
                        out["errors"] += 1
                        set_state("last_shortcut_error", str(exc)[:500])
                continue
            if actual.get("file") is None:
                continue
            result = _store_file(item, main_drive_id, "main")
            if result:
                out[result] += 1

        if new_delta:
            set_state("delta_url", new_delta)

        # During a full scan, enumerate items shared directly with this account,
        # including SharePoint/Teams-backed folders.
        if full_scan or get_state("shared_scan_done") != "1":
            _scan_shared_tree(token, main_drive_id, out)
            set_state("shared_scan_done", "1")

        # Extract a small batch every run so the process can resume after restart.
        _extract_pending(token, out)
        return out
    finally:
        LOCK.release()
