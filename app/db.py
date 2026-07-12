import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "portal.db"

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

        CREATE TABLE IF NOT EXISTS access_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            code_hint TEXT,
            scope_type TEXT NOT NULL DEFAULT 'all',
            scope_value TEXT,
            can_search INTEGER NOT NULL DEFAULT 1,
            can_download INTEGER NOT NULL DEFAULT 1,
            max_downloads INTEGER,
            download_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_code_id INTEGER,
            event_type TEXT NOT NULL,
            item_id TEXT,
            file_name TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
          name=excluded.name, path=excluded.path, web_url=excluded.web_url,
          mime_type=excluded.mime_type, extension=excluded.extension,
          modified_at=excluded.modified_at, size=excluded.size,
          content=excluded.content, indexed_at=CURRENT_TIMESTAMP,
          status=excluded.status, error=excluded.error
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

def _scope_sql(scope_type, scope_value):
    if scope_type == "folder" and scope_value:
        return " AND f.path LIKE ? ", [scope_value.rstrip("/") + "/%"]
    if scope_type == "file" and scope_value:
        return " AND f.item_id = ? ", [scope_value]
    return "", []

def search_files(query: str, scope_type="all", scope_value=None, limit=50):
    query = (query or "").strip()
    if not query:
        return []
    terms = [t.replace('"', '""') for t in query.split() if t]
    fts_query = " AND ".join(f'"{t}"' for t in terms)
    scope_sql, args = _scope_sql(scope_type, scope_value)
    sql = f"""
        SELECT f.*,
               snippet(files_fts, 3, '<mark>', '</mark>', ' … ', 28) AS snippet,
               bm25(files_fts, 2.0, 1.0, 1.0) AS score
        FROM files_fts
        JOIN files f ON f.item_id = files_fts.item_id
        WHERE files_fts MATCH ? {scope_sql}
        ORDER BY score, f.modified_at DESC
        LIMIT ?
    """
    with connect() as conn:
        rows = conn.execute(sql, [fts_query, *args, limit]).fetchall()
        return [dict(r) for r in rows]

def list_files(limit=200):
    with connect() as conn:
        rows = conn.execute("""
            SELECT item_id,name,path,extension,modified_at,size,status,error
            FROM files ORDER BY path LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

def get_file(item_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM files WHERE item_id=?", (item_id,)).fetchone()
        return dict(row) if row else None

def stats():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        indexed = conn.execute("SELECT COUNT(*) c FROM files WHERE status='indexed'").fetchone()["c"]
        errors = conn.execute("SELECT COUNT(*) c FROM files WHERE status='error'").fetchone()["c"]
        last = conn.execute("SELECT MAX(indexed_at) x FROM files").fetchone()["x"]
        return {"total": total, "indexed": indexed, "errors": errors, "last_indexed": last}

def create_access_code(label, code_hash, code_hint, scope_type, scope_value,
                       can_search, can_download, max_downloads, expires_at):
    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO access_codes(
              label,code_hash,code_hint,scope_type,scope_value,
              can_search,can_download,max_downloads,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
        """, (label, code_hash, code_hint, scope_type, scope_value,
              int(can_search), int(can_download), max_downloads, expires_at))
        return cur.lastrowid

def list_access_codes():
    with connect() as conn:
        rows = conn.execute("""
            SELECT * FROM access_codes ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

def get_access_code(code_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM access_codes WHERE id=?", (code_id,)).fetchone()
        return dict(row) if row else None

def active_access_codes():
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        rows = conn.execute("""
            SELECT * FROM access_codes
            WHERE is_active=1 AND expires_at > ?
            ORDER BY created_at DESC
        """, (now,)).fetchall()
        return [dict(r) for r in rows]

def touch_access_code(code_id: int):
    with connect() as conn:
        conn.execute("UPDATE access_codes SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (code_id,))

def set_access_code_active(code_id: int, active: bool):
    with connect() as conn:
        conn.execute("UPDATE access_codes SET is_active=? WHERE id=?", (int(active), code_id))

def increment_download(code_id: int):
    with connect() as conn:
        conn.execute("""
            UPDATE access_codes
            SET download_count=download_count+1,last_used_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (code_id,))

def add_audit(code_id, event_type, item_id=None, file_name=None, ip=None, user_agent=None):
    with connect() as conn:
        conn.execute("""
            INSERT INTO audit_logs(access_code_id,event_type,item_id,file_name,ip_address,user_agent)
            VALUES(?,?,?,?,?,?)
        """, (code_id, event_type, item_id, file_name, ip, user_agent))

def list_audit_logs(limit=200):
    with connect() as conn:
        rows = conn.execute("""
            SELECT a.*, c.label
            FROM audit_logs a
            LEFT JOIN access_codes c ON c.id=a.access_code_id
            ORDER BY a.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
