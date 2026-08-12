import io,json,os,unittest,zipfile
import artifact_shadow_extract as a
def blob(files):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for n,v in files.items():z.writestr(n,json.dumps(v))
 return b.getvalue()
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['ARTIFACT_SHADOW_EXTRACT_ENABLED']='true'
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_exact_allowlist(self):o=a.extract(blob({'data/evidence-contract.json':{},'data/candidate-manifest.json':{}}));self.assertEqual(o['mode'],'shadow_only')
 def test_missing_or_duplicate_fails(self):self.assertEqual(a.extract(blob({'data/evidence-contract.json':{}}))['mode'],'research_only');self.assertEqual(a.extract(b'not zip')['mode'],'research_only')
