from pathlib import Path

from tribal_assistant.core.game.scraper.social import BuddiesParser, InboxParser, TribeParser

HTML = Path(__file__).resolve().parents[1] / "fixtures" / "html"

PLAYER_ROW = """<table class="vis"><tr><td colspan="2">
  <input type="checkbox" name="ids[700001]" class="check">
  <a href="/game.php?village=1&screen=mail&mode=view&view=700001&group_id=0">
    <img src="https://cdn/graphic/new_mail.webp" data-title="Novo"> Quer entrar na tribo?</a></td>
  <td class="nowrap"><a href="/game.php?village=1&screen=info_player&id=4242">Vizinho Bom</a></td>
  <td>set. 29, 10:15</td></tr></table>"""

THREAD = """<td id="content_value"><h2>Quer entrar na tribo?</h2>
  <div class="post"><div class="igmline"><span class="author"><a href="/game.php?screen=info_player&id=4242">Vizinho Bom</a></span>
  <span class="date">set. 29, 10:15</span></div><div class="text">Oi, vi que você está sem tribo. Quer entrar?</div></div>
  <div class="post"><div class="igmline"><span class="author"><a href="/game.php?screen=info_player&id=1">Jogador</a></span>
  <span class="date">set. 29, 11:00</span></div><div class="text">Oi! Me conta mais.</div></div>
  <form action="/game.php?screen=mail&mode=view&action=answer&view=700001" method="post"><textarea name="text"></textarea></form></td>"""

BUDDIES = """<td id="content_value"><table class="vis">
  <tr><td><a href="/game.php?screen=info_player&id=11">Amiga</a></td><td><a href="/game.php?screen=buddies&action=delete_buddy&buddy_id=5">remover</a></td></tr>
  <tr><td><a href="/game.php?screen=info_player&id=12">Pedinte</a></td><td><a href="/game.php?screen=buddies&action=accept_buddy&buddy_id=77">aceitar</a> <a href="/game.php?screen=buddies&action=reject_buddy&buddy_id=77">recusar</a></td></tr>
  <tr><td><a href="/game.php?screen=info_player&id=13">Esperando</a></td><td><a href="/game.php?screen=buddies&action=cancel_buddy&buddy_id=78">cancelar</a></td></tr>
</table></td>"""


def test_inbox_from_the_real_screen_holds_only_system_mails() -> None:
    mails = InboxParser.inbox((HTML / "mail.html").read_text())

    assert [m["id"] for m in mails] == ["613141", "610745"]
    assert mails[0]["subject"] == "App do Tribal Wars: Nunca perca um ataque!"
    assert mails[0]["sender"] == "Equipe Tribal Wars" and mails[0]["system"] and mails[0]["sender_id"] is None
    assert mails[0]["date"] == "set. 29, 01:00" and not mails[0]["unread"]


def test_inbox_row_from_a_player_is_unread_with_sender() -> None:
    mail = InboxParser.inbox(PLAYER_ROW)[0]

    assert (mail["id"], mail["sender"], mail["sender_id"], mail["unread"], mail["system"]) == ("700001", "Vizinho Bom", "4242", True, False)
    assert mail["subject"] == "Quer entrar na tribo?"


def test_thread_keeps_every_message_in_order() -> None:
    thread = InboxParser.thread(THREAD)

    assert [m["author"] for m in thread["messages"]] == ["Vizinho Bom", "Jogador"]
    assert thread["messages"][0]["author_id"] == "4242"
    assert thread["messages"][0]["text"].startswith("Oi, vi que você")
    assert thread["can_reply"]


def test_thread_without_posts_falls_back_to_the_page_text() -> None:
    thread = InboxParser.thread('<td id="content_value"><h2>Oi</h2><p>Texto solto</p></td>')

    assert len(thread["messages"]) == 1 and "Texto solto" in thread["messages"][0]["text"]


def test_buddies_screen_without_friends_still_offers_the_form() -> None:
    html = (HTML / "buddies.html").read_text()

    assert BuddiesParser.parse(html) == {"friends": [], "incoming": [], "outgoing": []}
    assert BuddiesParser.can_add(html)


def test_buddies_split_friends_requests_and_pending() -> None:
    found = BuddiesParser.parse(BUDDIES)

    assert [f["name"] for f in found["friends"]] == ["Amiga"]
    assert found["incoming"] == [{"name": "Pedinte", "player_id": "12", "id": "77"}]
    assert [o["name"] for o in found["outgoing"]] == ["Esperando"]


def test_tribe_screen_lists_nearby_tribes_without_invites() -> None:
    state = TribeParser.state((HTML / "tribe.html").read_text())

    assert not state["in_tribe"] and state["invites"] == [] and state["applications"] == []
    assert state["nearby"] == [
        {"id": "239", "tag": "LARGA3", "members": 31, "points": 33830},
        {"id": "1165", "tag": "MINA3", "members": 59, "points": 145518},
    ]


def test_tribe_pages_members_and_intro_thread() -> None:
    members = TribeParser.members(
        """<td id="content_value"><table class="vis"><tr><th>Nome</th><th>Pontos</th></tr>
        <tr><td><a href="?screen=info_player&id=1">Chefe</a> (Líder)</td><td>12.300</td></tr>
        <tr><td><a href="?screen=info_player&id=2">Soldado</a></td><td>800</td></tr></table></td>"""
    )
    threads = TribeParser.threads(
        """<a href="?screen=forum&screenmode=view_thread&forum_id=3&thread_id=9">Apresente-se aqui</a>
        <a href="?screen=forum&screenmode=view_thread&forum_id=3&thread_id=10">Regras</a>"""
    )

    assert members == [
        {"name": "Chefe", "player_id": "1", "points": 12300, "leader": True},
        {"name": "Soldado", "player_id": "2", "points": 800, "leader": False},
    ]
    assert [(t["id"], t["forum_id"], t["intro"]) for t in threads] == [("9", "3", True), ("10", "3", False)]
    assert TribeParser.forums('<a href="?screen=forum&screenmode=view_forum&forum_id=3">Geral</a>') == ["3"]


def test_applications_table_is_read_when_the_game_shows_it() -> None:
    html = """<table class="vis"><tr><th>Convites</th></tr></table>
    <table class="vis"><tr><th>Candidaturas</th></tr><tr><td><a href="?screen=info_ally&id=809">ELE4</a></td></tr></table>"""

    assert TribeParser.state(html)["applications"] == [{"ally_id": "809", "tag": "ELE4"}]


def test_slovak_tribe_screen_never_falsely_claims_membership() -> None:
    html = """<td id="content_value"><h2>Kmeň</h2>
    <form action="/game.php?screen=ally&action=create"><input name="name"></form>
    <table class="vis"><tr><th>Pozvánky</th></tr><tr><td>Žiadne pozvánky</td></tr></table>
    <table class="vis"><tr><th>Kmene v okolí</th></tr>
    <tr><td>ROD</td><td>13 členov</td><td>300 bodov</td>
    <td><a href="?screen=info_ally&mode=apply&id=32">Požiadať</a></td></tr></table>
    <table class="vis"><tr><th>Žiadosti o prijatie</th></tr>
    <tr><td><a href="?screen=info_ally&id=32">ROD</a></td></tr></table></td>"""
    state = TribeParser.state(html)
    assert state["screen_recognized"] and state["applications_visible"]
    assert not state["in_tribe"]
    assert state["applications"] == [{"ally_id": "32", "tag": "ROD"}]


def test_unknown_tribe_screen_fails_closed() -> None:
    state = TribeParser.state("<h2>Kmeň</h2><p>Neznáma obrazovka</p>")
    assert not state["screen_recognized"]
    assert not state["in_tribe"]
