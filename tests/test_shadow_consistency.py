import os,unittest
import lineage_replay,shadow_consistency
class T(unittest.TestCase):
 def setUp(self):
  self.old=dict(os.environ);os.environ['LINEAGE_CONSISTENCY_ENABLED']='true';os.environ['LINEAGE_REPLAY_ENABLED']='true'
  r={'compositeKey':'k','provider':'M','dataset':'d','entityId':'2330','observationPeriod':'q','sourceRevision':'v','availableAt':'2026-01-01T00:00:00+08:00','contentHash':'c','status':'success','conflictStatus':'no_conflict','schemaVersion':1}
  vs=[{k:r[k] for k in ('compositeKey','contentHash','availableAt','sourceRevision')}]; rp={'schemaVersion':1,'decisionAsOf':'2026-02-01T00:00:00+08:00','coverage':1.0,'records':[r],'expectedVersionSetHash':lineage_replay.digest(vs),'expectedSnapshotHash':lineage_replay.digest(vs)}
  self.f={'schemaVersion':1,'policy':'shadow-lineage-v1','requiredInputs':['quote','fundamentals','corporate_actions','point_in_time'],'coverage':1.0,'replay':rp};self.f['expectedInputHash']=shadow_consistency.h({'requiredInputs':self.f['requiredInputs'],'coverage':1.0,'replay':rp})
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_ok_and_off(self):self.assertEqual(shadow_consistency.report(self.f)['mode'],'shadow_only');os.environ.pop('LINEAGE_CONSISTENCY_ENABLED');self.assertEqual(shadow_consistency.report(self.f)['mode'],'disabled')
 def test_all_uncertainty_fails(self):
  for key,val in [('coverage',.9),('policy','bad'),('requiredInputs',[]),('expectedInputHash','bad')]:
   x=dict(self.f);x[key]=val;self.assertEqual(shadow_consistency.report(x)['mode'],'research_only')
