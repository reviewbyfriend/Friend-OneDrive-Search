import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
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
            status TEXT DEFAULT 'indexed',
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

def get_state(key: str):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_state(key: str, value: str):
    with connect() as conn:
        conn.execute("""
            INSERT INTO app_state(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))

def upsert_file(meta: dict, content: str, status="indexed", error=None):
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
        if status == "indexed":
            conn.execute(
                "INSERT INTO files_fts(item_id,name,path,content) VALUES(?,?,?,?)",
                (meta["item_id"], meta["name"], meta.get("path") or "", content)
            )

def delete_file(item_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM files WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM files_fts WHERE item_id=?", (item_id,))

def search_files(query: str, limit=50):
    query = query.strip()
    if not query:
        return []
    # Quote each term to avoid malformed FTS syntax from legal case numbers/slashes.
    terms = [t.replace('"', '""') for t in query.split() if t]
    fts_query = " AND ".join(f'"{t}"' for t in terms)
    with connect() as conn:
        rows = conn.execute("""
        SELECT f.*,
               snippet(files_fts, 3, '<mark>', '</mark>', ' … ', 28) AS snippet,
               bm25(files_fts, 2.0, 1.0, 1.0) AS score
        FROM files_fts
        JOIN files f ON f.item_id = files_fts.item_id
        WHERE files_fts MATCH ?
        ORDER BY score, f.modified_at DESC
        LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [dict(r) for r in rows]

def stats():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        indexed = conn.execute("SELECT COUNT(*) c FROM files WHERE status='indexed'").fetchone()["c"]
        errors = conn.execute("SELECT COUNT(*) c FROM files WHERE status='error'").fetchone()["c"]
        last = conn.execute("SELECT MAX(indexed_at) x FROM files").fetchone()["x"]
        return {"total": total, "indexed": indexed, "errors": errors, "last_indexed": last}
