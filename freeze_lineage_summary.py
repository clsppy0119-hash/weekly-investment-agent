"""Offline, default-off freezing of allowlisted lineage contract evidence."""
from __future__ import annotations
import hashlib,json,os
from pathlib import Path
FLAG='LINEAGE_FREEZE_ENABLED'; REQUIRED={'quote','fundamentals','corporate_actions','point_in_time'}
def _h(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def freeze(contract:dict, manifest:dict)->dict:
 if os.environ.get(FLAG,'').lower() not in {'1','true','yes'}:return {'mode':'disabled'}
 records=contract.get('records',[]); by={x.get('name'):x for x in records if isinstance(x,dict)}; blockers=[]
 if contract.get('schemaVersion')!=1 or manifest.get('schemaVersion')!=1: blockers.append('schema_invalid')
 if not contract.get('certified') or set(by)&REQUIRED!=REQUIRED: blockers.append('contract_not_certified')
 selected=[]
 for n in sorted(REQUIRED):
  r=by.get(n,{})
  if r.get('quality')!='verified' or r.get('conflictStatus')!='no_conflict' or not r.get('availableAt'): blockers.append(f'{n}_not_pit_ready')
  else:selected.append({k:r.get(k) for k in ('name','source','sourceDataset','effectiveDate','availableAt','evidenceHash','quality','conflictStatus')})
 core={'schemaVersion':1,'policy':'frozen-lineage-v1','candidateOrder':manifest.get('candidateOrder',[]),'records':selected}
 return {'schemaVersion':1,'mode':'shadow_only' if not blockers else 'research_only','frozenDigest':_h(core),'coverage':1.0 if not blockers else 0.0,'records':selected,'blockers':sorted(set(blockers)),'limitation':'Metadata-only frozen lineage; no raw data, URLs, secrets, or formal advice effect.'}
def load_allowed(root:Path):
 root=root.resolve(); c=root/'data'/'evidence-contract.json'; m=root/'data'/'candidate-manifest.json'
 if not c.exists() or not m.exists():return None,None
 return json.loads(c.read_text(encoding='utf-8')),json.loads(m.read_text(encoding='utf-8'))
