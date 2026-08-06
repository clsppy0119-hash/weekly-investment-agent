"""Offline fail-closed comparison of pinned lineage summaries."""
from __future__ import annotations
import os
FLAG='LINEAGE_DRIFT_SHADOW_ENABLED'
def enabled(): return os.environ.get(FLAG,'').lower() in {'1','true','yes'}
def compare(base,current):
 if not enabled(): return {'mode':'disabled','blockers':['drift_disabled']}
 keys=('schemaVersion','policy','decisionAsOf','scope','version')
 if any(base.get(k)!=current.get(k) for k in keys) or base.get('coverage')!=1.0 or current.get('coverage')!=1.0:return {'mode':'research_only','blockers':['pinned_contract_incompatible']}
 b=base.get('selected',{});c=current.get('selected',{})
 if set(b)!=set(c):return {'mode':'research_only','blockers':['identity_set_mismatch']}
 changes={k:{'class':'unchanged' if b[k]==c[k] else 'post_as_of_change','baseline':b[k],'current':c[k]} for k in b}
 return {'mode':'shadow_only','decisionAsOf':base['decisionAsOf'],'changes':changes,'blockers':[],'limitation':'No strategy or formal advice effect.'}
