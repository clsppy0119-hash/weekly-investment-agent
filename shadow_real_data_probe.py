"""Read-only availability probe; never reads raw cache rows or secrets."""
from pathlib import Path
import json
def run(data_dir=Path('data')):
 names=['fundamentals-coverage.json','investment-advice-gate.json','full-market-backtest-cache-status.json']
 found=[]
 for n in names:
  p=data_dir/n
  if p.exists():
   try: found.append({'name':n,'schema':json.loads(p.read_text(encoding='utf-8-sig')).get('schemaVersion')})
   except Exception: pass
 return {'mode':'research_only','readOnly':True,'foundAllowlistedSummaries':found,'blockers':['frozen_lineage_summary_not_available'],'limitation':'No raw cache, secrets, network, advice, notification, or trade access.'}
