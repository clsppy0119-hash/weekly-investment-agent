"""Manual default-off, zero-network shadow validation pipeline."""
from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path
import contract_gap_report,freeze_lineage_summary
FLAG='SHADOW_PIPELINE_ENABLED'
def _h(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def run(contract,manifest,*,decision_as_of):
 if os.environ.get(FLAG,'').lower() not in {'1','true','yes'}:return {'mode':'disabled','blockers':['pipeline_disabled']}
 with tempfile.TemporaryDirectory(prefix='shadow-pipeline-') as tmp:
  frozen=freeze_lineage_summary.freeze(contract,manifest)
  stages={'freeze':{'mode':frozen.get('mode'),'digest':frozen.get('frozenDigest'),'blockers':frozen.get('blockers',[])}}
  gaps=contract_gap_report.report(contract,frozen,manifest,decision_as_of=decision_as_of) if contract_gap_report.enabled() else None
  if gaps is not None:stages['contractGaps']={'mode':gaps.get('mode'),'coverageState':gaps.get('coverageState'),'inputDigest':gaps.get('inputDigest')}
  if frozen.get('mode')!='shadow_only':
   out={'mode':'research_only','decisionAsOf':decision_as_of,'stages':stages,'blockers':['freeze_not_certified']}
   if gaps is not None:out['contractGapReport']=gaps
   return out
  # A frozen metadata contract is the only accepted pipeline input. Until a
  # qualified real replay summary exists this intentionally stays fail-closed.
  stages['replay']={'mode':'research_only','blockers':['replay_input_not_available']}
  out={'mode':'research_only','decisionAsOf':decision_as_of,'stages':stages,'digest':_h(stages),'blockers':['replay_not_certified'],'sandbox':Path(tmp).name}
  if gaps is not None:out['contractGapReport']=gaps
  return out
