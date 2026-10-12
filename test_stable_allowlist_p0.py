"""Regression: real watchdog can move through all stables milestones."""
import unittest
from agy_watchdog import ALLOWED, affordable
from sk106_lc_rush_policy import lc_rush_candidates

def village(level=0, queued=None, can_build=True, resource=3000):
    return {
        "wood": resource, "clay": resource, "iron": resource,
        "buildings":[
            {"name":"smith","level":5,"next_level":6,"queued_level":None},
            {"name":"main","level":10,"next_level":11,"queued_level":None},
            {"name":"stable","level":level,"next_level":level+1 if can_build else None,
             "queued_level":queued,"can_build":can_build,
             "next_wood":270,"next_clay":240,"next_iron":260}]
    }

class StableAllowlistIntegration(unittest.TestCase):
    def test_whitelisted(self):
        self.assertIn("stable", ALLOWED)
    def test_all_stable_levels_queue(self):
        for stage in (0,1,2):
            with self.subTest(stage=stage):
                v=village(stage)
                candidates=affordable(v)
                self.assertIn("stable", candidates)
                self.assertEqual(lc_rush_candidates(v,candidates),
                                (True,["stable"],f"stable:{stage+1}"))
    def test_not_affordable_stays_reserved(self):
        v=village(resource=0)
        self.assertNotIn("stable",affordable(v))
        self.assertEqual(lc_rush_candidates(v,affordable(v)),
                         (True,[],"WAIT_FOR_stable:1"))
    def test_queued_does_not_retry(self):
        v=village(level=0,queued=1)
        self.assertNotIn("stable", affordable(v))
        self.assertEqual(lc_rush_candidates(v,affordable(v)),
                         (True,[],"WAIT_FOR_stable:2"))
    def test_blocked_does_not_send(self):
        v=village(can_build=False)
        self.assertNotIn("stable",affordable(v))
    def test_ready_after_stage3(self):
        v=village(level=3)
        self.assertEqual(lc_rush_candidates(v, affordable(v)),
                         (True,[],"LC_MILESTONES_READY_CHECK_RESEARCH"))

if __name__=="__main__":
    unittest.main()
