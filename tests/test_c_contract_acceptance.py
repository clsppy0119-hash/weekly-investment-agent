"""C-1..C-8 end-to-end acceptance: shadow additions stay isolated."""
import os,unittest
import lineage_shadow,lineage_writer,lineage_replay,shadow_consistency,pit_shadow_sidecar,lineage_drift_shadow,provenance_health_shadow
class T(unittest.TestCase):
 def test_all_shadow_features_default_off(self):
  keys=['LINEAGE_SHADOW_ENABLED','LINEAGE_REPLAY_ENABLED','LINEAGE_CONSISTENCY_ENABLED','PIT_SHADOW_SIDECAR_ENABLED','LINEAGE_DRIFT_SHADOW_ENABLED','PROVENANCE_HEALTH_ENABLED']
  old=dict(os.environ)
  try:
   for k in keys:os.environ.pop(k,None)
   self.assertFalse(lineage_shadow.enabled());self.assertEqual(lineage_writer.write([])['status'],'disabled');self.assertEqual(lineage_replay.replay({})['mode'],'disabled');self.assertEqual(shadow_consistency.report({})['mode'],'disabled');self.assertEqual(pit_shadow_sidecar.attach({}, {})['mode'],'disabled');self.assertEqual(lineage_drift_shadow.compare({}, {})['mode'],'disabled');self.assertEqual(provenance_health_shadow.report([])['mode'],'disabled')
  finally:os.environ.clear();os.environ.update(old)
