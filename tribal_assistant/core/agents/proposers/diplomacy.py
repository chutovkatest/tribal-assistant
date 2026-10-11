"""Diplomacy: tribe search and applications, invites and the mentor, never with the other accounts run here."""

from datetime import timedelta
from typing import Any

from loguru import logger

from tribal_assistant.core.agents.coordination.proposal import Factors, Horizon, Proposal
from tribal_assistant.core.agents.coordination.view import CoordinationView
from tribal_assistant.core.agents.knobs import knob, knob_int
from tribal_assistant.core.agents.proposers.base import Proposer
from tribal_assistant.core.agents.sightings import SightingBook
from tribal_assistant.core.agents.social.ledger import SocialLedger, now
from tribal_assistant.core.agents.social.writer import SocialWriter
from tribal_assistant.core.game.diplomacy import Diplomacy

PENDING = "pendente"


def lead(view: CoordinationView) -> bool:
    """Account-wide steps run on one village only: the first one of the account."""
    return not view.siblings or view.ctx.id == min(s.id for s in view.siblings)


class DiplomacyProposer(Proposer):
    key = "diplomacy"
    title = "Diplomacia"
    observes = "convites, candidaturas e tribos ativas da região, ofertas de mentor"
    delivers = "entrada numa tribo forte e um mentor, sem tratar com as outras contas daqui"

    async def propose(self, view: CoordinationView) -> list[Proposal]:
        if view.dry_run or not lead(view):
            return []

        items: list[Proposal] = []
        for step in (self._tribe, self._mentor):
            try:
                items += await step(view)
            except Exception as exc:
                view.note_error = str(exc)
                logger.warning("Passo de diplomacia {} falhou: {}", step.__name__, exc)

        return items

    def _social(self, action: str, arguments: dict[str, Any], reason: str, benefit: str, impact: float) -> Proposal:
        return Proposal(
            self.key,
            action,
            {**arguments, "reason": reason[:60]},
            reason,
            benefit,
            factors=Factors(urgency=0.3, impact=impact, opportunity=0.6),
            horizon=Horizon.STRATEGIC,
            confidence=0.8,
        )

    @staticmethod
    async def track(ledger: SocialLedger, state: dict[str, Any], ally_id: str | None, wait_hours: float) -> list[dict[str, Any]]:
        """Move every open application forward from what the tribe screen shows; return the ones still open."""
        listed = {str(a.get("ally_id")) for a in state.get("applications", [])}
        # Fail closed on unrecognized/translated screens; do not close or resend applications.
        if not ally_id and (state.get("screen_recognized") is False or state.get("applications_visible") is False):
            return [a for a in await ledger.applications() if a.get("status") == PENDING]
        still: list[dict[str, Any]] = []
        for application in await ledger.applications():
            if application.get("status") != PENDING:
                continue

            key = str(application.get("ally_id"))
            if ally_id or state.get("in_tribe"):
                status = "aceita" if key == str(ally_id) else "encerrada"
            elif listed and key not in listed:
                status = "recusada"
            elif now() - application["first_seen"] >= timedelta(hours=wait_hours) and key not in listed:
                status = "sem resposta"
            else:
                still.append(application)
                continue

            await ledger.set_application(key, str(application.get("tag") or ""), status)
        return still

    async def _tribe(self, view: CoordinationView) -> list[Proposal]:
        if not await view.cooldown("tribe"):
            return []

        ledger = SocialLedger(view.session)
        player = view.ctx.player or {}
        state = await view.actions.diplomacy.tribes(view.ctx.game_id)
        still = await self.track(ledger, state, player.get("ally_id"), knob(view, "diplomacy.apply_wait_hours"))
        if state.get("screen_recognized") is False or (
            not state.get("applications_visible", True) and still
        ):
            logger.warning("Tribe application state unverified; skip diplomacy actions to avoid duplicates")
            return []
        if player.get("ally_id") or state.get("in_tribe"):
            return []

        avoid = await ledger.managed_allies()
        invites = [i for i in state.get("invites", []) if str(i.get("ally_id")) not in avoid]
        if invites:
            invite = invites[0]
            return [self._social("accept_tribe_invite", {"invite_id": str(invite["id"])}, f"convite da tribo {invite.get('tag', '')}", "proteção, apoio e comércio da tribo", 0.7)]

        if len(still) >= knob_int(view, "diplomacy.parallel_applications"):
            return []

        tried = {str(a.get("ally_id")) for a in await ledger.applications() if now() - a["first_seen"] < timedelta(hours=knob(view, "diplomacy.apply_retry_hours"))}
        candidates = {str(t["id"]): t for t in await ledger.nearby_tribes(view.knobs)}
        for tribe in state.get("nearby", []):
            candidates[str(tribe["id"])] = {**candidates.get(str(tribe["id"]), {}), **tribe, "id": str(tribe["id"])}

        seen = await SightingBook(view.session).tribes()
        for ally_id, signals in seen.items():
            if ally_id in candidates and signals.get("closed"):
                avoid = avoid | {ally_id}
            elif ally_id in candidates and signals.get("recruiting"):
                candidates[ally_id]["score"] = candidates[ally_id].get("score", 0) + 1

        best = Diplomacy.best_tribe(list(candidates.values()), tried | avoid | {str(a.get("ally_id")) for a in still}, knob_int(view, "diplomacy.min_members"))
        if best is None:
            return []

        facts = await ledger.facts(player, len(view.siblings) or 1, view.role.value)
        written = await SocialWriter().application(facts, {k: best.get(k) for k in ("tag", "members", "points", "rank")})
        if written.text is None:
            logger.info("Candidatura à tribo {} adiada: {}", best.get("tag"), written.skipped)
            return []

        return [
            self._social(
                "apply_to_tribe",
                {"ally_id": str(best["id"]), "tag": str(best.get("tag") or ""), "text": written.text},
                f"tribo ativa da região: {best.get('tag')}",
                f"{best.get('members')} membros, {best.get('points')} pontos",
                0.6,
            )
        ]

    async def _mentor(self, view: CoordinationView) -> list[Proposal]:
        if not await view.cooldown("mentor"):
            return []

        mentors = await view.actions.diplomacy.mentors(view.ctx.game_id)
        best = Diplomacy.best_mentor(mentors, await SocialLedger(view.session).managed())
        if best is None:
            return []

        return [self._social("accept_mentor", {"mentor_id": str(best["id"]), "name": str(best.get("name") or "")}, f"mentor recomendado: {best['name']}", "ajuda de jogador experiente e a conquista Graduado", 0.5)]
