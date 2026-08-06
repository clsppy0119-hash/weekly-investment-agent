import os,unittest
import provenance_health_shadow as h
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['PROVENANCE_HEALTH_ENABLED']='true'
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_valid_and_fail_closed(self):self.assertEqual(h.report([{'schemaVersion':1,'coverage':1.0},{'schemaVersion':1,'coverage':1.0}])['mode'],'shadow_only');self.assertEqual(h.report([{'schemaVersion':1,'coverage':1.0}])['mode'],'research_only')
