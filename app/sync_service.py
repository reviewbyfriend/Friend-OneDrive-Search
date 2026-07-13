import os,threading
from pathlib import Path
from .db import delete_file,get_state,set_state,upsert_file
from .extractors import CONTENT_EXTENSIONS,extract_text
from .graph import download_item,iter_delta
MAX_FILE_MB=int(os.getenv('MAX_FILE_MB','30')); LOCK=threading.Lock()
def item_path(i):
 p=(i.get('parentReference') or {}).get('path','').replace('/drive/root:',''); return f"{p}/{i.get('name','')}".replace('//','/')
def sync_drive(token,full_scan=False):
 if not LOCK.acquire(blocking=False):return {'status':'already_running'}
 try:
  if full_scan:set_state('delta_url','')
  delta=get_state('delta_url') or None; out={'status':'completed','mode':'full' if full_scan else 'delta','processed':0,'content_indexed':0,'metadata_only':0,'deleted':0,'errors':0}; new=None
  for i in iter_delta(token,delta):
   if '__delta_link__' in i:new=i['__delta_link__'];continue
   out['processed']+=1; iid=i.get('id')
   if not iid:continue
   if i.get('deleted') is not None:delete_file(iid);out['deleted']+=1;continue
   if 'folder' in i or 'file' not in i:continue
   n=i.get('name',''); ext=Path(n).suffix.lower(); size=int(i.get('size') or 0)
   m={'item_id':iid,'name':n,'path':item_path(i),'web_url':i.get('webUrl'),'mime_type':(i.get('file') or {}).get('mimeType'),'extension':ext,'modified_at':i.get('lastModifiedDateTime'),'size':size}
   if ext not in CONTENT_EXTENSIONS:upsert_file(m,'','metadata_only','ยังไม่มีตัวอ่านข้อความชนิดนี้');out['metadata_only']+=1;continue
   if size>MAX_FILE_MB*1024*1024:upsert_file(m,'','metadata_only',f'ไฟล์ใหญ่เกิน {MAX_FILE_MB} MB');out['metadata_only']+=1;continue
   try:
    txt=extract_text(download_item(iid,token),ext).strip()
    if txt:upsert_file(m,txt,'content_indexed',None);out['content_indexed']+=1
    else:upsert_file(m,'','metadata_only','ไม่พบข้อความในไฟล์');out['metadata_only']+=1
   except Exception as e:upsert_file(m,'','error',str(e)[:500]);out['errors']+=1
  if new:set_state('delta_url',new)
  return out
 finally:LOCK.release()
