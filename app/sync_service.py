import os
import threading
import time
from pathlib import Path

from .db import (
    delete_file,
    get_state,
    pending_count,
    pending_files,
    set_state,
    update_content_result,
    upsert_metadata,
)
from .extractors import CONTENT_EXTENSIONS, extract_text
from .graph import delta_start_url, download_item, get_delta_page

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))
CONTENT_BATCH_SIZE = max(1, int(os.getenv("CONTENT_BATCH_SIZE", "40")))
MAX_RUN_SECONDS = max(60, int(os.getenv("MAX_SYNC_RUN_SECONDS", "420")))
LOCK = threading.Lock()


def item_path(item):
    parent = (item.get("parentReference") or {}).get("path", "")
    parent = parent.replace("/drive/root:", "")
    return f"{parent}/{item.get('name','')}".replace("//", "/")


def _meta(item):
    name = item.get("name", "")
    return {
        "item_id": item.get("id"),
        "name": name,
        "path": item_path(item),
        "web_url": item.get("webUrl"),
        "mime_type": (item.get("file") or {}).get("mimeType"),
        "extension": Path(name).suffix.lower(),
        "modified_at": item.get("lastModifiedDateTime"),
        "size": int(item.get("size") or 0),
    }


def _index_metadata_page(payload, out):
    for item in payload.get("value", []):
        out["processed"] += 1
        item_id = item.get("id")
        if not item_id:
            continue
        if item.get("deleted") is not None:
            delete_file(item_id)
            out["deleted"] += 1
            continue
        if "folder" in item or "file" not in item:
            continue

        meta = _meta(item)
        ext = meta["extension"]
        size = meta["size"]
        if ext not in CONTENT_EXTENSIONS:
            upsert_metadata(meta, "metadata_only", "ยังไม่มีตัวอ่านข้อความชนิดนี้")
            out["metadata_only"] += 1
        elif size > MAX_FILE_MB * 1024 * 1024:
            upsert_metadata(meta, "metadata_only", f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
            out["metadata_only"] += 1
        else:
            status = upsert_metadata(meta, "pending_content", None)
            if status == "pending_content":
                out["queued"] += 1
            else:
                out["unchanged"] += 1


def _crawl_metadata(token, full_scan, deadline, out):
    if full_scan:
        set_state("scan_cursor", "")
        set_state("delta_url", "")
        set_state("scan_kind", "full")

    cursor = get_state("scan_cursor")
    if not cursor:
        cursor = get_state("delta_url") or delta_start_url()
        set_state("scan_kind", "delta" if get_state("delta_url") else "full")

    while cursor and time.monotonic() < deadline:
        payload = get_delta_page(cursor, token)
        _index_metadata_page(payload, out)
        out["pages"] += 1

        next_link = payload.get("@odata.nextLink")
        if next_link:
            # Save after every Graph page so a Railway restart resumes here.
            set_state("scan_cursor", next_link)
            cursor = next_link
            print(
                f"metadata page={out['pages']} processed={out['processed']} "
                f"queued={out['queued']}",
                flush=True,
            )
            continue

        delta_link = payload.get("@odata.deltaLink")
        if delta_link:
            set_state("delta_url", delta_link)
        set_state("scan_cursor", "")
        set_state("scan_kind", "")
        out["metadata_complete"] = True
        cursor = None
        print(
            f"metadata complete processed={out['processed']} pending={pending_count()}",
            flush=True,
        )

    if cursor:
        out["metadata_complete"] = False
        out["resume_saved"] = True


def _process_content(token, deadline, out):
    while time.monotonic() < deadline:
        batch = pending_files(CONTENT_BATCH_SIZE)
        if not batch:
            break
        for meta in batch:
            if time.monotonic() >= deadline:
                break
            try:
                text = extract_text(
                    download_item(meta["item_id"], token), meta["extension"]
                ).strip()
                if text:
                    update_content_result(meta, text, "content_indexed", None)
                    out["content_indexed"] += 1
                else:
                    update_content_result(
                        meta, "", "metadata_only", "ไม่พบข้อความในไฟล์"
                    )
                    out["metadata_only"] += 1
            except Exception as exc:
                update_content_result(meta, "", "error", str(exc)[:500])
                out["errors"] += 1
            done = out["content_indexed"] + out["metadata_only"] + out["errors"]
            if done and done % 20 == 0:
                print(
                    f"content processed={done} pending={pending_count()}",
                    flush=True,
                )


def sync_drive(token, full_scan=False):
    if not LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    started = time.monotonic()
    deadline = started + MAX_RUN_SECONDS
    out = {
        "status": "completed",
        "mode": "full" if full_scan else "delta",
        "processed": 0,
        "pages": 0,
        "queued": 0,
        "unchanged": 0,
        "content_indexed": 0,
        "metadata_only": 0,
        "deleted": 0,
        "errors": 0,
        "metadata_complete": False,
        "resume_saved": False,
    }
    try:
        _crawl_metadata(token, full_scan, deadline, out)
        # Only start expensive downloads after the complete file list is saved.
        if out["metadata_complete"] and time.monotonic() < deadline:
            _process_content(token, deadline, out)
        out["pending"] = pending_count()
        out["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return out
    finally:
        LOCK.release()
