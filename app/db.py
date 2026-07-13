
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("./data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "search.db"

ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
QUERY_TOKEN_RE = re.compile(r'"([^"]+)"|(\|)|(\S+)')

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def normalize_text(value):
    value = "" if value is None else str(value)
    value = ZERO_WIDTH.sub("", value)
    value = unicodedata.normalize("NFC", value)
    # Normalize decomposed Thai sara-am: nikhahit + sara aa -> sara am.
    value = value.replace("\u0e4d\u0e32", "\u0e33")
    return value.casefold()

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
            search_text_norm TEXT DEFAULT '',
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

        CREATE TABLE IF NOT EXISTS recent_searches (
            query TEXT PRIMARY KEY,
            used_count INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Upgrade existing v1.x databases in place.
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(files)").fetchall()
        }
        if "search_text_norm" not in columns:
            conn.execute(
                "ALTER TABLE files ADD COLUMN search_text_norm TEXT DEFAULT ''"
            )

        rows = conn.execute("""
            SELECT item_id,name,path,content
            FROM files
            WHERE search_text_norm IS NULL OR search_text_norm=''
        """).fetchall()

        for row in rows:
            blob = normalize_text(
                f"{row['name'] or ''}\n{row['path'] or ''}\n{row['content'] or ''}"
            )
            conn.execute(
                "UPDATE files SET search_text_norm=? WHERE item_id=?",
                (blob, row["item_id"])
            )

def get_state(key):
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key=?",
            (key,)
        ).fetchone()
        return row["value"] if row else None

def set_state(key, value):
    with connect() as conn:
        conn.execute("""
        INSERT INTO app_state(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))

def upsert_file(meta, content="", status="metadata_only", error=None):
    content = content or ""
    normalized_blob = normalize_text(
        f"{meta.get('name','')}\n{meta.get('path','')}\n{content}"
    )

    with connect() as conn:
        conn.execute("""
        INSERT INTO files(
            item_id,name,path,web_url,mime_type,extension,
            modified_at,size,content,search_text_norm,
            indexed_at,status,error
        )
        VALUES(
            :item_id,:name,:path,:web_url,:mime_type,:extension,
            :modified_at,:size,:content,:search_text_norm,
            CURRENT_TIMESTAMP,:status,:error
        )
        ON CONFLICT(item_id) DO UPDATE SET
            name=excluded.name,
            path=excluded.path,
            web_url=excluded.web_url,
            mime_type=excluded.mime_type,
            extension=excluded.extension,
            modified_at=excluded.modified_at,
            size=excluded.size,
            content=excluded.content,
            search_text_norm=excluded.search_text_norm,
            indexed_at=CURRENT_TIMESTAMP,
            status=excluded.status,
            error=excluded.error
        """, {
            **meta,
            "content": content,
            "search_text_norm": normalized_blob,
            "status": status,
            "error": error
        })

        conn.execute("DELETE FROM files_fts WHERE item_id=?", (meta["item_id"],))
        conn.execute("""
        INSERT INTO files_fts(item_id,name,path,content)
        VALUES(?,?,?,?)
        """, (
            meta["item_id"],
            meta["name"],
            meta.get("path") or "",
            content
        ))


def upsert_metadata(meta, status="pending", error=None):
    """Insert/update metadata without erasing already indexed content when unchanged."""
    with connect() as conn:
        row = conn.execute(
            "SELECT modified_at,size,content,status FROM files WHERE item_id=?",
            (meta["item_id"],)
        ).fetchone()
        unchanged = bool(
            row and row["modified_at"] == meta.get("modified_at")
            and int(row["size"] or 0) == int(meta.get("size") or 0)
        )
        if unchanged and row["status"] == "content_indexed":
            effective_status = "content_indexed"
            content = row["content"] or ""
            effective_error = None
        else:
            effective_status = status
            content = "" if not row else (row["content"] or "" if unchanged else "")
            effective_error = error

        normalized_blob = normalize_text(
            f"{meta.get('name','')}\n{meta.get('path','')}\n{content}"
        )
        conn.execute("""
        INSERT INTO files(
            item_id,name,path,web_url,mime_type,extension,modified_at,size,
            content,search_text_norm,indexed_at,status,error
        ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,?)
        ON CONFLICT(item_id) DO UPDATE SET
            name=excluded.name,path=excluded.path,web_url=excluded.web_url,
            mime_type=excluded.mime_type,extension=excluded.extension,
            modified_at=excluded.modified_at,size=excluded.size,
            content=excluded.content,search_text_norm=excluded.search_text_norm,
            indexed_at=CURRENT_TIMESTAMP,status=excluded.status,error=excluded.error
        """, (
            meta["item_id"], meta.get("name") or "", meta.get("path") or "",
            meta.get("web_url"), meta.get("mime_type"), meta.get("extension"),
            meta.get("modified_at"), int(meta.get("size") or 0), content,
            normalized_blob, effective_status, effective_error
        ))
        conn.execute("DELETE FROM files_fts WHERE item_id=?", (meta["item_id"],))
        conn.execute("INSERT INTO files_fts(item_id,name,path,content) VALUES(?,?,?,?)",
                     (meta["item_id"], meta.get("name") or "", meta.get("path") or "", content))
        return effective_status


def list_pending_files(limit=100):
    with connect() as conn:
        rows = conn.execute("""
            SELECT item_id,name,path,web_url,mime_type,extension,modified_at,size
            FROM files WHERE status='pending'
            ORDER BY indexed_at ASC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

def delete_file(item_id):
    with connect() as conn:
        conn.execute("DELETE FROM files WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM files_fts WHERE item_id=?", (item_id,))

def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _term_pattern(term):
    normalized = normalize_text(term)
    # User wildcard * maps to SQL wildcard %. Other LIKE characters stay escaped.
    parts = normalized.split("*")
    escaped = [_escape_like(part) for part in parts]
    return "%" + "%".join(escaped) + "%"

def parse_query(query):
    """
    Syntax:
      foo bar       = foo AND bar
      foo | bar     = foo OR bar
      "foo bar"     = exact phrase
      foo -bar      = include foo, exclude bar
      foo*          = wildcard substring
    """
    query = (query or "").strip()
    groups = [[]]
    excludes = []

    for match in QUERY_TOKEN_RE.finditer(query):
        phrase, pipe, plain = match.groups()

        if pipe:
            if groups[-1]:
                groups.append([])
            continue

        token = phrase if phrase is not None else plain
        if not token:
            continue

        is_exclude = token.startswith("-") and len(token) > 1
        if is_exclude:
            token = token[1:]

        token = token.strip()
        if not token:
            continue

        if is_exclude:
            excludes.append(token)
        else:
            groups[-1].append(token)

    groups = [group for group in groups if group]
    return {"groups": groups, "excludes": excludes}

def _record_search(query):
    if not query.strip():
        return
    with connect() as conn:
        conn.execute("""
        INSERT INTO recent_searches(query,used_count,last_used_at)
        VALUES(?,1,CURRENT_TIMESTAMP)
        ON CONFLICT(query) DO UPDATE SET
            used_count=used_count+1,
            last_used_at=CURRENT_TIMESTAMP
        """, (query.strip(),))

def _match_positions(text_norm, positive_terms):
    positions = []
    for term in positive_terms:
        clean = normalize_text(term.replace("*", ""))
        if clean:
            pos = text_norm.find(clean)
            if pos >= 0:
                positions.append(pos)
    return positions

def _make_snippet(content, terms, radius=110):
    if not content:
        return ""

    content_norm = normalize_text(content)
    cleaned = [
        normalize_text(term.replace("*", ""))
        for term in terms
        if term.replace("*", "").strip()
    ]
    positions = [
        content_norm.find(term)
        for term in cleaned
        if term and content_norm.find(term) >= 0
    ]

    start = max(0, (min(positions) if positions else 0) - radius)
    end = min(len(content), start + 320)
    snippet = content[start:end].replace("\n", " ")

    # Highlight all included terms, longest first.
    for term in sorted(
        [t.replace("*", "") for t in terms if t.replace("*", "").strip()],
        key=len,
        reverse=True
    ):
        snippet = re.sub(
            re.escape(term),
            lambda m: f"<mark>{m.group(0)}</mark>",
            snippet,
            flags=re.IGNORECASE
        )
    return snippet

def search_files(query, limit=100):
    query = (query or "").strip()
    if not query:
        return []

    parsed = parse_query(query)
    groups = parsed["groups"]
    excludes = parsed["excludes"]

    if not groups and excludes:
        return []

    sql_parts = []
    params = []

    # Each OR group contains AND terms.
    for group in groups:
        group_parts = []
        for term in group:
            group_parts.append(
                "search_text_norm LIKE ? ESCAPE '\\'"
            )
            params.append(_term_pattern(term))
        sql_parts.append("(" + " AND ".join(group_parts) + ")")

    where_sql = "(" + " OR ".join(sql_parts) + ")" if sql_parts else "1=1"

    for term in excludes:
        where_sql += " AND search_text_norm NOT LIKE ? ESCAPE '\\'"
        params.append(_term_pattern(term))

    # Pull extra candidates, then rank precisely in Python.
    candidate_limit = min(max(limit * 4, 200), 1000)
    params.append(candidate_limit)

    with connect() as conn:
        rows = conn.execute(f"""
        SELECT *
        FROM files
        WHERE {where_sql}
        ORDER BY modified_at DESC
        LIMIT ?
        """, params).fetchall()

    all_positive = [term for group in groups for term in group]
    normalized_query = normalize_text(query)
    results = []

    for row in rows:
        item = dict(row)
        name_norm = normalize_text(item.get("name"))
        path_norm = normalize_text(item.get("path"))
        content_norm = normalize_text(item.get("content"))

        score = 100

        # Strong preference for filename matches.
        if normalized_query and normalized_query in name_norm:
            score -= 70
        elif any(
            normalize_text(t.replace("*", "")) in name_norm
            for t in all_positive
            if t.replace("*", "").strip()
        ):
            score -= 50

        if any(
            normalize_text(t.replace("*", "")) in path_norm
            for t in all_positive
            if t.replace("*", "").strip()
        ):
            score -= 20

        positions = _match_positions(content_norm, all_positive)
        if positions:
            score -= 10
            # Terms appearing close together rank higher.
            if len(positions) >= 2:
                spread = max(positions) - min(positions)
                score += min(spread // 100, 20)

        item["score"] = score
        item["snippet"] = _make_snippet(
            item.get("content") or "",
            all_positive
        )
        results.append(item)

    results.sort(
        key=lambda x: (
            x["score"],
            x.get("name", "").casefold(),
            x.get("modified_at") or ""
        )
    )

    _record_search(query)
    return results[:limit]

def suggestions(prefix, limit=10):
    prefix = (prefix or "").strip()
    if len(prefix) < 1:
        return []

    normalized = normalize_text(prefix)
    like_value = f"%{_escape_like(normalized)}%"

    suggestions_out = []
    seen = set()

    with connect() as conn:
        # Prior searches first.
        recent = conn.execute("""
        SELECT query
        FROM recent_searches
        WHERE lower(query) LIKE lower(?) ESCAPE '\\'
        ORDER BY used_count DESC,last_used_at DESC
        LIMIT ?
        """, (f"{_escape_like(prefix)}%", limit)).fetchall()

        for row in recent:
            value = row["query"]
            key = normalize_text(value)
            if key not in seen:
                suggestions_out.append(value)
                seen.add(key)

        # Then filenames and paths.
        rows = conn.execute("""
        SELECT name,path
        FROM files
        WHERE search_text_norm LIKE ? ESCAPE '\\'
        ORDER BY modified_at DESC
        LIMIT 80
        """, (like_value,)).fetchall()

    word_re = re.compile(r"[\w\u0e00-\u0e7f./-]+", re.UNICODE)
    for row in rows:
        candidates = [row["name"] or ""]
        candidates.extend(word_re.findall(row["name"] or ""))
        candidates.extend(word_re.findall(row["path"] or ""))

        for value in candidates:
            value = value.strip()
            if not value:
                continue
            key = normalize_text(value)
            if normalized not in key or key in seen:
                continue
            suggestions_out.append(value)
            seen.add(key)
            if len(suggestions_out) >= limit:
                return suggestions_out

    return suggestions_out[:limit]

def stats():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        content = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE status='content_indexed'"
        ).fetchone()["c"]
        metadata = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE status='metadata_only'"
        ).fetchone()["c"]
        errors = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE status='error'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) c FROM files WHERE status='pending'"
        ).fetchone()["c"]
        last_indexed = conn.execute(
            "SELECT MAX(indexed_at) x FROM files"
        ).fetchone()["x"]

    return {
        "total": total,
        "content_indexed": content,
        "metadata_only": metadata,
        "errors": errors,
        "pending": pending,
        "last_indexed": last_indexed
    }

def list_problem_files(limit=500):
    with connect() as conn:
        rows = conn.execute("""
        SELECT item_id,name,path,web_url,extension,status,error,modified_at
        FROM files
        WHERE status IN ('error','metadata_only')
        ORDER BY
            CASE WHEN status='error' THEN 0 ELSE 1 END,
            modified_at DESC
        LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def sync_state():
    raw = get_state("last_sync_result")
    try:
        parsed = json.loads(raw) if raw else None
    except Exception:
        parsed = None

    return {
        "last_started": get_state("last_sync_started"),
        "last_completed": get_state("last_sync_completed"),
        "last_result": parsed,
        "sync_running": get_state("sync_running") == "1"
    }
