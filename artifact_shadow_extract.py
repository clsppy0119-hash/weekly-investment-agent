"""Default-off safe extraction of two allowlisted GitHub artifact JSON files."""
from __future__ import annotations
import io,json,os,zipfile
FLAG='ARTIFACT_SHADOW_EXTRACT_ENABLED'; ALLOWED={'data/evidence-contract.json','data/candidate-manifest.json'}
MAX_ARCHIVE=2_000_000; MAX_MEMBER=500_000; MAX_TOTAL=1_000_000; MAX_RATIO=20
def extract(blob:bytes):
 if os.environ.get(FLAG,'').lower() not in {'1','true','yes'}:return {'mode':'disabled'}
 try:
  if len(blob)>MAX_ARCHIVE:raise ValueError('archive_too_large')
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
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
