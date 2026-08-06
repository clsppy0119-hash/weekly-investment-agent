import os,unittest
import lineage_drift_shadow as d
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['LINEAGE_DRIFT_SHADOW_ENABLED']='true';self.x={'schemaVersion':1,'policy':'p','decisionAsOf':'2026-01-01T00:00:00+08:00','scope':'s','version':1,'coverage':1.0,'selected':{'2330':'h1'}}
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_identical_and_drift(self):self.assertEqual(d.compare(self.x,dict(self.x))['mode'],'shadow_only');y=dict(self.x);y['selected']={'2330':'h2'};self.assertEqual(d.compare(self.x,y)['changes']['2330']['class'],'post_as_of_change')
 def test_fail_closed(self):y=dict(self.x);y['coverage']=.9;self.assertEqual(d.compare(self.x,y)['mode'],'research_only')
