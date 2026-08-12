import io,json,os,stat,unittest,zipfile
from types import SimpleNamespace
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
 def test_safe_extra_file_is_ignored(self):
  o=a.extract(blob({'data/evidence-contract.json':{},'data/candidate-manifest.json':{},'daily-report.md':'ok'}));self.assertEqual(o['mode'],'shadow_only')
 def test_all_members_are_preflighted(self):
  attacks=['../escape','DATA/EVIDENCE-CONTRACT.JSON','nested.zip','caf\u00e9.json']
  for name in attacks:
   with self.subTest(name=name):
    files={'data/evidence-contract.json':{},'data/candidate-manifest.json':{},name:'x'}
    self.assertEqual(a.extract(blob(files))['mode'],'research_only')
  for name in ('bad\\name','bad\u0000name'):
   with self.subTest(name=name),self.assertRaises(ValueError):
    a._preflight([SimpleNamespace(filename=name,is_dir=lambda:False,flag_bits=0,external_attr=0,file_size=1,compress_size=1)])
 def test_casefold_collision_and_special_member_fail(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('data/evidence-contract.json','{}');z.writestr('data/candidate-manifest.json','{}');z.writestr('extra.txt','x');z.writestr('EXTRA.TXT','y')
  self.assertEqual(a.extract(b.getvalue())['mode'],'research_only')
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('data/evidence-contract.json','{}');z.writestr('data/candidate-manifest.json','{}')
   info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'target')
  self.assertEqual(a.extract(b.getvalue())['mode'],'research_only')
 def test_high_ratio_extra_member_fails(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
   z.writestr('data/evidence-contract.json','{}');z.writestr('data/candidate-manifest.json','{}');z.writestr('extra.txt','A'*10000)
  self.assertEqual(a.extract(b.getvalue())['mode'],'research_only')
