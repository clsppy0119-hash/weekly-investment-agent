import os,unittest,copy
import freeze_lineage_summary as f
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['LINEAGE_FREEZE_ENABLED']='true';self.r=[{'name':n,'source':'s','sourceDataset':'d','effectiveDate':'2026-01-01','availableAt':'2026-01-02T00:00:00+08:00','evidenceHash':n,'quality':'verified','conflictStatus':'no_conflict'} for n in f.REQUIRED];self.c={'schemaVersion':1,'certified':True,'records':self.r,'generatedAt':'x'};self.m={'schemaVersion':1,'candidateOrder':['2330']}
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_digest_stable_and_metadata_only(self):a=f.freeze(self.c,self.m);c=copy.deepcopy(self.c);c['generatedAt']='y';self.assertEqual(a['frozenDigest'],f.freeze(c,self.m)['frozenDigest']);self.assertEqual(a['mode'],'shadow_only');self.assertNotIn('generatedAt',str(a))
 def test_fail_closed(self):c=copy.deepcopy(self.c);c['records'][0]['availableAt']=None;self.assertEqual(f.freeze(c,self.m)['mode'],'research_only')
