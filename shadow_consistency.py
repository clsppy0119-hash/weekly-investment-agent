"""Default-off, offline consistency report for lineage shadow evidence."""
from __future__ import annotations
import hashlib, json, os
from typing import Any
import lineage_replay

FLAG="LINEAGE_CONSISTENCY_ENABLED"
REQUIRED={"quote","fundamentals","corporate_actions","point_in_time"}
def enabled(): return os.environ.get(FLAG,"").lower() in {"1","true","yes"}
def h(v:Any): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def report(frozen:dict[str,Any])->dict[str,Any]:
 if not enabled(): return {"mode":"disabled","blockers":["consistency_disabled"]}
 blockers=[]
 if frozen.get("schemaVersion")!=1 or frozen.get("policy")!="shadow-lineage-v1": blockers.append("frozen_contract_invalid")
 identities=set(frozen.get("requiredInputs",[]))
 if identities!=REQUIRED: blockers.append("required_identity_set_invalid")
 if frozen.get("coverage")!=1.0: blockers.append("coverage_incomplete")
 replay=lineage_replay.replay(frozen.get("replay",{}))
 if replay.get("mode")!="shadow_only": blockers.append("replay_not_certified")
 if frozen.get("expectedInputHash")!=h({"requiredInputs":frozen.get("requiredInputs"),"coverage":frozen.get("coverage"),"replay":frozen.get("replay")}): blockers.append("frozen_input_hash_mismatch")
 return {"schemaVersion":1,"mode":"shadow_only" if not blockers else "research_only","inputHash":frozen.get("expectedInputHash"),"coverage":frozen.get("coverage"),"replayMode":replay.get("mode"),"selectedVersionSetHash":replay.get("selectedVersionSetHash"),"blockers":sorted(set(blockers)),"limitation":"Shadow lineage consistency does not validate raw values, strategies, or formal advice."}
