import unittest
from test_engine import d
class Tests(unittest.TestCase):
 def test_session_shared_until_expiry(self):
  now=[0.0];calls=[];value=[True]
  probe=d.SessionProbe(lambda:calls.append(1) or value[0],clock=lambda:now[0])
  for _ in range(8):self.assertTrue(probe())
  self.assertEqual(len(calls),1)
  value[0]=False;now[0]=1.01;self.assertFalse(probe());self.assertEqual(len(calls),2)
 def test_mutation_invalidates_even_within_ttl(self):
  value=[True];probe=d.SessionProbe(lambda:value[0],clock=lambda:0)
  self.assertTrue(probe());value[0]=False;probe.invalidate();self.assertFalse(probe())
 def test_probe_failure_does_not_extend_cache(self):
  calls=[]
  def fail():calls.append(1);raise OSError('logind missing')
  probe=d.SessionProbe(fail,clock=lambda:0)
  for _ in range(2):
   with self.assertRaises(OSError):probe()
  self.assertEqual(len(calls),2)
