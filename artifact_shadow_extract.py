"""Default-off safe extraction of two allowlisted GitHub artifact JSON files."""
from __future__ import annotations
import io,json,os,stat,unicodedata,zipfile
FLAG='ARTIFACT_SHADOW_EXTRACT_ENABLED'; ALLOWED={'data/evidence-contract.json','data/candidate-manifest.json'}
MAX_ARCHIVE=2_000_000; MAX_MEMBER=500_000; MAX_TOTAL=1_000_000; MAX_RATIO=20
MAX_ALL_MEMBER=1_000_000; MAX_ALL_TOTAL=2_000_000
NESTED_ARCHIVE_SUFFIXES=('.zip','.tar','.tar.gz','.tgz','.gz','.bz2','.xz','.7z','.rar')

def _preflight(infos):
 seen=set(); total=0
 for i in infos:
  n=i.filename
  if not isinstance(n,str) or not n or len(n)>240:raise ValueError('archive_member_name_invalid')
  if unicodedata.normalize('NFC',n)!=n or any(ord(c)<32 or ord(c)>126 for c in n):raise ValueError('archive_member_name_invalid')
  if '\\' in n or n.startswith('/') or any(p in ('','.','..') or p.endswith(('.',' ')) for p in n.split('/')):raise ValueError('archive_member_name_invalid')
  key=n.casefold()
  if key in seen:raise ValueError('archive_member_duplicate')
  seen.add(key)
  if i.is_dir() or i.flag_bits&1:raise ValueError('archive_member_special')
  mode=i.external_attr>>16; kind=stat.S_IFMT(mode)
  if kind not in (0,stat.S_IFREG):raise ValueError('archive_member_special')
  if n.casefold().endswith(NESTED_ARCHIVE_SUFFIXES):raise ValueError('nested_archive_not_allowed')
  total+=i.file_size
  if i.file_size<0 or i.file_size>MAX_ALL_MEMBER or total>MAX_ALL_TOTAL:raise ValueError('archive_member_size_invalid')
  if (i.file_size and not i.compress_size) or (i.compress_size and i.file_size/i.compress_size>MAX_RATIO):raise ValueError('archive_member_ratio_invalid')

def extract(blob:bytes):
 if os.environ.get(FLAG,'').lower() not in {'1','true','yes'}:return {'mode':'disabled'}
 try:
  if len(blob)>MAX_ARCHIVE:raise ValueError('archive_too_large')
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
   _preflight(z.infolist())
   seen=set();total=0;out={}
   for i in z.infolist():
    n=i.filename
    if n not in ALLOWED:continue
    if n in seen or '\\' in n or i.is_dir() or (i.external_attr>>16)&0o170000 not in (0,0o100000):raise ValueError('member_invalid')
    if i.file_size>MAX_MEMBER or i.file_size+total>MAX_TOTAL or (i.compress_size and i.file_size/i.compress_size>MAX_RATIO):raise ValueError('member_size_invalid')
    seen.add(n);total+=i.file_size;out[n]=json.loads(z.read(i).decode('utf-8'))
   if set(out)!=ALLOWED:raise ValueError('allowlist_incomplete')
   return {'mode':'shadow_only','contract':out['data/evidence-contract.json'],'manifest':out['data/candidate-manifest.json']}
 except Exception as e:return {'mode':'research_only','blockers':[type(e).__name__]}
