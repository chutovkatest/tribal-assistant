import unittest
from datetime import datetime,timezone,timedelta
from sk106_auto_scavenge import decide,iso
NOW=datetime(2026,10,12,0,15,tzinfo=timezone.utc)
def fixtures():
  return ({"player":{"name":"chlpatucky","world":"sk106","incomings":0},"villages":[{"id":1,"coords":"502|458","synced_at":iso(NOW-timedelta(minutes=2)),"units":[{"name":"spear","home":14,"away":0},{"name":"sword","home":10,"away":0}]}]},[{"village_id":1,"free_options":[1,2],"units_home":{"spear":14,"sword":10}}])
class Tests(unittest.TestCase):
  def test_valid(self):
    v,s=fixtures(); self.assertEqual(decide(v,s,{},NOW)["send"]["units"],{"spear":12,"sword":8})
  def test_wrong_account(self):
    v,s=fixtures();v["player"]["name"]="not_ours";self.assertEqual(decide(v,s,{},NOW)["skip"],"ACCOUNT_PIN_MISMATCH")
  def test_stale(self):
    v,s=fixtures();v["villages"][0]["synced_at"]=iso(NOW-timedelta(minutes=15));self.assertEqual(decide(v,s,{},NOW)["skip"],"GAME_STATE_STALE")
  def test_attack(self):
    v,s=fixtures();v["player"]["incomings"]=1;self.assertEqual(decide(v,s,{},NOW)["skip"],"INCOMING_ACTIVITY")
  def test_inflight(self):
    v,s=fixtures();v["villages"][0]["units"][0].update({"home":2,"away":12});s[0]["units_home"]["spear"]=2;self.assertEqual(decide(v,s,{},NOW)["skip"],"SCAVENGE_IN_FLIGHT")
  def test_pending(self):
    v,s=fixtures();self.assertEqual(decide(v,s,{"pending":{"at":iso(NOW-timedelta(minutes=4))}},NOW)["skip"],"PENDING_ACTION_NOT_RETRIED")
  def test_cooldown(self):
    v,s=fixtures();self.assertEqual(decide(v,s,{"last_sent_at":iso(NOW-timedelta(minutes=9))},NOW)["skip"],"RECENT_SUCCESS_COOLDOWN")
  def test_conflict(self):
    v,s=fixtures();s[0]["units_home"]["spear"]=3;self.assertEqual(decide(v,s,{},NOW)["skip"],"SCAVENGE_SYNC_CONFLICT")
  def test_tier_1(self):
    v,s=fixtures();s[0]["free_options"]=[1];self.assertEqual(decide(v,s,{},NOW)["send"]["option_id"],1)
if __name__=="__main__": unittest.main()
