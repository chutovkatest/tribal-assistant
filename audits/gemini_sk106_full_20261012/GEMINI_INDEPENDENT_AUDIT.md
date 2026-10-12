# Independent Gemini review

# Gemini independent audit — SK106

## Executive verdict (scores 0-10 for functionality/strategy/automation/testing/observability)

- **Funkčnosť (Functionality): 3/10** — Systém je v stave akútnej stavebnej paralýzy. Po dokončení Stajní 3 stavebný rad úplne zastal, suroviny nečinne stoja v sklade a hrozí pretečenie dreva. Výskum ani nábor Ľahkej jazdy nie sú automatizované a questové odmeny ležia nevyzdvihnuté.
- **Stratégia (Strategy): 5/10** — Zámer "Light Cavalry Rush" je pre svet SK106 optimálny, avšak exekúcia zlyhala na rozhraní dokončenia infraštruktúry a výskumu. Suroviny sa nezhodnocujú a náskok rivalky veroniccba (60 bodov) neklesá.
- **Automatizácia (Automation): 4/10** — Izolované procesy (`agy_watchdog.py` a `sk106_auto_scavenge.py`) majú precízne ošetrené zamykanie (`fcntl.flock`) a prevenciu duplicitných POST volaní. Globálne nastavenie asistenta však beží v `dry_run: true`, výskum a verbovanie nie sú implementované a chýba automatické preberanie questov.
- **Testovanie (Testing): 4/10** — Existujúce jednotkové testy pokrývajú iba čiastkové regexy, allowlist stajní a základné vetvy zberu. Chýbajú integračné testy prechodu fáz (míľnik dosiahnutý -> prechod do ekonomického režimu/výskumu) a testy správania pri naplnení skladu.
- **Pozorovateľnosť (Observability): 5/10** — Snapshoty a čítanie herného stavu fungujú spoľahlivo, avšak tiché uviaznutie systému v stave `LC_MILESTONES_READY_CHECK_RESEARCH` nevyvoláva žiaden alert a watchdog bez varovania ignoruje 12 dostupných budov.
- **Poznámka k repozitáru:** Kontrola dostupnosti repozitárových zručností (scan `AGENTS.md` / `SKILL.md`) nevrátila žiadne nálezy; špecializované rozširujúce skilly nie sú v repozitári prítomné.

---

## P0 findings (real confirmed blockers / causes)

### 1. Paralyzovaný stavebný rad po dosiahnutí Stajní 3 (Deadlock politiky)
- **Súbory a riadky:** [`sk106_lc_rush_policy.py` L0018-L0026](file:///home/worker/tribal-sk106-lab/sk106_lc_rush_policy.py#L18-L26), [`agy_watchdog.py` L0124-L0128](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L124-L128), [`agy_watchdog.py` L0313-L0328](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L313-L328).
- **Overený fakt:** V live stave sú budovy: `main: 10`, `smith: 5`, `stable: 3`. Všetky podmienky z `MILESTONES` sú splnené (`current >= level`). Funkcia `lc_rush_candidates` v [`sk106_lc_rush_policy.py` L0026](file:///home/worker/tribal-sk106-lab/sk106_lc_rush_policy.py#L26) vracia `(True, [], "LC_MILESTONES_READY_CHECK_RESEARCH")`.
- **Koreňová príčina:** V [`agy_watchdog.py` L0125-L0128](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L125-L128) platí:
  ```python
  active, chosen, milestone = lc_rush_candidates(v, candidates)
  if active:
      return chosen
  ```
  Keďže `active == True` a `chosen == []`, funkcia vráti prázdny zoznam `[]`. Kód sa nikdy nedostane k `sk106_growth_engine` ani k záložnému ekonomickému hodnoteniu (L0130-L0166). Navyše podmienka `and not lc_active` v L0328 zablokovala volanie AI poradcu (`last_ai_success` je starý takmer 20 hodín).
- **Dopad:** Stavebný rad je prázdny (`queue: []`), hoci je dostupných 12 budov (`affordable: 12`). Posledná stavba prebehla o 13:06:55 UTC (Stajne 3). Odvtedy (takmer 3 hodiny) dedina nestavia vôbec nič.

### 2. Absencia automatizácie výskumu a náboru Ľahkej jazdy
- **Súbory a riadky:** [`agy_watchdog.py` L0042-L0048](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L42-L48), Sanitized Live State (`units.light`).
- **Overený fakt:** V hernom stave má jednotka `light` blokátor `"Stajne (Stupeň 3) Výskum"` a `available: false`. Watchdog disponuje výhradne metódou `post_act` volajúcou nástroj `"upgrade_building"` (L0043).
- **Koreňová príčina:** V systéme neexistuje žiadny aktívny ovládač pre výskum v kováčskej dielni (`research_unit`) ani pre nábor vojska v stajniach. Core asistent beží v `dry_run: true`.
- **Dopad:** Systém uviazol v stave čakania na výskum, ktorý sám nedokáže vykonať, a súčasne zablokoval akúkoľvek alternatívnu výstavbu.

### 3. Akútne riziko pretečenia skladu (Resource Overflow)
- **Súbory a riadky:** Sanitized Live State (`village.resources`, `village.buildings`).
- **Overený fakt:** Drevo = 3683, Kapacita skladu (úroveň 8) = 4247, Produkcia dreva = 224/hod. Voľná kapacita je len `4247 - 3683 = 564`.
- **Dopad:** Pri nulovej výstavbe nastane pretečenie skladu do cca 2 hodín a 31 minút od synchronizácie (okolo 18:25 UTC). Akákoľvek nevyužitá produkcia predstavuje čistú stratu strategického tempa.

---

## P1/P2 findings (clear facts versus hypotheses)

### P1: Nevyzdvihnuté odmeny z questov (Fakt)
- **Dôkaz:** V stave `quests.rewards` ležia 3 odmeny: ID 158760 (Kováčska dielňa 4: 150/150/100), ID 159982 (Hlavná budova 9: 150/150/100), ID 176963 (Stajne 1: 150/150/100). Celkovo 450 dreva, 450 hliny, 300 železa. Quest "Boj o informácie" má `state: "finished", can_complete: true`.
- **Súvislosť s kódom:** V [`tribal_assistant/core/game/scraper/quests.py` L0094-L0102](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/game/scraper/quests.py#L94-L102) existuje len pasívny parser. Neexistuje žiadny automatický claimer. Odmeny stoja ladom.
- **Kritické upozornenie:** Okamžité vyzdvihnutie bez spustenia stavby/výskumu by okamžite zaplnilo sklad dreva (`3683 + 450 = 4133 / 4247`).

### P1: Globálny dry_run a asymetria exekúcie (Fakt)
- **Dôkaz:** `agent_settings.dry_run: true`. Moduly v `tribal_assistant` (nábor, presuny surovín, útoky) bežia výhradne simulovane. Jediné reálne mutácie vykonávajú `agy_watchdog.py` a `sk106_auto_scavenge.py`. Výskum a verbovanie cez `tribal_assistant` do hry neprejdú.

### P1: Lokalizačné relikty v parseroch a guardrails (Fakt)
- **Dôkaz:** V [`tribal_assistant/core/game/scraper/quests.py` L0101](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/game/scraper/quests.py#L101) kód vykonáva `label.replace("Reivindicar", "").strip()`. Na slovenskom serveri je text tlačidla "Prevziať" / "Prevziať všetko" (viď live state `"label":"Kováčska dielňa 4 150 150 100 Prevziať Prevziať všetko"`), takže reťazec sa neodstráni.
- V [`tribal_assistant/core/agents/guardrails.py` L0048-L0070](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/agents/guardrails.py#L48-L70) a [`tribal_assistant/core/game/scraper/game.py` L0027-L0039](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/game/scraper/game.py#L27-L39) sú požiadavky a odmietnutia v portugalčine (`"requisitos não atendidos"`, `"nível máximo"`, `"Ferreiro (Nível 1)"`), hoci herné texty v live stave sú zmesou slovenčiny a portugalčiny.

### P2: Chýbajúce budovy v allowliste pre ďalší postup (Fakt)
- **Dôkaz:** V [`agy_watchdog.py` L0024](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L24) zoznam `ALLOWED` obsahuje 12 budov. Chýba v ňom `garage` (dielňa) a `snob` (panský dvor/akadémia). V neskoršej fáze watchdog tieto kľúčové budovy odmietne zaradiť do výstavby (regresia identická s minulým stavom stajní overeným v [`test_stable_allowlist_p0.py` L0019](file:///home/worker/tribal-sk106-lab/test_stable_allowlist_p0.py#L19)).

### P2: Extrémne sieťové timeouty blokujúce watchdog (Fakt)
- **Dôkaz:** V [`agy_watchdog.py` L0048, L0056](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L48) je nastavený synchrónny timeout `timeout=180` sekúnd. V live stave je evidovaných 6 transportných chýb (`transport_timeout_count: 6`, `last_transport_error: TimeoutError na "wall"`). Jedno zamrznutie prehliadača zablokuje celý 3-minútový cyklus.

### Hypotéza verzus fakty: Súperka veroniccba
- **Fakty:** veroniccba má 429 bodov, rank 595, kmeň ID 32, vzdialenosť 3.16 poľa, zmena bodov `delta_points: 0`. Hráč chlpatucky má 369 bodov (strata 60 bodov), je bez kmeňa (`tribe_id: 0`). Začiatočnícka ochrana trvá do 14. 10. 2026 19:45:36 UTC.
- **Hypotéza (NEVERIFIKOVANÉ):** Z verejných dát nemožno odvodiť stavbu budov, veľkosť armády ani úmysly veroniccba. Akékoľvek tvrdenie o jej vojsku je nepodložená špekulácia.

---

## Root cause chain (how 24h still loses)

```
[Míľnik Stajne 3 dosiahnutý v čase 13:06 UTC]
                     │
                     ▼
[sk106_lc_rush_policy.py vráti (True, [], "LC_MILESTONES_READY_CHECK_RESEARCH")]
                     │
                     ▼
[agy_watchdog.py zablokuje fallback na ekonomiku aj volanie AI poradcu]
                     │
                     ▼
[Stavebný rad ostáva prázdny (0 stavieb, 12 budov dostupných)]
                     │
                     ▼
[Sklad dreva (3683/4247) pretečie o ~18:25 UTC -> strata ~224 dreva/h]
                     │
                     ▼
[Watchdog nemá modul pre výskum; core asistent beží v dry_run: true]
                     │
                     ▼
[Ľahká jazda sa nevyskúma, vojsko sa neverbuje, scavenging beží len na 20 pešiakov]
                     │
                     ▼
[14. 10. 19:45 UTC vyprší začiatočnícka ochrana:
 hráč stojí na 369 bodoch, bez jazdy, bez kmeňa, kým veroniccba v kmeni dominuje]
```

---

## Top 10 prioritized fixes

| Priorita | Vlastník | Úžitok | Testovateľná Done Gate | Odhadovaná zložitosť |
|---|---|---|---|---|
| **1. P0** | Agent | Odblokovanie výstavby: vrátiť `active=False` pri hotových LC míľnikoch pre fall-through do ekonomiky | `lc_rush_candidates` vráti `False, [], ...`; watchdog zaradí budovu (Sklad/Baňu) | Nízka (5 riadkov kódu) |
| **2. P0** | Manual | Okamžité spustenie výskumu Ľahkej jazdy a Špeha v Kováčskej dielni v prehliadači | V hernom prehľade je výskum LK aktívny alebo dokončený (`light.available == true`) | Nízka (1 minúta manuálne) |
| **3. P0** | Manual | Spotrebovanie dreva stavbou/výskumom a následné vyzdvihnutie 3 questových odmien | Odmeny v `quests.rewards` sú prevzaté; zásoby dreva nepresiahnu kapacitu skladu | Nízka (2 minúty manuálne) |
| **4. P1** | Agent | Doplnenie allowlistu budov o `garage` a `snob` v [`agy_watchdog.py` L0024](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L24) | `self.assertIn("garage", ALLOWED)` a `self.assertIn("snob", ALLOWED)` v teste | Triviálna (1 riadok) |
| **5. P1** | Agent | Zvýšenie úrovne Skladu na 9–10 a Ťažby hliny na 11 v rastovom modeli | Snapshot potvrdí zaradenie Skladu do frontu pri zaplnení nad 80 % kapacity | Nízka |
| **6. P1** | Manual/Agent | Nastavenie a spustenie náboru Ľahkej jazdy (kontrolované zrušenie dry_run pre recruit) | V `recruit_queue` sa objaví nábor LK; `units.light.total > 0` | Stredná |
| **7. P1** | Agent | Skrátenie HTTP timeoutu z 180s na 25s s riadnym handlovaním chýb | Simulácia timeoutu v teste nezablokuje watchdog na viac než 25s | Nízka (2 konštanty) |
| **8. P2** | Agent | Oprava lokalizácie quest claimera (odstránenie "Prevziať" namiesto "Reivindicar") | Test overí správne vyčistenie labelu s textom "Prevziať všetko" v `quests.py` | Nízka (regex úprava) |
| **9. P2** | Manual/Agent | Odomknutie druhej úrovne zberu (Scavenge Tier 2) po naverbovaní prvých jazdcov | `scavenge.free_options` obsahuje `[1, 2]` | Nízka |
| **10. P2** | Manual | Vstup do stabilného lokálneho kmeňa pred koncom začiatočníckej ochrany | Snapshot hráča obsahuje platné `ally_id != null` | Manuálna diplomacia |

---

## Next 24h measurable program (low risk only)

- **Hodina 0–1 (Okamžitá stabilizácia):**
  1. *Manuálny krok:* V hre zadať v Kováčskej dielni výskum Špeha a Ľahkej jazdy.
  2. *Manuálny krok:* Spustiť stavbu Skladu na úroveň 9 (spotrebuje sa 393 dreva, 338 hliny, 231 železa).
  3. *Manuálny krok:* Vyzdvihnúť questové odmeny (158760, 159982, 176963), čím sa získa 450 dreva, 450 hliny, 300 železa bez pretečenia skladu.
- **Hodina 1–3 (Kódový fix):**
  1. Upraviť [`sk106_lc_rush_policy.py` L0026](file:///home/worker/tribal-sk106-lab/sk106_lc_rush_policy.py#L26): ak sú míľniky hotové, vrátiť `(False, [], "LC_MILESTONES_COMPLETED")`, aby riadenie prevzal `sk106_growth_engine`.
  2. Pridať `garage` a `snob` do `ALLOWED` v [`agy_watchdog.py` L0024](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L24).
  3. Skrátiť `timeout=180` na `timeout=25` v [`agy_watchdog.py` L0048, L0056](file:///home/worker/tribal-sk106-lab/agy_watchdog.py#L48).
- **Hodina 3–8 (Výstavba a nábor):**
  1. Watchdog automaticky zaradí stavbu Hliny 11 a Železa 9.
  2. Po dokončení výskumu LK zadať nábor prvých 10–15 jednotiek Ľahkej jazdy.
- **Hodina 8–18 (Scavenge a suroviny):**
  1. Ponechať `sk106_auto_scavenge.py` posielať pešie jednotky (12 kopijí, 8 mečov) na zber s rezervou 2/2 doma.
  2. Odomknúť Scavenge Tier 2.
- **Hodina 18–24 (Vyhodnotenie a diplomacia):**
  1. Kontrola bodového rastu voči veroniccba cez `sk106_growth_scoreboard.py`.
  2. Odoslanie žiadosti o vstup do kmeňa s dobrou regionálnou kontrolou.

---

## Test coverage gaps & acceptance gates

### Identifikované medzery v testoch:
1. **Falošný pocit bezpečia v regresnom teste allowlistu:** V [`test_stable_allowlist_p0.py` L0041-L0044](file:///home/worker/tribal-sk106-lab/test_stable_allowlist_p0.py#L41-L44) test iba overí, že pri `level=3` vráti politika `"LC_MILESTONES_READY_CHECK_RESEARCH"`. Test vôbec netestuje integráciu s `agy_watchdog.py` — teda že táto hodnota spôsobí úplné zastavenie výstavby!
2. **Absencia testov správania pri plnom sklade:** Žiaden test neoveruje, či watchdog prioritizuje sklad pred baňami, keď zásoby presiahnu 80 % kapacity.
3. **Absencia testov quest parsera so slovenskými textami:** V [`test_sk106_locale.py` L0018-L0083](file:///home/worker/tribal-sk106-lab/test_sk106_locale.py#L18-L83) sú testované len správy a ochrana, chýba testovanie HTML parsera questov s textom "Prevziať" / "Prevziať všetko".

### Akceptačné brány (Acceptance Gates):
- **Gate 1 (Watchdog Non-Blocking):** Pri stave `stable: 3` a prázdnom rade musí funkcia `ranked_candidates` vrátiť neprázdny zoznam kandidátov, pokiaľ sú v dedine dostupné suroviny.
- **Gate 2 (Resource Overflow Prevention):** Ak stav ktorejkoľvek suroviny presiahne 80 % kapacity skladu, `storage` musí byť vyhodnotený ako priorita s najvyšším skóre.
- **Gate 3 (Idempotencia a no-retry):** Test simulujúci zlyhanie siete pri POST požiadavke musí potvrdiť, že nedôjde k okamžitému opakovanému odoslaniu akcie a stav zostane bezpečne chránený v `build_pending` minimálne 7 minút.

---

## Evidence, uncertainty and recommendations

### Pravidlá hry a autorizácia (Game Rules & Safety):
- Pravidlá Tribal Wars výslovne zakazujú používanie neautorizovaných botov a automatických skriptov generujúcich herné kliknutia. Architektúra projektu stavia na "advisory-first" princípe, pričom automatické POST akcie nesú inherentné riziko detekcie a zablokovania účtu.
- **Kategorické odporúčanie:** Nikdy neukladať heslá v plaintexte, neobchádzať mechanizmy CAPTCHA a neimplementovať vysokofrekvenčné útočné makrá na hráčske dediny.

### Neistota a limity zdrojových dát:
- **Dáta súperov:** Dáta o veroniccba pochádzajú výhradne z verejného mapového exportu (`latest_summary.json`). Informácie o jej budovách, technológiách či vojsku nie sú dostupné. Akékoľvek taktické rozhodnutia sa musia riadiť vlastným rozvojom, nie domnienkami o súperke.
- **Skrátený kód (Truncated Source):** Zdrojové súbory [`tribal_assistant/core/game/scraper/game.py`](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/game/scraper/game.py) a [`tribal_assistant/core/agents/guardrails.py`](file:///home/worker/tribal-sk106-lab/tribal_assistant/core/agents/guardrails.py) boli v zadaní poskytnuté skrátené (536 z 578 riadkov, resp. 323 z 341 riadkov). Závery týkajúce sa interných metód `_is_barbarian` a detailov parserov bojových správ preto označujeme so **strednou mierou istoty (low-to-medium confidence)**. Všetky závery týkajúce sa watchdogu, LC rush politiky a live stavu sú overené so **stopercentnou istotou (high confidence)**.
