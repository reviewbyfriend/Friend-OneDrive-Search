
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("./data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "search.db"

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS files (
            item_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT,
            web_url TEXT,
            mime_type TEXT,
            extension TEXT,
            modified_at TEXT,
            size INTEGER DEFAULT 0,
            content TEXT DEFAULT '',
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'metadata_only',
            error TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
            item_id UNINDEXED,
            name,
            path,
            content,
            tokenize='unicode61'
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

def get_state(key):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_state(key, value):
    with connect() as conn:
        conn.execute("""
        INSERT INTO app_state(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))

def upsert_file(meta, content="", status="metadata_only", error=None):
    with connect() as conn:
        conn.execute("""
        INSERT INTO files(item_id,name,path,web_url,mime_type,extension,modified_at,size,content,indexed_at,status,error)
        VALUES(:item_id,:name,:path,:web_url,:mime_type,:extension,:modified_at,:size,:content,CURRENT_TIMESTAMP,:status,:error)
        ON CONFLICT(item_id) DO UPDATE SET
          name=excluded.name,
          path=excluded.path,
          web_url=excluded.web_url,
          mime_type=excluded.mime_type,
          extension=excluded.extension,
          modified_at=excluded.modified_at,
          size=excluded.size,
          content=excluded.content,
          indexed_at=CURRENT_TIMESTAMP,
          status=excluded.status,
          error=excluded.error
        """, {**meta, "content": content, "status": status, "error": error})
        conn.execute("DELETE FROM files_fts WHERE item_id=?", (meta["item_id"],))
        conn.execute("""
        INSERT INTO files_fts(item_id,name,path,content)
        VALUES(?,?,?,?)
        """, (meta["item_id"], meta["name"], meta.get("path") or "", content or ""))

def delete_file(item_id):
    with connect() as conn:
        conn.execute("DELETE FROM files WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM files_fts WHERE item_id=?", (item_id,))

def search_files(query, limit=100):
    query = (query or "").strip()
    if not query:
        return []
    terms = [x.replace('"', '""') for x in query.split() if x]
    fts_query = " AND ".join(f'"{x}"' for x in terms)
    with connect() as conn:
        rows = conn.execute("""
        SELECT f.*,
               snippet(files_fts, 3, '<mark>', '</mark>', ' … ', 32) AS snippet,
               bm25(files_fts, 3.0, 2.0, 1.0) AS score
        FROM files_fts
        JOIN files f ON f.item_id=files_fts.item_id
        WHERE files_fts MATCH ?
        ORDER BY score, f.modified_at DESC
        LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [dict(r) for r in rows]

def stats():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        content = conn.execute("SELECT COUNT(*) c FROM files WHERE status='content_indexed'").fetchone()["c"]
        metadata = conn.execute("SELECT COUNT(*) c FROM files WHERE status='metadata_only'").fetchone()["c"]
        errors = conn.execute("SELECT COUNT(*) c FROM files WHERE status='error'").fetchone()["c"]
        last = conn.execute("SELECT MAX(indexed_at) x FROM files").fetchone()["x"]
        return {
            "total": total,
            "content_indexed": content,
            "metadata_only": metadata,
            "errors": errors,
            "last_indexed": last
        }


def sync_state():
    return {
        "last_started": get_state("last_sync_started"),
        "last_completed": get_state("last_sync_completed"),
        "last_result": get_state("last_sync_result"),
        "sync_running": get_state("sync_running") == "1"
    }
