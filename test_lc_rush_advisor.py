import unittest
from datetime import datetime, timezone, timedelta
from sk106_lc_rush_advisor import advice, steps, wait_minutes


def build(name, level, cost=None, queued=None):
    return {"name":name,"level":level,"next_level":level+1 if cost else None,
            "next_wood":cost[0] if cost else None,
            "next_clay":cost[1] if cost else None,
            "next_iron":cost[2] if cost else None,
            "can_build":cost is not None,"queued_level":queued,"queued_until":None}

def world():
    return {
        "coords":"502|458","synced_at":datetime.now(timezone.utc).isoformat(),
        "wood":973,"clay":238,"iron":407,"wood_prod":224,"clay_prod":193,
        "iron_prod":143,
        "buildings":[build("main",8,(572,559,445)),
                     build("smith",3,(440,373,480)),
                     build("stable",0),build("stone",10,(709,525,344))],
        "units":[]}

class TestLcRushAdvisor(unittest.TestCase):
    def test_smith4_earlier_than_main9(self):
        v=world()
        a=advice({"name":"chlpatucky","world":"sk106","points":308,"rank":880},
                 v,{"quests":[]},[])
        self.assertEqual(a["mode"],"READ_ONLY_MANUAL_ADVISORY")
        self.assertEqual(a["primary_manual_step"]["building"],"smith")
        self.assertEqual(a["primary_manual_step"]["target_level"],4)
        self.assertLess(a["primary_manual_step"]["resource_wait_min"], 60)
    def test_queued_smith_recommends_main(self):
        v=world()
        v["buildings"][1]["queued_level"]=4
        a=steps(v)
        self.assertNotIn(("smith",4),[(x["building"],x["target_level"]) for x in a])
        self.assertEqual(next(x for x in a if x["status"]=="saving")["building"],"main")
    def test_no_fabricated_stable_cost(self):
        v=world()
        st=next(x for x in steps(v) if x["building"]=="stable")
        self.assertIsNone(st["cost"])
        self.assertIsNone(st["resource_wait_min"])
    def test_account_pinning(self):
        with self.assertRaisesRegex(RuntimeError,"ACCOUNT_PIN"):
            advice({"name":"veroniccba","world":"sk106"},world(),{"quests":[]},[])
    def test_stale_status_refused(self):
        v=world()
        v["synced_at"]=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.assertRaisesRegex(RuntimeError,"STALE_GAME"):
            advice({"name":"chlpatucky","world":"sk106"},v,{"quests":[]},[])
    def test_need_unavailable_production(self):
        v=world()
        v["iron_prod"]=0
        self.assertIsNone(wait_minutes(v,v["buildings"][1]))

if __name__=="__main__":
    unittest.main()
