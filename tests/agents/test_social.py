from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from tests.agents.builders import context
from tribal_assistant.core.agents.knobs import Knobs, Tuner
from tribal_assistant.core.agents.learning import LessonBook
from tribal_assistant.core.agents.proposers.diplomacy import DiplomacyProposer
from tribal_assistant.core.agents.proposers.social import SocialProposer
from tribal_assistant.core.agents.roles.operator import OperatorAgent
from tribal_assistant.core.agents.social.guard import SocialGuard
from tribal_assistant.core.agents.social.ledger import SocialLedger
from tribal_assistant.core.agents.social.rules import SocialRules
from tribal_assistant.core.agents.social.writer import SocialWriter, Written
from tribal_assistant.core.agents.toolbox import Toolbox
from tribal_assistant.core.ai.types import Reply
from tribal_assistant.core.game.result import ActionResult
from tribal_assistant.core.models.agent import AgentDecision
from tribal_assistant.core.models.player import Player
from tribal_assistant.core.schemas.agent_settings import AgentSettings

ME = {"name": "Jogador", "world": "br144", "points": 320, "rank": 900, "villages": 1, "incomings": 0}


class Chat:
    def __init__(self, text: str, seen: list[str]) -> None:
        self.text, self.seen = text, seen

    async def send(self, results=None):
        return Reply(text=self.text)


class Llm:
    def __init__(self, text: str) -> None:
        self.text, self.prompts = text, []

    def conversation(self, system, prompt, tools):
        self.prompts.append(prompt)
        return Chat(self.text, self.prompts)


class Factory:
    def __init__(self, llm) -> None:
        self.llm = llm

    def build(self):
        return self.llm


class FakeWriter:
    def __init__(self, written: Written) -> None:
        self.written, self.calls = written, []

    async def reply(self, facts, card, thread, intent, history):
        self.calls.append((card, thread, intent))
        return self.written


class FakeSocial:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def reply(self, village_id, mail_id, text):
        self.calls.append(("reply", mail_id, text))
        return ActionResult(True, "reply_mail", "resposta enviada")

    async def send(self, village_id, to, subject, text):
        self.calls.append(("send", to, subject, text))
        return ActionResult(True, "send_mail", "mensagem enviada")


class FakeActions:
    def __init__(self) -> None:
        self.social = FakeSocial()


async def own_account(session: AsyncSession, name: str = "Irmao") -> None:
    session.add(Player(account_id=2, game_id="99", name=name, world="br144"))
    await session.commit()


def view(player: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(ctx=SimpleNamespace(player=player or ME), knobs=Knobs())


def box(session: AsyncSession, actions: FakeActions, dry_run: bool = False) -> Toolbox:
    ctx = context()
    ctx.player = dict(ME)
    return Toolbox(agent=OperatorAgent(), ctx=ctx, session=session, config=AgentSettings(), run_id="t", dry_run=dry_run, actions=actions)


def test_intent_and_category_of_a_message() -> None:
    mail = {"id": "1", "sender": "Vizinho", "subject": "oi", "system": False}

    assert SocialRules.intent("Pode me mandar apoio? estou sendo atacado") == "support"
    assert SocialRules.intent("Topa um NAP entre nossas tribos?") == "diplomacy"
    assert SocialRules.intent("vou te nobrar amanhã") == "threat"
    assert SocialRules.intent("Compre pontos premium grátis em www.site.com") == "spam"
    assert SocialRules.classify(mail, "Compre já, promoção!", me="Jogador", managed=set(), card={}) == ("ignore", "propaganda ou spam")
    assert SocialRules.classify({**mail, "sender": "Equipe Tribal Wars", "system": True}, "", me="Jogador", managed=set(), card={})[0] == "ignore"
    assert SocialRules.classify({**mail, "sender": "irmao"}, "oi", me="Jogador", managed={"Irmao"}, card={}) == ("ignore", "conta administrada por este app")
    assert SocialRules.classify(mail, "tudo bem?", me="Jogador", managed=set(), card={"leader": True})[0] == "leader"
    assert SocialRules.classify(mail, "me ajuda com tropa na defesa", me="Jogador", managed=set(), card={})[0] == "support"


def test_pending_only_when_the_last_word_is_theirs() -> None:
    assert SocialRules.pending([{"author": "Jogador", "text": "oi"}, {"author": "Outro", "text": "e aí"}], "Jogador")
    assert not SocialRules.pending([{"author": "Outro", "text": "e aí"}, {"author": "jogador", "text": "tudo certo"}], "Jogador")
    assert not SocialRules.pending([], "Jogador")


def test_texts_that_never_go_out() -> None:
    facts = '{"pontos": 320, "aldeias": 1}'

    assert SocialRules.refusal("Vou mandar apoio para você agora.", facts) == "texto promete tropas ou recursos"
    assert SocialRules.refusal("Não consigo mandar apoio para outros jogadores, desculpe.", facts) is None
    assert SocialRules.refusal("Relaxa, não sou um bot.", facts) == "texto afirma ser humano"
    assert SocialRules.refusal("Me chama em www.site.com", facts) == "texto com link ou e-mail"
    assert SocialRules.refusal("Tenho 5000 pontos já.", facts).startswith("texto com número fora dos fatos")
    assert SocialRules.refusal("Tenho 320 pontos e 1 aldeia.", facts) is None
    assert SocialRules.refusal("Claro, aceito sitter sim.", facts) == "texto aceita sitter ou conta compartilhada"
    assert SocialRules.refusal("Não aceito sitter nem conta compartilhada.", facts) is None
    assert SocialRules.skipped("IGNORAR: só um cumprimento automático") == "só um cumprimento automático"
    assert SocialRules.recipients("a; b") == ["a", "b"]


async def test_writer_uses_the_model_and_refuses_without_it() -> None:
    thread = {"subject": "Oi", "messages": [{"author": "Vizinho", "date": "hoje", "text": "Você é bot?"}]}
    llm = Llm("Jogo por aqui com calma, focado em crescer a aldeia.")

    written = await SocialWriter(Factory(llm)).reply({"meu_nome": "Jogador"}, {"nome": "Vizinho"}, thread, "question", 12)
    assert written.text == "Jogo por aqui com calma, focado em crescer a aldeia."
    assert "Você é bot?" in llm.prompts[0] and '"asks_bot": true' in llm.prompts[0] and "limites" in llm.prompts[0]

    assert (await SocialWriter(Factory(None)).reply({}, {}, thread, "question", 12)).skipped == "IA indisponível"
    assert (await SocialWriter(Factory(Llm("IGNORAR: nada a dizer"))).reply({}, {}, thread, "chat", 12)).skipped == "nada a dizer"
    assert (await SocialWriter(Factory(Llm("Tenho 9999 tropas prontas."))).reply({}, {}, thread, "chat", 12)).skipped.startswith("descartado")


async def test_intro_brings_subject_and_text() -> None:
    llm = Llm("ASSUNTO: Olá, vizinho\nTEXTO: Oi! Estou começando aqui perto e quero manter boa vizinhança.")

    written = await SocialWriter(Factory(llm)).intro({"meu_nome": "Jogador"}, {"nome": "Vizinho"}, "vizinho ativo")
    assert (written.subject, written.text) == ("Olá, vizinho", "Oi! Estou começando aqui perto e quero manter boa vizinhança.")


async def test_guard_blocks_own_accounts_bulk_and_limits(session: AsyncSession) -> None:
    await own_account(session)
    guard = SocialGuard(session, Knobs({"social.messages_per_hour": 2, "social.first_contacts_per_day": 1}))

    assert await guard.check("Irmao", "oi", "Jogador", first=False) == "destinatário é uma conta administrada por este app"
    assert await guard.check("jogador", "oi", "Jogador", first=False) == "destinatário é a própria conta"
    assert await guard.check("A; B", "oi", "Jogador", first=False) == "cada mensagem vai para um único jogador, nunca em massa"
    assert await guard.check("Outro", "oi", "Jogador", first=True) is None

    await guard.ledger.record_sent("Outro", "intro_neighbour")
    assert await guard.check("Outro", "oi de novo", "Jogador", first=True) == "jogador já contatado e ainda sem resposta"
    assert await guard.check("Terceiro", "oi", "Jogador", first=True) == "limite de 1 primeiros contatos por dia"

    now = datetime.now(UTC).replace(tzinfo=None)
    for _ in range(2):
        session.add(AgentDecision(village_id=None, run_id="r", agent="social", action="reply_mail", ok=True, dry_run=False, created_at=now, updated_at=now))
    await session.commit()
    assert await guard.check("Outro", "oi", "Jogador", first=False) == "limite de 2 mensagens por hora"


async def test_proposer_ignores_own_accounts_and_answers_players(session: AsyncSession) -> None:
    await own_account(session)
    ledger = SocialLedger(session)
    writer = FakeWriter(Written(text="Oi! Por enquanto sigo sem tribo, obrigado pelo convite."))
    proposer = SocialProposer(writer)
    thread = {"messages": [{"author": "Recrutador", "author_id": "7", "date": "hoje", "text": "Quer entrar na nossa tribo?"}]}

    managed = await ledger.managed()
    own = {"id": "1", "subject": "oi", "sender": "Irmao", "system": False, "unread": True}
    assert await proposer.answer(view(), ledger, own, thread, {}, "Jogador", managed, {}) is None
    assert (await ledger.thread("1"))["decision"]["why"] == "conta administrada por este app"
    assert writer.calls == []

    mail = {"id": "2", "subject": "Convite", "sender": "Recrutador", "sender_id": "7", "system": False, "unread": True}
    proposal = await proposer.answer(view(), ledger, mail, thread, {}, "Jogador", managed, {})
    assert proposal.action == "reply_mail" and proposal.arguments["mail_id"] == "2"
    assert proposal.arguments["text"].startswith("Oi!")
    stored = await ledger.thread("2")
    assert stored["intent"] == "invite" and stored["messages"][0]["text"] == "Quer entrar na nossa tribo?"
    assert (await ledger.contact("Recrutador"))["heard"] == 1


async def test_proposer_keeps_the_thread_pending_without_ai(session: AsyncSession) -> None:
    ledger = SocialLedger(session)
    proposer = SocialProposer(FakeWriter(Written(skipped="IA indisponível")))
    mail = {"id": "3", "subject": "Oi", "sender": "Vizinho", "system": False, "unread": True}
    thread = {"messages": [{"author": "Vizinho", "text": "tudo bem?"}]}

    assert await proposer.answer(view(), ledger, mail, thread, {}, "Jogador", set(), {}) is None
    decision = (await ledger.thread("3"))["decision"]
    assert (decision["what"], decision["why"]) == ("pendente", "IA indisponível")
    assert SocialProposer.worth_opening({"unread": False}, await ledger.thread("3"))


async def test_reply_tool_goes_through_the_guard(session: AsyncSession) -> None:
    await own_account(session)
    ledger = SocialLedger(session)
    await ledger.save_thread({"id": "5", "subject": "oi", "sender": "Irmao", "system": False}, {"messages": []}, {})
    await ledger.save_thread({"id": "6", "subject": "oi", "sender": "Vizinho", "sender_id": "8", "system": False}, {"messages": []}, {})
    actions = FakeActions()
    toolbox = box(session, actions)

    refused = await toolbox.invoke("reply_mail", {"mail_id": "5", "text": "oi, tudo bem?", "reason": "t"})
    assert not refused.ok and "administrada" in refused.text

    promise = await toolbox.invoke("reply_mail", {"mail_id": "6", "text": "Vou mandar tropas agora.", "reason": "t"})
    assert not promise.ok and "promete" in promise.text

    sent = await toolbox.invoke("reply_mail", {"mail_id": "6", "text": "Oi! Tudo certo por aqui.", "reason": "t"})
    assert sent.ok and actions.social.calls == [("reply", "6", "Oi! Tudo certo por aqui.")]
    assert (await ledger.thread("6"))["decision"]["what"] == "respondida"
    assert (await ledger.contact("Vizinho"))["sent"] == 1


async def test_send_tool_never_repeats_a_silent_contact(session: AsyncSession) -> None:
    actions = FakeActions()
    toolbox = box(session, actions)
    args = {"to": "Vizinho", "subject": "Olá", "text": "Oi, sou seu vizinho.", "kind": "intro_neighbour", "reason": "t"}

    assert (await toolbox.invoke("send_mail", args)).ok
    again = await toolbox.invoke("send_mail", args)
    assert not again.ok and "já contatado" in again.text
    assert len(actions.social.calls) == 1


async def test_application_needs_a_written_text(session: AsyncSession) -> None:
    toolbox = box(session, FakeActions())

    outcome = await toolbox.invoke("apply_to_tribe", {"ally_id": "239", "tag": "LARGA3", "reason": "t"})
    assert not outcome.ok and outcome.text.startswith("RECUSADO: candidatura sem texto")


async def test_application_text_is_not_written_without_ai() -> None:
    written = await SocialWriter(Factory(None)).application({"meu_nome": "Jogador"}, {"tag": "LARGA3"})
    assert written.text is None and written.skipped == "IA indisponível"


async def test_applications_move_from_pending_to_refused_or_silent(session: AsyncSession) -> None:
    ledger = SocialLedger(session)
    await ledger.set_application("239", "LARGA3", "pendente")
    await ledger.set_application("1165", "MINA3", "pendente")

    still = await DiplomacyProposer.track(ledger, {"in_tribe": False, "applications": [{"ally_id": "1165"}]}, None, 48)
    statuses = {a["ally_id"]: a["status"] for a in await ledger.applications()}
    assert [a["ally_id"] for a in still] == ["1165"] and statuses == {"239": "recusada", "1165": "pendente"}

    assert await DiplomacyProposer.track(ledger, {"in_tribe": False, "applications": []}, None, 0) == []
    assert {a["ally_id"]: a["status"] for a in await ledger.applications()}["1165"] == "sem resposta"


async def test_joining_closes_every_open_application(session: AsyncSession) -> None:
    ledger = SocialLedger(session)
    await ledger.set_application("239", "LARGA3", "pendente")
    await ledger.set_application("1165", "MINA3", "pendente")

    await DiplomacyProposer.track(ledger, {"in_tribe": True}, "1165", 48)
    assert {a["ally_id"]: a["status"] for a in await ledger.applications()} == {"239": "encerrada", "1165": "aceita"}


def test_hourly_cap_hits_become_a_metric() -> None:
    metrics = Tuner.measure([], [("reply_mail", False, "RECUSADO: limite de 3 mensagens por hora"), ("send_mail", True, "mensagem enviada")])
    assert metrics.mail_capped == 0.5


async def test_synced_mail_keeps_sender_and_thread(session: AsyncSession) -> None:
    mail = {"id": "9", "subject": "Oi", "sender": "Vizinho", "sender_id": "4", "unread": True, "messages": [{"author": "Vizinho", "text": "oi"}]}
    await LessonBook(session).texts([("mail:9", "mail", "Oi", "Vizinho (-): oi", mail), ("report:1", "report", "r", "texto")])

    stored = await SocialLedger(session).thread("9")
    assert stored["sender"] == "Vizinho" and stored["messages"][0]["text"] == "oi"


async def test_unknown_locale_never_closes_pending_application(session: AsyncSession) -> None:
    ledger = SocialLedger(session)
    await ledger.set_application("32", "ROD", "pendente")
    still = await DiplomacyProposer.track(
        ledger, {"in_tribe": False, "screen_recognized": False,
                 "applications_visible": False, "applications": []}, None, 0
    )
    assert [a["ally_id"] for a in still] == ["32"]
    assert (await ledger.applications())[0]["status"] == "pendente"
