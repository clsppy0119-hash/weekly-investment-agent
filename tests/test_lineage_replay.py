import os, unittest
import lineage_replay

class ReplayTests(unittest.TestCase):
 def setUp(self):
  self.old=dict(os.environ); os.environ['LINEAGE_REPLAY_ENABLED']='true'
  self.row={'compositeKey':'k','provider':'MOPS','dataset':'filing','entityId':'2330','observationPeriod':'2026Q1','sourceRevision':'v1','availableAt':'2026-05-15T10:00:00+08:00','contentHash':'h','status':'success','conflictStatus':'no_conflict','schemaVersion':1}
 def tearDown(self): os.environ.clear(); os.environ.update(self.old)
 def summary(self):
  base={'schemaVersion':1,'decisionAsOf':'2026-06-01T00:00:00+08:00','coverage':1.0,'records':[self.row]}
  h=lineage_replay.digest([{k:self.row[k] for k in ('compositeKey','contentHash','availableAt','sourceRevision')}]); base.update(expectedVersionSetHash=h,expectedSnapshotHash=h); return base
 def test_default_off(self):
  os.environ.pop('LINEAGE_REPLAY_ENABLED'); self.assertEqual(lineage_replay.replay(self.summary())['mode'],'disabled')
 def test_frozen_summary_replays(self):
  out=lineage_replay.replay(self.summary()); self.assertEqual(out['mode'],'shadow_only'); self.assertTrue(out['snapshotHashMatch']); self.assertNotIn('endpoint',str(out))
 def test_unknown_conflict_partial_future_or_ambiguous_fail_closed(self):
  cases = [
   {'coverage': .9},
   {'decisionAsOf': '2026-05-01T00:00:00+08:00'},
   {'records': [dict(self.row, conflictStatus='unknown')]},
   {'records': [self.row, dict(self.row, contentHash='other')]},
   {'records': [dict(self.row, availableAt="")]},
  ]
  for change in cases:
   s=self.summary(); s.update(change); self.assertEqual(lineage_replay.replay(s)['mode'],'research_only')
 def test_timezone_and_polluted_fields_fail_closed(self):
  s=self.summary(); s['decisionAsOf']='2026-06-01'; self.assertEqual(lineage_replay.replay(s)['mode'],'research_only')
  s=self.summary(); s['records']=[dict(self.row,endpoint='https://secret')]; self.assertEqual(lineage_replay.replay(s)['mode'],'research_only')
