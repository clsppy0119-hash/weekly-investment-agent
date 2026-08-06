"""Default-off offline health trend for allowlisted pinned shadow artifacts."""
from __future__ import annotations
import os
FLAG='PROVENANCE_HEALTH_ENABLED'
def report(samples):
 if os.environ.get(FLAG,'').lower() not in {'1','true','yes'}:return {'mode':'disabled'}
 if len(samples)<2 or any(x.get('schemaVersion')!=1 or x.get('coverage')!=1.0 for x in samples):return {'mode':'research_only','blockers':['health_samples_insufficient']}
 return {'mode':'shadow_only','window':len(samples),'coverageTrend':[x['coverage'] for x in samples],'blockers':[],'limitation':'Shadow-only; no formal alert or advice effect.'}
