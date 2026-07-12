import os
from pathlib import Path

from .db import delete_file, get_state, set_state, upsert_file
from .extractors import SUPPORTED, extract_text
from .graph import download_item, iter_delta

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))
ALLOWED = {
    x.strip().lower()
    for x in os.getenv("SYNC_EXTENSIONS", ".docx,.xlsx,.pdf,.txt,.csv").split(",")
    if x.strip()
}

def item_path(item: dict) -> str:
    parent = (item.get("parentReference") or {}).get("path", "")
    parent = parent.replace("/drive/root:", "")
    return f"{parent}/{item.get('name','')}".replace("//", "/")

def sync_drive(token: str):
    delta_url = get_state("delta_url")
    processed = indexed = skipped = deleted = errors = 0
    new_delta = None

    for item in iter_delta(token, delta_url):
        if "__delta_link__" in item:
            new_delta = item["__delta_link__"]
            continue

        processed += 1
        item_id = item.get("id")
        if not item_id:
            continue

        if item.get("deleted") is not None:
            delete_file(item_id)
            deleted += 1
            continue

        if "folder" in item or "file" not in item:
            skipped += 1
            continue

        name = item.get("name", "")
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED or ext not in ALLOWED:
            skipped += 1
            continue

        size = int(item.get("size") or 0)
        meta = {
            "item_id": item_id,
            "name": name,
            "path": item_path(item),
            "web_url": item.get("webUrl"),
            "mime_type": (item.get("file") or {}).get("mimeType"),
            "extension": ext,
            "modified_at": item.get("lastModifiedDateTime"),
            "size": size,
        }

        if size > MAX_FILE_MB * 1024 * 1024:
            upsert_file(meta, "", status="error", error=f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
            errors += 1
            continue

        try:
            data = download_item(item_id, token)
            text = extract_text(data, ext).strip()
            if ext == ".pdf" and not text:
                upsert_file(meta, "", status="error", error="PDF ไม่มีข้อความ อาจเป็นไฟล์สแกน ต้องเพิ่ม OCR")
                errors += 1
            else:
                upsert_file(meta, text)
                indexed += 1
        except Exception as exc:
            upsert_file(meta, "", status="error", error=str(exc)[:500])
            errors += 1

    if new_delta:
        set_state("delta_url", new_delta)

    return {
        "processed": processed,
        "indexed": indexed,
        "skipped": skipped,
        "deleted": deleted,
        "errors": errors,
        "full_scan": not bool(delta_url),
    }

def reset_delta():
    set_state("delta_url", "")
