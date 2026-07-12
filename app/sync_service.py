import os
import threading
from pathlib import Path

from .db import delete_file, get_state, set_state, upsert_file
from .extractors import SUPPORTED_CONTENT, extract_text
from .graph import download_item, iter_delta

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))
ALLOWED_CONTENT = {
    item.strip().lower()
    for item in os.getenv(
        "SYNC_EXTENSIONS",
        ".docx,.xlsx,.pdf,.txt,.csv"
    ).split(",")
    if item.strip()
}

SYNC_LOCK = threading.Lock()

def item_path(item):
    parent = (item.get("parentReference") or {}).get("path", "")
    parent = parent.replace("/drive/root:", "")
    return f"{parent}/{item.get('name', '')}".replace("//", "/")

def sync_drive(token, full_scan=False):
    if not SYNC_LOCK.acquire(blocking=False):
        return {"status": "already_running"}

    try:
        if full_scan:
            set_state("delta_url", "")

        delta_url = get_state("delta_url") or None
        result = {
            "status": "completed",
            "mode": "full" if full_scan else "delta",
            "processed": 0,
            "content_indexed": 0,
            "metadata_only": 0,
            "deleted": 0,
            "errors": 0
        }
        new_delta = None

        for item in iter_delta(token, delta_url):
            if "__delta_link__" in item:
                new_delta = item["__delta_link__"]
                continue

            result["processed"] += 1
            item_id = item.get("id")
            if not item_id:
                continue

            if item.get("deleted") is not None:
                delete_file(item_id)
                result["deleted"] += 1
                continue

            # Folders are not indexed as search results.
            if "folder" in item or "file" not in item:
                continue

            name = item.get("name", "")
            extension = Path(name).suffix.lower()
            size = int(item.get("size") or 0)

            meta = {
                "item_id": item_id,
                "name": name,
                "path": item_path(item),
                "web_url": item.get("webUrl"),
                "mime_type": (item.get("file") or {}).get("mimeType"),
                "extension": extension,
                "modified_at": item.get("lastModifiedDateTime"),
                "size": size
            }

            # Every file type is indexed by filename and folder path.
            if extension not in SUPPORTED_CONTENT or extension not in ALLOWED_CONTENT:
                upsert_file(meta, "", "metadata_only", None)
                result["metadata_only"] += 1
                continue

            if size > MAX_FILE_MB * 1024 * 1024:
                upsert_file(
                    meta,
                    "",
                    "metadata_only",
                    f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB"
                )
                result["metadata_only"] += 1
                continue

            try:
                raw = download_item(item_id, token)
                text = extract_text(raw, extension).strip()

                if text:
                    upsert_file(meta, text, "content_indexed", None)
                    result["content_indexed"] += 1
                else:
                    upsert_file(
                        meta,
                        "",
                        "metadata_only",
                        "ไม่พบข้อความในไฟล์"
                    )
                    result["metadata_only"] += 1
            except Exception as exc:
                # Keep metadata and OneDrive link even when content extraction fails.
                upsert_file(meta, "", "error", str(exc)[:500])
                result["errors"] += 1

        if new_delta:
            set_state("delta_url", new_delta)

        return result

    finally:
        SYNC_LOCK.release()
