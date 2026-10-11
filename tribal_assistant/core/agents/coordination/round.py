"""One coordinated round on one village: proposals from every specialist, one decision, one executor."""

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from tribal_assistant.core.agents.context import VillageContext
from tribal_assistant.core.agents.coordination.coordinator import Coordinator, Decision
from tribal_assistant.core.agents.coordination.insight import Certainty, Insight, now
from tribal_assistant.core.agents.coordination.live import FlowFeed
from tribal_assistant.core.agents.coordination.outcomes import Outcomes
from tribal_assistant.core.agents.coordination.policy import Policy
from tribal_assistant.core.agents.coordination.proposal import Proposal
from tribal_assistant.core.agents.coordination.roles import RoleSelector
from tribal_assistant.core.agents.coordination.view import CoordinationView
from tribal_assistant.core.agents.knobs import Knobs, KnobStore
from tribal_assistant.core.agents.proposers.attack import AttackProposer
from tribal_assistant.core.agents.proposers.base import Proposer
from tribal_assistant.core.agents.proposers.conquest import ConquestProposer
from tribal_assistant.core.agents.proposers.defense import DefenseProposer
from tribal_assistant.core.agents.proposers.diplomacy import DiplomacyProposer
from tribal_assistant.core.agents.proposers.economy import EconomyProposer
from tribal_assistant.core.agents.proposers.expansion import ExpansionProposer
from tribal_assistant.core.agents.proposers.infrastructure import InfrastructureProposer
from tribal_assistant.core.agents.proposers.intelligence import IntelligenceProposer
from tribal_assistant.core.agents.proposers.logistics import LogisticsProposer
from tribal_assistant.core.agents.proposers.recruitment import RecruitmentProposer
from tribal_assistant.core.agents.proposers.social import SocialProposer
from tribal_assistant.core.agents.proposers.upkeep import UpkeepProposer
from tribal_assistant.core.agents.roles.base import VillageAgent
from tribal_assistant.core.agents.toolbox import Toolbox
from tribal_assistant.core.game.actions import GameActions
from tribal_assistant.core.repositories.agents import AgentRepository
from tribal_assistant.core.repositories.coordination import CoordinationRepository
from tribal_assistant.core.schemas.agent_settings import AgentSettings

if TYPE_CHECKING:
    from tribal_assistant.core.agents.trace import RunTrace


class ProposerAgent(VillageAgent):
    """Identity used in the decision log when the coordinator executes a proposer's action."""

    def __init__(self, key: str, title: str) -> None:
        self.key = key
        self.title = title
        self.mission = ""
        self.tools = tuple(tool.name for tool in Toolbox.CATALOG)
        self.buildings = ()

    async def rules(self, box: "Toolbox") -> str:
        return ""


class VillageRound:
    PROPOSERS: tuple[type[Proposer], ...] = (
        DefenseProposer,
        EconomyProposer,
        InfrastructureProposer,
        RecruitmentProposer,
        AttackProposer,
        ExpansionProposer,
        ConquestProposer,
        LogisticsProposer,
        IntelligenceProposer,
        UpkeepProposer,
        DiplomacyProposer,
        SocialProposer,
    )

    def __init__(
        self,
        session: AsyncSession,
        config: AgentSettings,
        run_id: str,
        dry_run: bool,
        actions: GameActions,
        trace: "RunTrace | None" = None,
    ) -> None:
        self.session = session
        self.config = config
        self.run_id = run_id
        self.dry_run = dry_run
        self.actions = actions
        self.trace = trace
        self.proposers = [proposer() for proposer in self.PROPOSERS]
        self.acted = False

    def _box(self, ctx: VillageContext, key: str, title: str) -> Toolbox:
        box = Toolbox(
            agent=ProposerAgent(key, title),
            ctx=ctx,
            session=self.session,
            config=self.config,
            run_id=self.run_id,
            dry_run=self.dry_run,
            actions=self.actions,
            trace=self.trace,
        )
        box.brain_name = "coordinator"
        return box

    async def run(self, ctx: VillageContext, siblings: list[VillageContext] | None = None) -> tuple[Decision, list[Insight]]:
        knobs = await KnobStore(self.session).load()
        base, mode, reason = await RoleSelector(self.session, knobs).select(ctx, siblings)
        ctx.policy = Policy.for_role(mode.value, knobs)
        view = CoordinationView(ctx, self.session, self.config, self.actions, self.dry_run, mode, base, self._box(ctx, "coordinator", "Coordenador"))
        view.knobs = knobs
        view.siblings = list(siblings or [])
        view.recent = await self._recent(ctx, knobs.int("coordinator.recent_minutes"))
        view.note(Insight("policy", f"limites do modo {mode.value}: {ctx.policy.describe()}", Certainty.FACT, now(), 1.0, None, "coordenador"))
        view.note(Insight("role", f"papel {base.value}: {reason}", Certainty.FACT, now(), 1.0, base.value, "coordenador"))

        proposals: list[Proposal] = []
        constraints, reservations = [], []
        titles = {p.key: p.title for p in self.proposers}
        feed = FlowFeed(ctx)
        feed.start(mode.value, base.value, [{"key": p.key, "title": p.title} for p in self.proposers])

        for proposer in self.proposers:
            try:
                constraints += await proposer.constraints(view)
                reservations += await proposer.reservations(view)
                proposals += await proposer.propose(view)
            except Exception as exc:
                logger.exception("Proponente {} falhou", proposer.key)
                view.note(Insight(f"error:{proposer.key}", f"{proposer.title} falhou: {exc}", Certainty.FACT, now(), 1.0, None, proposer.key))

        # Do not feed construction proposals to the internal coordinator:
        # the external watchdog is the single construction planner/writer.
        proposals = [p for p in proposals if p.action != "upgrade_building"]
        await self._trace(
            "plan",
            f"{len(proposals)} proposta(s), {len(constraints)} veto(s), {len(reservations)} reserva(s) no modo {mode.value}",
        )

        async def execute(proposal: Proposal) -> tuple[bool, str]:
            box = self._box(ctx, proposal.source, titles.get(proposal.source, proposal.source))
            outcome = await box.invoke(proposal.action, proposal.arguments)
            self.acted = self.acted or box.acted
            return outcome.ok, outcome.text

        before = view.estimator.insights()
        decision = await Coordinator(view).run(proposals, constraints, reservations, execute)
        insights = before + view.insights
        data = decision.to_dict(insights)
        await self._streak(ctx, data, knobs)
        decision.learned = data["learned"]

        await CoordinationRepository(self.session).save_round(
            self.run_id,
            ctx.id,
            decision.role.value,
            decision.mode.value,
            decision.goal,
            decision.next_review_at,
            data,
        )
        await self._trace("summary", decision.summary())
        failed = sum(1 for e in decision.executed if not e.get("ok"))
        feed.done(decision.summary(), len(decision.executed) - failed, failed, len(decision.deferred), decision.next_review_at)
        return decision, insights

    async def _streak(self, ctx: VillageContext, data: dict[str, Any], knobs: Knobs) -> None:
        """How many rounds in a row this village executed the same actions, for the panel and the tuner."""
        limit = knobs.int("coordinator.repeat_rounds")
        rows = await CoordinationRepository(self.session).history(ctx.id, limit=limit * 4)
        history = [data, *(json.loads(row.data) if isinstance(row.data, str) else (row.data or {}) for row in rows)]
        streak = Outcomes.streak(history)
        data["learned"] = {**data.get("learned", {}), "streak": streak, "repeated": streak >= limit}

    async def _recent(self, ctx: VillageContext, minutes: int = 10) -> set[str]:
        keys = set()
        since = now() - timedelta(minutes=minutes)
        for decision in await AgentRepository(self.session).decisions(village_id=ctx.id, limit=30):
            if decision.created_at < since or not decision.ok or decision.dry_run or decision.run_id == self.run_id:
                continue

            args = json.loads(decision.arguments) if isinstance(decision.arguments, str) else decision.arguments
            keys.add(Proposal(decision.agent, decision.action, args or {}, "").key)

        return keys

    async def _trace(self, kind: str, content: str) -> None:
        if self.trace is not None:
            await self.trace.step(kind, content)

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        return [{"key": p.key, "title": p.title, "observes": p.observes, "delivers": p.delivers} for p in (c() for c in VillageRound.PROPOSERS)]
