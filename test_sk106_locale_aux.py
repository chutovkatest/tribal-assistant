import unittest
from datetime import datetime,timezone
from tribal_assistant.core.agents.clock import Clock
from tribal_assistant.core.game.incoming import size_of
from tribal_assistant.core.game.items import ItemCount
from tribal_assistant.core.game.market import SendParser
from tribal_assistant.core.game.diplomacy import GONE,MENTORS_JS
from tribal_assistant.core.game.profile import EDIT
from tribal_assistant.core.game.tribe import FORUM_OPEN

class AuxiliaryLocaleTests(unittest.TestCase):
    def test_slovak_attack_sizes(self):
        self.assertEqual(size_of("Malý útok"),"small")
        self.assertEqual(size_of("Stredný útok"),"medium")
        self.assertEqual(size_of("Veľký útok"),"large")
        self.assertEqual(size_of("pequeno"),"small")
    def test_local_clock_sk(self):
        c=Clock(now=datetime(2026,10,12,12,0,tzinfo=timezone.utc))
        self.assertIn("(em ",c.annotate("Dostupné dnes o 15:00"))
        self.assertIn("(em ",c.annotate("Zajtra o 09:00"))
        self.assertIn("(em ",c.annotate("hoje às 15:00"))
    def test_relic_item_count(self):
        self.assertEqual(ItemCount.of("Vlastníte: 3"),3)
        self.assertEqual(ItemCount.of("Da propriedade de: 4"),4)
    def test_market_owner_sk(self):
        html='<div id="content_value"><tr><td>Hráč:</td><td>chlpatucky</td></tr> (502|458)</div>'
        self.assertEqual(SendParser.confirmation(html)["player"],"chlpatucky")
    def test_tribe_and_profile(self):
        self.assertIn("Kmeň neexistuje",GONE)
        self.assertIn("Body",MENTORS_JS)
        self.assertIn("Upraviť profil",EDIT)
        self.assertIn("Odpovedať",FORUM_OPEN)

if __name__=="__main__":unittest.main()
