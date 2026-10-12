import unittest
from sk106_lc_rush_policy import lc_rush_candidates

def snapshot(smith=3, main=8, stable=0, smith_queue=None, main_queue=None):
    def b(name, level, queue=None):
        return {"name":name,"level":level,"queued_level":queue,
                "next_level":level+1 if not queue else level+1}
    return {"buildings":[b("smith",smith,smith_queue),
                         b("main",main,main_queue),b("stable",stable)]}

class LCRushTests(unittest.TestCase):
    def test_smith_first(self):
        active,pick,goal=lc_rush_candidates(snapshot(),["smith","main","stone"])
        self.assertTrue(active)
        self.assertEqual((pick,goal),(["smith"],"smith:4"))
    def test_no_distraction_when_short_on_clay(self):
        active,pick,goal=lc_rush_candidates(snapshot(),["stone","wall"])
        self.assertEqual(pick,[])
        self.assertEqual(goal,"WAIT_FOR_smith:4")
    def test_after_queued_smith_pick_main(self):
        active,pick,goal=lc_rush_candidates(snapshot(smith_queue=4),["main"])
        self.assertEqual((pick,goal),(["main"],"main:9"))
    def test_after_main_and_smith_queue(self):
        active,pick,goal=lc_rush_candidates(snapshot(smith=4,main_queue=9),["smith"])
        self.assertEqual((pick,goal),(["smith"],"smith:5"))
    def test_after_smith5_main10(self):
        active,pick,goal=lc_rush_candidates(snapshot(smith=5,main=9),["main"])
        self.assertEqual((pick,goal),(["main"],"main:10"))
    def test_stable_locked_is_safe(self):
        active,pick,goal=lc_rush_candidates(snapshot(smith=5,main=10),["wall"])
        self.assertEqual((pick,goal),([],"WAIT_FOR_stable:1"))
    def test_all_milestones_stop(self):
        active,pick,goal=lc_rush_candidates(snapshot(smith=5,main=10,stable=3),["wall"])
        self.assertFalse(active)
        self.assertEqual((pick,goal),([],"LC_MILESTONES_READY_CHECK_RESEARCH"))
    def test_missing_core_buildings_fail_closed(self):
        self.assertFalse(lc_rush_candidates({"buildings":[]},["wall"])[0])
if __name__=="__main__":
    unittest.main()
