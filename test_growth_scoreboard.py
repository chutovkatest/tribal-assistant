import unittest
from datetime import datetime, timezone, timedelta
from sk106_growth_scoreboard import compute, wait_minutes

NOW=datetime(2026,10,11,20,5,tzinfo=timezone.utc)

def fixture():
    v={
        "world":"sk106","account":"chlpatucky","points":312,
        "rank":896,"source_synced_at":(NOW-timedelta(minutes=4)).isoformat(),
        "resources":{"wood":1203,"clay":468,"iron":372},
        "production_per_hour":{"wood":224,"clay":193,"iron":143},
        "queue":[{"building":"smith","level":4,"until":"2026-10-11T20:20:57Z"}],
        "units":{"spear":{"home":14},"sword":{"home":10},"knight":{"home":1}},
        "milestones":[
            {"building":"main","target_level":9,"status":"saving","cost":{"wood":572,"clay":559,"iron":445}},
            {"building":"smith","target_level":5,"status":"locked_or_preceding_level","cost":None}]}
    w={
        "observed_at":(NOW-timedelta(minutes=10)).isoformat(),
        "previous_at":(NOW-timedelta(minutes=70)).isoformat(),
        "players_of_interest":[{"player_id":1086181,"points":312,"delta_points":0},
                               {"player_id":1054384,"points":412,"delta_points":17}]}
    return v,w

class TestGrowthScoreboard(unittest.TestCase):
    def test_competitor_gap_and_rate(self):
        v,w=fixture()
        result=compute(v,w,NOW)
        self.assertEqual(result["point_gap"],100)
        self.assertEqual(result["relative_points_per_hour"],-17)
        self.assertEqual(result["gap_trend"],"LOSING_GROUND_OR_FLAT")
        self.assertIsNone(result["hypothetical_catchup_hours_at_observed_rates"])
    def test_build_eta(self):
        v,w=fixture()
        report=compute(v,w,NOW)
        self.assertEqual(report["next_build_resource_wait_min_estimate"],31)
        self.assertEqual(report["ordinary_troops_home"],24)
        self.assertFalse(report["construction_queue_empty"])
    def test_stale_rival_refused(self):
        v,w=fixture()
        w["observed_at"]=(NOW-timedelta(hours=3)).isoformat()
        report=compute(v,w,NOW)
        self.assertEqual(report["world_status"],"STALE")
        self.assertIsNone(report["veronika_points"])
        self.assertIsNone(report["point_gap"])
    def test_stale_private_refused(self):
        v,w=fixture()
        v["source_synced_at"]=(NOW-timedelta(hours=1)).isoformat()
        report=compute(v,w,NOW)
        self.assertEqual(report["own_status"],"STALE")
        self.assertIsNone(report["our_points"])
        self.assertIsNone(report["point_gap"])
    def test_account_pin(self):
        v,w=fixture()
        v["account"]="veroniccba"
        with self.assertRaisesRegex(RuntimeError,"PIN"):
            compute(v,w,NOW)
    def test_missing_competitor(self):
        v,w=fixture()
        w["players_of_interest"]=w["players_of_interest"][:1]
        report=compute(v,w,NOW)
        self.assertEqual(report["gap_trend"],"INSUFFICIENT_EVIDENCE")
    def test_no_resource_production(self):
        self.assertIsNone(wait_minutes({"wood":5,"clay":3,"iron":2},
                                       {"wood_prod":1,"clay_prod":0,"iron_prod":1},
                                       {"wood":2,"clay":10,"iron":2}))
    def test_catching_up_projection(self):
        v,w=fixture()
        w["players_of_interest"][0]["delta_points"]=40
        report=compute(v,w,NOW)
        self.assertEqual(report["relative_points_per_hour"],23)
        self.assertEqual(report["hypothetical_catchup_hours_at_observed_rates"],4.3)

if __name__=="__main__":
    unittest.main()
