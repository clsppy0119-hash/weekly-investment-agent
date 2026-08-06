import json,tempfile,unittest
from pathlib import Path
import shadow_real_data_probe as p
class T(unittest.TestCase):
 def test_allowlisted_read_only_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   x=Path(d);(x/'fundamentals-coverage.json').write_text(json.dumps({'schemaVersion':1}));(x/'raw.json').write_text('{"secret":1}')
   out=p.run(x);self.assertEqual(out['mode'],'research_only');self.assertTrue(out['readOnly']);self.assertEqual([a['name'] for a in out['foundAllowlistedSummaries']],['fundamentals-coverage.json'])
