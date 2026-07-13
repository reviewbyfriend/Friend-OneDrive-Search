import csv,io,os,subprocess,tempfile
from pathlib import Path
import pytesseract
from PIL import Image
from docx import Document
from openpyxl import load_workbook
from pdf2image import convert_from_bytes
from pypdf import PdfReader
DIRECT={'.docx','.xlsx','.pdf','.txt','.csv'}; LEGACY={'.doc','.xls'}
IMAGES={x.strip().lower() for x in os.getenv('OCR_IMAGE_EXTENSIONS','.jpg,.jpeg,.png,.tif,.tiff,.bmp,.webp').split(',') if x.strip()}
CONTENT_EXTENSIONS=DIRECT|LEGACY|IMAGES
OCR=os.getenv('ENABLE_OCR','true').lower() in {'1','true','yes','on'}; LANG=os.getenv('OCR_LANG','tha+eng'); MAXP=max(1,int(os.getenv('OCR_MAX_PAGES','20')))
def docx(data):
 d=Document(io.BytesIO(data)); p=[x.text for x in d.paragraphs if x.text.strip()]
 for t in d.tables:
  for r in t.rows:p.append(' | '.join(c.text for c in r.cells))
 return '\n'.join(p)
def xlsx(data):
 w=load_workbook(io.BytesIO(data),read_only=True,data_only=True); p=[]
 for s in w.worksheets:
  p.append(f'[ชีต: {s.title}]')
  for r in s.iter_rows(values_only=True):
   v=[str(x) for x in r if x is not None]
   if v:p.append(' | '.join(v))
 return '\n'.join(p)
def legacy(data,ext):
 with tempfile.TemporaryDirectory() as td:
  td=Path(td); src=td/f'source{ext}'; src.write_bytes(data); fmt='docx' if ext=='.doc' else 'xlsx'
  r=subprocess.run(['libreoffice','--headless','--convert-to',fmt,'--outdir',str(td),str(src)],capture_output=True,timeout=120)
  if r.returncode!=0:raise RuntimeError(r.stderr.decode(errors='replace')[:500] or 'LibreOffice conversion failed')
  matches=list(td.glob(f'*.{fmt}'))
  if not matches:raise RuntimeError('Converted file was not created')
  return docx(matches[0].read_bytes()) if fmt=='docx' else xlsx(matches[0].read_bytes())
def extract_text(data,ext):
 ext=ext.lower()
 if ext=='.docx':return docx(data)
 if ext=='.xlsx':return xlsx(data)
 if ext=='.pdf':
  txt='\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(data)).pages).strip()
  if txt:return txt
  if not OCR:return ''
  return '\n'.join(pytesseract.image_to_string(im,lang=LANG) for im in convert_from_bytes(data,dpi=180,first_page=1,last_page=MAXP))
 if ext=='.txt':return data.decode('utf-8',errors='replace')
 if ext=='.csv':return '\n'.join(' | '.join(r) for r in csv.reader(io.StringIO(data.decode('utf-8-sig',errors='replace'))))
 if ext in LEGACY:return legacy(data,ext)
 if ext in IMAGES:return pytesseract.image_to_string(Image.open(io.BytesIO(data)),lang=LANG) if OCR else ''
 raise ValueError(f'Unsupported content extension: {ext}')
