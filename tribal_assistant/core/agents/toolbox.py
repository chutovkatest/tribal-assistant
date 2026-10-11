"""Binds one agent to one village: its tools, the guardrails, the game actions and the decision log."""

from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from tribal_assistant.core.agents.context import VillageContext
from tribal_assistant.core.agents.guardrails import Guardrails
from tribal_assistant.core.agents.knobs import tuning
from tribal_assistant.core.agents.learning import LessonBook
from tribal_assistant.core.agents.repair import ErrorRepair
from tribal_assistant.core.agents.tools.act import (
    AcceptMarketOffer,
    AcceptMentor,
    AcceptTribeInvite,
    ApplyToTribe,
    AssignFlag,
    CancelMarketOffer,
    ChooseRelic,
    ClaimQuestRewards,
    CollectEventPrizes,
    CompleteQuest,
    CraftEventItem,
    CreateMarketOffer,
    EquipRelic,
    LearnKnightSkill,
    OpenDailyBonus,
    ParkMarketOffer,
    RecruitKnight,
    RecruitUnits,
    RenameVillage,
    ResearchUnit,
    SendFarmAttack,
    SendResources,
    SendScavenge,
    SendSpy,
    SetProfileText,
    SetVillageGoal,
    SetVillagePlan,
    TrainKnight,
    UnlockScavenge,
    UpgradeBuilding,
    UseItem,
)
from tribal_assistant.core.agents.tools.base import AgentTool, ToolOutcome
from tribal_assistant.core.agents.tools.browse import BrowseGame
from tribal_assistant.core.agents.tools.conquest import SendNoble
from tribal_assistant.core.agents.tools.defense import GetIncoming, SimulateBattle
from tribal_assistant.core.agents.tools.farm import (
    ReadFarmAssistant,
    SendFarmTemplate,
    SetFarmTemplates,
)
from tribal_assistant.core.agents.tools.insight import GetForecast, GetOwnOffers, PlanScavenge
from tribal_assistant.core.agents.tools.intel import GetTargetIntel
from tribal_assistant.core.agents.tools.read import (
    GetQuests,
    GetVillageState,
    ListBarbarians,
    LookupKnowledge,
    ReadDoc,
    SearchDocs,
)
from tribal_assistant.core.agents.tools.social import (
    AcceptFriend,
    AddFriend,
    ReadInbox,
    ReadThread,
    ReadTribe,
    ReplyForum,
    ReplyMail,
    SendMail,
)
from tribal_assistant.core.ai.types import ToolCall, ToolResult, ToolSpec
from tribal_assistant.core.events import event_bus
from tribal_assistant.core.game.actions import GameActions
from tribal_assistant.core.game.narrator import Narrator
from tribal_assistant.core.repositories.agents import AgentRepository
from tribal_assistant.core.schemas.agent_settings import AgentSettings

if TYPE_CHECKING:
    from tribal_assistant.core.agents.roles.base import VillageAgent
    from tribal_assistant.core.agents.trace import RunTrace


class Toolbox:
    """Everything a VillageAgent may call for one village during one run."""

    CATALOG: tuple[type[AgentTool], ...] = (
        GetVillageState,
        GetQuests,
        LookupKnowledge,
        SearchDocs,
        ReadDoc,
        ListBarbarians,
        GetTargetIntel,
        ReadFarmAssistant,
        GetIncoming,
        SimulateBattle,
        GetForecast,
        PlanScavenge,
        GetOwnOffers,
        UpgradeBuilding,
        RecruitUnits,
        SendFarmAttack,
        SendFarmTemplate,
        SetFarmTemplates,
        SendSpy,
        SendScavenge,
        ClaimQuestRewards,
        OpenDailyBonus,
        CompleteQuest,
        RecruitKnight,
        UseItem,
        ChooseRelic,
        EquipRelic,
        RenameVillage,
        SetProfileText,
        AssignFlag,
        LearnKnightSkill,
        TrainKnight,
        CraftEventItem,
        CollectEventPrizes,
        ApplyToTribe,
        AcceptTribeInvite,
        AcceptMentor,
        ReadInbox,
        ReadThread,
        ReadTribe,
        ReplyMail,
        SendMail,
        AcceptFriend,
        AddFriend,
        ReplyForum,
        BrowseGame,
        AcceptMarketOffer,
        CreateMarketOffer,
        ParkMarketOffer,
        CancelMarketOffer,
        SendResources,
        SendNoble,
        ResearchUnit,
        SetVillageGoal,
        SetVillagePlan,
        UnlockScavenge,
    )

    def __init__(
        self,
        *,
        agent: "VillageAgent",
        ctx: VillageContext,
        session: AsyncSession,
        config: AgentSettings,
        run_id: str,
        dry_run: bool,
        actions: GameActions | None = None,
        trace: "RunTrace | None" = None,
    ) -> None:
        self.agent = agent
        self.ctx = ctx
        self.session = session
        self.run_id = run_id
        self.dry_run = dry_run
        self.actions = actions or GameActions()
        self.config = config
        self.guard = Guardrails(session, config)
        self.repo = AgentRepository(session)
        self.lessons = LessonBook(session)
        self.repair = ErrorRepair()
        self.acted = False
        self.trace = trace
        self.brain_name = "rules"

        catalog = {tool.name: tool() for tool in self.CATALOG}
        self.tools = {name: catalog[name] for name in agent.tools if name in catalog}

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self.tools.values()]

    async def call(self, call: ToolCall) -> ToolResult:
        outcome = await self.invoke(call.name, call.arguments)
        return ToolResult(call.id, outcome.text, is_error=not outcome.ok)

    async def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        # The external resource-aware watchdog is the sole autonomous building writer.
        # Direct operator commands still use the normal guardrails and game API.
        if name == "upgrade_building" and self.agent.key != "operator":
            outcome = ToolOutcome(False, "RECUSADO: build authority belongs to SK106 growth watchdog")
            await self._trace("tool_result", outcome.text, name, is_error=True)
            return outcome
        tool = self.tools.get(name)
        if tool is None:
            outcome = ToolOutcome(False, f"ferramenta {name!r} não disponível para {self.agent.title}")
            await self._trace("tool_result", outcome.text, name, is_error=True)
            return outcome

        outcome = await self._attempt(tool, arguments)

        if not outcome.ok and tool.acts and not self.dry_run:
            corrected = await self.repair.fix(tool, arguments, outcome.text, self.facts())
            if corrected is not None:
                await self._record(tool, arguments, outcome)
                await self._trace("thought", f"erro lido: {outcome.text[:160]} → nova tentativa com {AgentTool.dump(corrected)}", name)
                logger.info("[{}] {} corrige {} depois do erro: {}", self.ctx.village.coords, self.agent.key, name, outcome.text[:120])
                Narrator.repair(self.agent.key, name, outcome.text, corrected, self.ctx.id)
                arguments = corrected
                outcome = await self._attempt(tool, arguments)

        if tool.acts:
            await self._record(tool, arguments, outcome)

            if self.trace is not None:
                self.trace.count(outcome.ok, refused=outcome.text.startswith("RECUSADO"))

        return outcome

    async def _attempt(self, tool: AgentTool, arguments: dict[str, Any]) -> ToolOutcome:
        name = tool.name
        await self._trace("tool_call", AgentTool.dump(arguments), name)
        Narrator.tool(self.agent.key, name, arguments, self.ctx.id)

        blocked = await self.lessons.blocked(name, arguments, tuning(self.ctx)) if tool.acts and not self.dry_run else None

        try:
            outcome = ToolOutcome(False, blocked) if blocked else await tool.run(self, arguments)
        except (KeyError, TypeError, ValueError) as exc:
            outcome = ToolOutcome(False, f"argumentos inválidos: {exc}")
        except Exception as exc:
            logger.exception("Ferramenta {} falhou", name)
            outcome = ToolOutcome(False, f"falha: {exc}")

        await self._trace("tool_result", outcome.text, name, is_error=not outcome.ok)
        Narrator.result(self.agent.key, name, outcome.ok, outcome.text, self.ctx.id)
        return outcome

    def facts(self) -> dict[str, Any]:
        ctx = self.ctx
        return {
            "tropas_em_casa": {u.name: u.home for u in ctx.village.units if u.home},
            "recursos": ctx.stock,
            "população_livre": ctx.pop_free,
            "edifícios": ctx.levels,
        }

    async def _trace(self, kind: str, content: str, tool: str | None = None, *, is_error: bool = False) -> None:
        if self.trace is not None:
            await self.trace.step(kind, content, tool=tool, is_error=is_error)

    async def note(self, text: str) -> None:
        await self.repo.record(
            run_id=self.run_id,
            village_id=self.ctx.id,
            agent=self.agent.key,
            action="summary",
            arguments={},
            ok=True,
            dry_run=self.dry_run,
            reason="",
            result=text,
        )

    async def _record(self, tool: AgentTool, arguments: dict[str, Any], outcome: ToolOutcome) -> None:
        if outcome.ok and not self.dry_run and tool.name != "set_village_goal":
            self.acted = True

        await self.repo.record(
            run_id=self.run_id,
            village_id=self.ctx.id,
            agent=self.agent.key,
            action=tool.name,
            arguments={k: v for k, v in arguments.items() if k != "reason"} | outcome.data,
            ok=outcome.ok,
            dry_run=self.dry_run,
            reason=str(arguments.get("reason", "")),
            result=outcome.text,
        )

        if not self.dry_run:
            event_bus.publish("decision", {"action": tool.name, "ok": outcome.ok, "village_id": self.ctx.id})

            await self.lessons.action(self.agent.key, tool.name, arguments, outcome.ok, outcome.text)
            await self.lessons.notices(tool.name, list(outcome.data.get("notices", [])))

        logger.info(
            "[{}] {} {} → {}", self.ctx.village.coords, self.agent.key, tool.name, outcome.text
        )
