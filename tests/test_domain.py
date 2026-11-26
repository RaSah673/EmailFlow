import sys, unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.domain import chunk_ranges,valid_chunk,delivery_key,require_aware
class DomainTests(unittest.TestCase):
    def test_small(self): self.assertEqual(list(chunk_ranges(0,201,100)),[(1,100),(101,200),(201,201)])
    def test_million(self):
        cursor=0; count=0
        while cursor<1000000:
            batch=list(chunk_ranges(cursor,1000000,100)); count+=len(batch)
            for a,b in batch: self.assertTrue(valid_chunk(a,b,1000000,100)); self.assertEqual(a,cursor+1);cursor=b
        self.assertEqual((cursor,count),(1000000,10000))
    def test_replay_determinism(self): self.assertEqual(list(chunk_ranges(3200,10000,100)),list(chunk_ranges(3200,10000,100)))
    def test_invalid_chunk(self):
        for a,b in [(0,100),(1,99),(2,101),(101,201),(201,301)]: self.assertFalse(valid_chunk(a,b,250,100))
    def test_key(self): self.assertNotEqual(delivery_key('a',1),delivery_key('a',2));self.assertNotEqual(delivery_key('a',1),delivery_key('b',1))
    def test_timezone(self):
        with self.assertRaises(ValueError): require_aware(datetime.now())
        self.assertIsNotNone(require_aware(datetime.now(timezone.utc)))
    def test_empty(self): self.assertEqual(list(chunk_ranges(100,100,100)),[])
if __name__=='__main__': unittest.main()
