"""Default-off PIT shadow sidecar; never mutates candidates or backtest inputs."""
from __future__ import annotations
import hashlib,json,os
from typing import Any
FLAG='PIT_SHADOW_SIDECAR_ENABLED'
ALLOW=('mode','decisionAsOf','coverage','selectedVersionSetHash','blockers','inputHash')
def enabled(): return os.environ.get(FLAG,'').lower() in {'1','true','yes'}
def h(v:Any): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def attach(original:dict[str,Any], consistency:dict[str,Any])->dict[str,Any]:
 """Return separate metadata. The original object is never changed."""
 before=h(original)
 if not enabled(): return {'shadowOnly':True,'mode':'disabled','inputHash':before,'blockers':['pit_shadow_disabled']}
 mode=consistency.get('mode'); coverage=consistency.get('coverage')
 blockers=list(consistency.get('blockers',[]))
 if mode!='shadow_only' or coverage!=1.0 or not consistency.get('selectedVersionSetHash'):
  return {'shadowOnly':True,'mode':'research_only','decisionAsOf':consistency.get('decisionAsOf'),'coverage':coverage,'selectedVersionSetHash':None,'blockers':sorted(set(blockers+['pit_shadow_not_certified'])),'inputHash':before}
 return {'shadowOnly':True,'mode':'shadow_only','decisionAsOf':consistency.get('decisionAsOf'),'coverage':coverage,'selectedVersionSetHash':consistency['selectedVersionSetHash'],'blockers':[],'inputHash':before}
