import json, os, sqlite3
from pathlib import Path
DATA_DIR=Path(os.getenv("DATA_DIR","/data"))
try: DATA_DIR.mkdir(parents=True,exist_ok=True)
except Exception: DATA_DIR=Path('./data'); DATA_DIR.mkdir(parents=True,exist_ok=True)
DB_PATH=DATA_DIR/'search.db'
def connect():
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row; return c
def init_db():
    with connect() as c:
        c.executescript("""CREATE TABLE IF NOT EXISTS files(item_id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT,web_url TEXT,mime_type TEXT,extension TEXT,modified_at TEXT,size INTEGER DEFAULT 0,content TEXT DEFAULT '',indexed_at TEXT DEFAULT CURRENT_TIMESTAMP,status TEXT DEFAULT 'metadata_only',error TEXT); CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(item_id UNINDEXED,name,path,content,tokenize='unicode61'); CREATE TABLE IF NOT EXISTS app_state(key TEXT PRIMARY KEY,value TEXT);""")
def get_state(k):
    with connect() as c:
        r=c.execute('SELECT value FROM app_state WHERE key=?',(k,)).fetchone(); return r['value'] if r else None
def set_state(k,v):
    with connect() as c: c.execute("INSERT INTO app_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))
def upsert_file(m,content='',status='metadata_only',error=None):
    with connect() as c:
        c.execute("""INSERT INTO files(item_id,name,path,web_url,mime_type,extension,modified_at,size,content,indexed_at,status,error) VALUES(:item_id,:name,:path,:web_url,:mime_type,:extension,:modified_at,:size,:content,CURRENT_TIMESTAMP,:status,:error) ON CONFLICT(item_id) DO UPDATE SET name=excluded.name,path=excluded.path,web_url=excluded.web_url,mime_type=excluded.mime_type,extension=excluded.extension,modified_at=excluded.modified_at,size=excluded.size,content=excluded.content,indexed_at=CURRENT_TIMESTAMP,status=excluded.status,error=excluded.error""",{**m,'content':content or '','status':status,'error':error})
        c.execute('DELETE FROM files_fts WHERE item_id=?',(m['item_id'],)); c.execute('INSERT INTO files_fts(item_id,name,path,content) VALUES(?,?,?,?)',(m['item_id'],m['name'],m.get('path') or '',content or ''))
def delete_file(i):
    with connect() as c: c.execute('DELETE FROM files WHERE item_id=?',(i,)); c.execute('DELETE FROM files_fts WHERE item_id=?',(i,))
def _esc(v): return v.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
def _hi(t,q):
    if not t:return ''
    p=t.lower().find(q.lower())
    return t if p<0 else t[:p]+'<mark>'+t[p:p+len(q)]+'</mark>'+t[p+len(q):]
def search_files(query,limit=100):
    q=(query or '').strip()
    if not q:return []
    lv='%'+_esc(q)+'%'; out=[]; seen=set()
    with connect() as c:
        rows=c.execute("""SELECT f.*, CASE WHEN instr(lower(f.content),lower(?))>0 THEN substr(f.content,max(1,instr(lower(f.content),lower(?))-90),260) ELSE '' END raw_snippet, CASE WHEN lower(f.name) LIKE lower(?) ESCAPE '\\' THEN 0 WHEN lower(f.path) LIKE lower(?) ESCAPE '\\' THEN 1 ELSE 2 END score FROM files f WHERE lower(f.name) LIKE lower(?) ESCAPE '\\' OR lower(f.path) LIKE lower(?) ESCAPE '\\' OR lower(f.content) LIKE lower(?) ESCAPE '\\' ORDER BY score,f.modified_at DESC LIMIT ?""",(q,q,lv,lv,lv,lv,lv,limit)).fetchall()
        for r in rows:
            d=dict(r); d['snippet']=_hi(d.pop('raw_snippet','') or '',q); out.append(d); seen.add(d['item_id'])
        if len(out)<limit:
            terms=[x.replace('"','""') for x in q.split() if x]
            if terms:
                try:
                    fr=c.execute("""SELECT f.*,snippet(files_fts,3,'<mark>','</mark>',' … ',32) snippet,bm25(files_fts,3.0,2.0,1.0) score FROM files_fts JOIN files f ON f.item_id=files_fts.item_id WHERE files_fts MATCH ? ORDER BY score,f.modified_at DESC LIMIT ?""",(' AND '.join(f'"{x}"' for x in terms),limit)).fetchall()
                    for r in fr:
                        d=dict(r)
                        if d['item_id'] not in seen: out.append(d); seen.add(d['item_id'])
                        if len(out)>=limit: break
                except sqlite3.OperationalError: pass
    return out[:limit]
def stats():
    with connect() as c:
        return {'total':c.execute('SELECT COUNT(*) c FROM files').fetchone()['c'],'content_indexed':c.execute("SELECT COUNT(*) c FROM files WHERE status='content_indexed'").fetchone()['c'],'metadata_only':c.execute("SELECT COUNT(*) c FROM files WHERE status='metadata_only'").fetchone()['c'],'errors':c.execute("SELECT COUNT(*) c FROM files WHERE status='error'").fetchone()['c'],'last_indexed':c.execute('SELECT MAX(indexed_at) x FROM files').fetchone()['x']}
def list_problem_files(limit=500):
    with connect() as c:return [dict(r) for r in c.execute("SELECT item_id,name,path,web_url,extension,status,error,modified_at FROM files WHERE status IN ('error','metadata_only') ORDER BY CASE WHEN status='error' THEN 0 ELSE 1 END,modified_at DESC LIMIT ?",(limit,)).fetchall()]
def sync_state():
    raw=get_state('last_sync_result')
    try:p=json.loads(raw) if raw else None
    except Exception:p=None
    return {'last_started':get_state('last_sync_started'),'last_completed':get_state('last_sync_completed'),'last_result':p,'sync_running':get_state('sync_running')=='1'}
