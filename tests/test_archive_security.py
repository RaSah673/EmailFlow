import unittest,sys,base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.ranking import chunks,score
from app.totp import totp,matching_step
class SearchTests(unittest.TestCase):
    def test_long_body(self):
        body='a'*20000; parts=chunks('subject',body);self.assertEqual(len(parts),20);self.assertTrue(all(len(p)<=1401 for p in parts));self.assertTrue(all(p.startswith('subject') for p in parts))
    def test_weighting(self):
        a={'semantic':1,'keyword':0,'recency':0,'priority':0};b={'semantic':0,'keyword':0,'recency':0,'priority':1}
        self.assertGreater(score(.9,0,30,False,a)[0],score(.2,0,0,True,a)[0]);self.assertLess(score(.9,0,30,False,b)[0],score(.2,0,0,True,b)[0])
    def test_clamp_normalize(self):
        val,parts=score(2,-1,0,True,{'semantic':1,'keyword':1,'recency':1,'priority':1});self.assertEqual(val,.75);self.assertEqual(parts['semantic'],1)
    def test_recency(self):self.assertGreater(score(0,0,1,False,{'recency':1})[0],score(0,0,90,False,{'recency':1})[0])
    def test_empty_weights(self):
        with self.assertRaises(ValueError):score(0,0,0,False,{'semantic':0})
class TotpTests(unittest.TestCase):
    key=base64.b32encode(b'12345678901234567890').decode().rstrip('=')
    def test_rfc6238_vector(self):self.assertEqual(totp(self.key,59//30),'287082')
    def test_replay(self):self.assertEqual(matching_step(self.key,'287082',-1,59),1);self.assertIsNone(matching_step(self.key,'287082',1,59))
    def test_wrong_code(self):self.assertIsNone(matching_step(self.key,'000000',-1,59))
if __name__=='__main__':unittest.main()
