import re
import unittest
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from tribal_assistant.core.game.locale_text import (
    report_category,is_battle_report,report_month,relative_day,is_loot_label
)
from tribal_assistant.core.game.scraper.game import (
    parse_protection,parse_report_date,parse_reports
)
from tribal_assistant.core.game.scraper.spy_report import SpyReportParser
from tribal_assistant.core.game.scraper.social import INTRO_THREAD,LEADER
from tribal_assistant.core.game.modules import game_sync
from tribal_assistant.core.game import actions

NOW=datetime(2026,10,12,10,0,0,tzinfo=timezone.utc)

class LocaleSupportTests(unittest.TestCase):
    def test_slovak_battle_titles(self):
        for title in ("Útok na dedinu (502|458)", "Správa o útoku (502|458)",
                      "Útočí dedina 502|458"):
            self.assertEqual(report_category(title),"attack",title)
            self.assertTrue(is_battle_report(title))
        for title in ("Špehovanie", "Správa o špionáži", "Prieskum dediny"):
            self.assertEqual(report_category(title),"scout",title)
    def test_portuguese_battle_backward_compat(self):
        self.assertEqual(report_category("Ataque contra aldeia"),"attack")
        self.assertEqual(report_category("Explorador entrou"),"scout")
        self.assertEqual(report_category("Apoio enviado"),"support")
    def test_other_report_types(self):
        self.assertEqual(report_category("Obchod dokončený"),"trade")
        self.assertEqual(report_category("Podpora dorazila"),"support")
        self.assertEqual(report_category("Neznáma správa"),"other")
    def test_dates_relative(self):
        self.assertEqual(parse_report_date("dnes o 12:30",NOW),
                         datetime(2026,10,12,10,30,tzinfo=timezone.utc))
        self.assertEqual(parse_report_date("včera o 08:10",NOW),
                         datetime(2026,10,11,6,10,tzinfo=timezone.utc))
        self.assertEqual(parse_report_date("hoje às 20:42",NOW),
                         datetime(2026,10,12,18,42,tzinfo=timezone.utc))
    def test_dates_calendar(self):
        expected=datetime(2026,10,12,18,42,tzinfo=timezone.utc)
        for text in ("12.10. 20:42","12.10.2026 o 20:42",
                     "okt. 12, 20:42","out. 12, 20:42","Oct 12, 20:42"):
            with self.subTest(text=text):
                self.assertEqual(parse_report_date(text,NOW),expected)
    def test_protection(self):
        for value in ("Začiatočnícka ochrana končí 13.10. o 20:38:21",
                      "Ochrana začiatočníka skončí 13.10. 20:38:21",
                      "A sua proteção de iniciante acaba em 13.10. às 20:38:21"):
            with self.subTest(value=value):
                self.assertEqual(parse_protection(value,NOW),
                                 datetime(2026,10,13,18,38,21,tzinfo=timezone.utc))
    def test_no_protection_hallucination(self):
        self.assertIsNone(parse_protection("Stavba končí 13.10. o 20:38:21",NOW))
        self.assertIsNone(parse_protection("",NOW))
    def test_no_malformed_date_crashes(self):
        self.assertIsNone(parse_report_date("99.99.2026 20:42",NOW))
        self.assertIsNone(parse_report_date("nie je dátum",NOW))
    def test_slovak_spy_buildings(self):
        for text,expected in (("Stajne","stable"),("Kováčska dielňa","smith"),
                              ("Trhovisko","market"),("Hradby","wall")):
            html=BeautifulSoup("<td>"+text+"</td>","lxml")
            self.assertEqual(SpyReportParser.building_name(html.td),expected)
    def test_tribe_role_and_intro(self):
        self.assertIsNotNone(LEADER.search("Vodca kmeňa"))
        self.assertIsNotNone(INTRO_THREAD.search("Privítanie nových členov"))
    def test_detail_read_requires_language_neutral_classification(self):
        import inspect
        self.assertIn("is_battle_report",inspect.getsource(game_sync._read_reports))
    def test_browser_script_labels(self):
        self.assertIn("koris",game_sync.REPORT_DETAIL_JS)
        self.assertIn("Obchodn",actions.MERCHANTS_JS)
        self.assertIn("Aktu",actions.CURRENT_FLAG_JS)
        self.assertIn("Železo",actions.MARKET_OFFERS_JS)
    def test_actual_report_category_and_date(self):
        row={"id":"100","title":"Útok na dedinu (502|458)",
             "received":"dnes o 12:30","icons":"img/dots/green.png","is_new":True}
        report=parse_reports([row],now=NOW)[0]
        self.assertEqual(report.category,"attack")
        self.assertIsNotNone(report.received_at)

if __name__=="__main__":
    unittest.main()
