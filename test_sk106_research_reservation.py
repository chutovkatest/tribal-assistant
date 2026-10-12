import unittest
from agy_watchdog import LC_RESEARCH_COST, light_research_reserve, ranked_candidates

def village(main=10, smith=5, stable=3, available=False, blocker="Stajne (Stupeň 3) Výskum"):
    return {
        "buildings": [
            {"name": "main", "level": main},
            {"name": "smith", "level": smith},
            {"name": "stable", "level": stable},
        ],
        "units": [{"name": "light", "available": available, "blocker": blocker}],
        "wood": 3300, "clay": 2600, "iron": 1400,
    }

class ResearchReservationTests(unittest.TestCase):
    def test_cost_is_verified_20261012(self):
        self.assertEqual(LC_RESEARCH_COST, {"wood":2200,"clay":2400,"iron":2000})

    def test_reserve_while_research_locked(self):
        v=village()
        self.assertTrue(light_research_reserve(v))
        self.assertEqual(ranked_candidates(v,["storage","iron"],[]),[])

    def test_does_not_reserve_before_milestones(self):
        self.assertFalse(light_research_reserve(village(stable=2)))

    def test_does_not_reserve_once_unlocked(self):
        self.assertFalse(light_research_reserve(village(available=True,blocker=None)))

    def test_does_not_reserve_missing_units(self):
        v=village();v.pop("units")
        self.assertFalse(light_research_reserve(v))

if __name__=="__main__":
    unittest.main()
