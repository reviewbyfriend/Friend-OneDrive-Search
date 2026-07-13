import logging
import os
import threading
from pathlib import Path

from .db import (
    delete_file,
    get_state,
    list_pending_files,
    set_state,
    upsert_file,
    upsert_metadata,
)
from .extractors import CONTENT_EXTENSIONS, extract_text
from .graph import delta_start_url, download_item, iter_delta_pages

log = logging.getLogger("friend-sync")
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))
CONTENT_BATCH_SIZE = max(1, int(os.getenv("CONTENT_BATCH_SIZE", "25")))
LOCK = threading.Lock()


def item_path(item):
    parent = (item.get("parentReference") or {}).get("path", "")
    parent = parent.replace("/drive/root:", "")
    return f"{parent}/{item.get('name', '')}".replace("//", "/")


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


def _index_pending(token, out):
    while True:
        batch = list_pending_files(CONTENT_BATCH_SIZE)
        if not batch:
            return
        for meta in batch:
            ext = (meta.get("extension") or "").lower()
            size = int(meta.get("size") or 0)
            if ext not in CONTENT_EXTENSIONS:
                upsert_file(meta, "", "metadata_only", "ยังไม่มีตัวอ่านข้อความชนิดนี้")
                out["metadata_only"] += 1
                continue
            if size > MAX_FILE_MB * 1024 * 1024:
                upsert_file(meta, "", "metadata_only", f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
                out["metadata_only"] += 1
                continue
            try:
                text = extract_text(download_item(meta["item_id"], token), ext).strip()
                if text:
                    upsert_file(meta, text, "content_indexed", None)
                    out["content_indexed"] += 1
                else:
                    upsert_file(meta, "", "metadata_only", "ไม่พบข้อความในไฟล์")
                    out["metadata_only"] += 1
            except Exception as exc:
                upsert_file(meta, "", "error", str(exc)[:500])
                out["errors"] += 1
            out["content_processed"] += 1
            if out["content_processed"] % 25 == 0:
                set_state("sync_progress", str(out))
                log.info("content progress: %s", out)


def sync_drive(token, full_scan=False):
    if not LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        if full_scan:
            start_url = delta_start_url()
            set_state("delta_url", "")
        else:
            start_url = get_state("delta_url") or delta_start_url()

        out = {
            "status": "completed",
            "mode": "full" if full_scan else "delta",
            "processed": 0,
            "files_seen": 0,
            "content_processed": 0,
            "content_indexed": 0,
            "metadata_only": 0,
            "deleted": 0,
            "errors": 0,
            "pages": 0,
        }

        log.info("sync started mode=%s", out["mode"])
        for items, next_link, delta_link in iter_delta_pages(token, start_url):
            out["pages"] += 1
            for item in items:
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
                elif size > MAX_FILE_MB * 1024 * 1024:
                    upsert_metadata(meta, "metadata_only", f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
                else:
                    upsert_metadata(meta, "pending", None)
                out["files_seen"] += 1

            # Critical: checkpoint every Graph page so a Railway restart resumes,
            # instead of beginning again at file 1.
            checkpoint = next_link or delta_link
            if checkpoint:
                set_state("delta_url", checkpoint)
            set_state("sync_progress", str(out))
            log.info(
                "metadata page=%s processed=%s files=%s next=%s",
                out["pages"], out["processed"], out["files_seen"], bool(next_link)
            )

        # Metadata for the whole drive is now present. Content extraction can be
        # slow, but it is resumable because pending rows remain in SQLite.
        _index_pending(token, out)
        set_state("sync_progress", str(out))
        log.info("sync completed: %s", out)
        return out
    finally:
        LOCK.release()
