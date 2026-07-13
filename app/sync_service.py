"""Background content indexer (OCR Database).

Why the local index matters for this setup:
- Personal (MSA) OneDrive Search is unreliable for Thai text inside files,
  and it CANNOT see contents of folders shared from other accounts.
- So this index is the primary content search for shared case folders
  (e.g. bkc1) and for scanned PDFs/images, while Microsoft covers live
  filename search.

Behavior:
- Delta-based on the user's own drive (no forced full scan).
- Shared folders added as "shortcut to My files" appear as remoteItem stubs;
  we walk their contents recursively on the owner's drive and index them.
- Content is extracted for CONTENT_INDEX_EXTENSIONS (docx/xlsx/pdf/txt/csv
  + images with OCR). Unchanged files (same lastModifiedDateTime) are
  skipped, so re-runs over large shared folders are cheap.
"""
import os
import threading
from pathlib import Path

from .db import delete_file, get_file_meta, get_state, set_state, upsert_file
from .extractors import CONTENT_EXTENSIONS, extract_text
from .graph import download_item, iter_children, iter_delta

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "30"))

# File types whose CONTENT gets extracted into the local index.
CONTENT_INDEX_EXTENSIONS = {
    x.strip().lower()
    for x in os.getenv(
        "CONTENT_INDEX_EXTENSIONS",
        ".docx,.xlsx,.pdf,.txt,.csv,.jpg,.jpeg,.png,.tif,.tiff,.bmp,.webp",
    ).split(",")
    if x.strip()
}

LOCK = threading.Lock()


def _clean_path(parent_path, name):
    p = (parent_path or "")
    # /drive/root:/x or /drives/{id}/root:/x -> /x
    if ":" in p:
        p = p.split(":", 1)[1]
    return f"{p}/{name}".replace("//", "/")


def item_path(i):
    return _clean_path((i.get("parentReference") or {}).get("path", ""), i.get("name", ""))


def _index_file(token, meta, ext, size, out, drive_id=None):
    """Extract + upsert one file. Skips download when unchanged."""
    existing = get_file_meta(meta["item_id"])
    if existing and existing[0] == meta.get("modified_at") and existing[1] != "error":
        out["skipped_unchanged"] += 1
        return

    if ext not in CONTENT_INDEX_EXTENSIONS or ext not in CONTENT_EXTENSIONS:
        upsert_file(meta, "", "metadata_only", "ชนิดไฟล์นี้ค้นได้เฉพาะชื่อ")
        out["metadata_only"] += 1
        return

    if size > MAX_FILE_MB * 1024 * 1024:
        upsert_file(meta, "", "metadata_only", f"ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB")
        out["metadata_only"] += 1
        return

    try:
        txt = extract_text(download_item(meta["item_id"], token, drive_id=drive_id), ext).strip()
        if txt:
            upsert_file(meta, txt, "content_indexed", None)
            out["content_indexed"] += 1
        else:
            upsert_file(meta, "", "metadata_only", "ไม่พบข้อความในไฟล์")
            out["metadata_only"] += 1
    except Exception as e:
        upsert_file(meta, "", "error", str(e)[:500])
        out["errors"] += 1


def _walk_shared_folder(token, drive_id, item_id, base_path, out):
    """Recursively index a shared folder living on another account's drive."""
    stack = [(item_id, base_path)]
    while stack:
        folder_id, folder_path = stack.pop()
        for child in iter_children(token, drive_id, folder_id):
            cid = child.get("id")
            if not cid:
                continue
            name = child.get("name", "")
            path = f"{folder_path}/{name}".replace("//", "/")
            if child.get("folder") is not None:
                stack.append((cid, path))
                continue
            if child.get("file") is None:
                continue
            out["processed"] += 1
            ext = Path(name).suffix.lower()
            size = int(child.get("size") or 0)
            meta = {
                "item_id": cid,
                "name": name,
                "path": path,
                "web_url": child.get("webUrl"),
                "mime_type": (child.get("file") or {}).get("mimeType"),
                "extension": ext,
                "modified_at": child.get("lastModifiedDateTime"),
                "size": size,
            }
            _index_file(token, meta, ext, size, out, drive_id=drive_id)


def sync_drive(token, full_scan=False):
    if not LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        if full_scan:
            set_state("delta_url", "")
        delta = get_state("delta_url") or None
        out = {
            "status": "completed",
            "mode": "full" if full_scan else "delta",
            "processed": 0,
            "content_indexed": 0,
            "metadata_only": 0,
            "skipped_unchanged": 0,
            "shared_folders": 0,
            "deleted": 0,
            "errors": 0,
        }
        new = None
        shared_folders = []

        for i in iter_delta(token, delta):
            if "__delta_link__" in i:
                new = i["__delta_link__"]
                continue
            out["processed"] += 1
            iid = i.get("id")
            if not iid:
                continue
            if i.get("deleted") is not None:
                delete_file(iid)
                out["deleted"] += 1
                continue

            # Shortcut to a shared folder from another account.
            remote = i.get("remoteItem")
            if remote and remote.get("folder") is not None:
                r_parent = remote.get("parentReference") or {}
                r_drive = r_parent.get("driveId")
                r_id = remote.get("id")
                if r_drive and r_id:
                    shared_folders.append(
                        (r_drive, r_id, f"/{i.get('name','')}")
                    )
                continue

            if "folder" in i or "file" not in i:
                continue

            n = i.get("name", "")
            ext = Path(n).suffix.lower()
            size = int(i.get("size") or 0)
            meta = {
                "item_id": iid,
                "name": n,
                "path": item_path(i),
                "web_url": i.get("webUrl"),
                "mime_type": (i.get("file") or {}).get("mimeType"),
                "extension": ext,
                "modified_at": i.get("lastModifiedDateTime"),
                "size": size,
            }
            _index_file(token, meta, ext, size, out)

        # Remember shared folders across runs: remoteItem stubs only appear
        # in delta when first added/changed, but new files inside them must
        # be picked up on every run.
        import json as _json
        known = {}
        try:
            for f in _json.loads(get_state("shared_folders") or "[]"):
                known[(f["drive"], f["id"])] = f["path"]
        except Exception:
            known = {}
        for r_drive, r_id, base in shared_folders:
            known[(r_drive, r_id)] = base
        set_state(
            "shared_folders",
            _json.dumps(
                [{"drive": d, "id": i, "path": p} for (d, i), p in known.items()],
                ensure_ascii=False,
            ),
        )

        # Walk shared folders every run; unchanged files are skipped cheaply.
        for (r_drive, r_id), base in known.items():
            out["shared_folders"] += 1
            try:
                _walk_shared_folder(token, r_drive, r_id, base, out)
            except Exception as e:
                out["errors"] += 1
                print(f"[sync] shared folder walk failed ({base}): {e}")

        if new:
            set_state("delta_url", new)
        return out
    finally:
        LOCK.release()
