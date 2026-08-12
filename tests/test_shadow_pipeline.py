import os,unittest
import shadow_pipeline as p
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['SHADOW_PIPELINE_ENABLED']='true';os.environ['LINEAGE_FREEZE_ENABLED']='true';rs=[{'name':n,'source':'s','sourceDataset':'d','effectiveDate':'x','availableAt':'2026-01-01T00:00:00+08:00','evidenceHash':n,'quality':'verified','conflictStatus':'no_conflict'} for n in ('quote','fundamentals','corporate_actions','point_in_time')];self.c={'schemaVersion':1,'certified':True,'records':rs};self.m={'schemaVersion':1,'candidateOrder':[]}
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_manual_pipeline_fail_closed(self):o=p.run(self.c,self.m,decision_as_of='2026-02-01T00:00:00+08:00');self.assertEqual(o['mode'],'research_only');self.assertEqual(o['stages']['freeze']['mode'],'shadow_only')
 def test_disabled(self):os.environ.pop('SHADOW_PIPELINE_ENABLED');self.assertEqual(p.run({}, {},decision_as_of='x')['mode'],'disabled')
