import os,unittest,copy
import pit_shadow_sidecar as p
class T(unittest.TestCase):
 def setUp(self):self.old=dict(os.environ);os.environ['PIT_SHADOW_SIDECAR_ENABLED']='true';self.x={'candidates':[{'code':'2330','score':1}],'backtest':{'fee':.001}}
 def tearDown(self):os.environ.clear();os.environ.update(self.old)
 def test_attach_never_changes_input(self):
  before=p.h(self.x);out=p.attach(self.x,{'mode':'shadow_only','coverage':1.0,'selectedVersionSetHash':'v','decisionAsOf':'2026-01-01T00:00:00+08:00','blockers':[]});self.assertEqual(p.h(self.x),before);self.assertEqual(out['mode'],'shadow_only');self.assertEqual(out['inputHash'],before)
 def test_uncertain_or_off_fails_closed(self):
  self.assertEqual(p.attach(self.x,{'mode':'shadow_only','coverage':.9})['mode'],'research_only');os.environ.pop('PIT_SHADOW_SIDECAR_ENABLED');self.assertEqual(p.attach(self.x,{})['mode'],'disabled')
