"""The multi-agent workflow, built on Microsoft Agent Framework.

    Brief ─► router ─┬─► analyst ──────┐
                     └─► recap writer ─┤
                                       ▼
                                  fact-checker ──(problems, attempts left)──► back to the author
                                       │
                                  (approved, or out of attempts: published unverified)
                                       ▼
                                   narrator ─► Insight

Each agent has one job and its own instructions. The analyst and recap writer explain, the fact-checker checks
every claim against the fact sheet and the MCP data tools, and the narrator writes broadcast copy from the
approved analysis only. The personaliser runs separately, on demand, per viewer (see `AgentWriter.personalize`).

If any step fails or the run times out, `AgentWriter.write` falls back to a template insight, so the overlay still
gets the moment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel, Field
from typing_extensions import Never

from footballfoundry_schema import Insight, InsightVariant

from footballfoundry_agents.briefs import Brief
from footballfoundry_agents.reference import Reference
from footballfoundry_agents.templates import make_insight, template_insight

log = logging.getLogger(__name__)

# -- structured outputs --------------------------------------------------------------------------------------


class Stat(BaseModel):
    label: str = Field(description="Short overlay label, e.g. 'xG' or 'Passes in move'")
    value: str = Field(description="Display value, e.g. '0.27' or '11'")


class Analysis(BaseModel):
    headline: str = Field(description="Working headline, at most 60 characters")
    explanation: str = Field(description="What happened and how, 2-3 sentences")
    why_it_matters: str = Field(description="Why this moment matters for the match, 1-2 sentences")
    key_stats: list[Stat] = Field(description="2-4 numbers worth putting on screen")
    player_ids: list[str] = Field(description="Players central to the moment, most important first")
    event_ids: list[str] = Field(description="Event ids the analysis relies on")


class Verdict(BaseModel):
    ok: bool = Field(description="True only if every factual claim is supported")
    problems: list[str] = Field(description="Each unsupported or wrong claim, with the correct fact if known")


class Narration(BaseModel):
    headline: str = Field(description="Lower-third headline, at most 60 characters")
    text: str = Field(description="Broadcast copy, 2-4 sentences, present tense")


class Variant(BaseModel):
    headline: str
    text: str


# -- instructions --------------------------------------------------------------------------------------------

GROUNDING = (
    "Use only facts from the fact sheet or from the footballfoundry tools. When you call a tool for this match, "
    "always pass as_of equal to the moment's event seq, so you never see what happens later. "
    "Refer to players and clubs by name; ids are for tools and for the event_ids/player_ids fields."
)

ANALYST = f"""You are the lead match analyst for a live football broadcast.
Explain why the moment matters, not just that it happened. Look at how it was created (the passing move,
progressive passes, pressure, set piece), the game state (score, minute, momentum, possession versus threat:
control or chaos), and the players' numbers today. Be specific and concrete; no clichés.
{GROUNDING}"""

RECAP = f"""You write the full-time recap for a football broadcast's studio segment.
Tell the story of the match in order: who controlled it, the turning points, and why the result happened.
Use the earlier insights listed in the fact sheet as the timeline of turning points.
{GROUNDING}"""

FACT_CHECKER = f"""You are the fact-checker for a live football broadcast. Check every factual claim in the
analysis: names, minutes, scores, numbers, who did what, and the cited event ids. Interpretation is fine when the
data supports it. Set ok to false and list each problem if anything is wrong or unsupported.
{GROUNDING}"""

NARRATOR = """You are the broadcast writer. Turn the approved analysis into on-screen copy: a headline of at most
60 characters for the lower third, and 2-4 sentences of present-tense commentary. Keep every fact exactly as given;
add nothing new."""

PERSONALIZER = """You adapt a football match insight for one viewer without changing any facts.
Audience: 'analyst' wants tactical vocabulary and numbers; 'casual' wants plain words, no jargon, and short
sentences; 'player' wants the story told around the focus player; 'broadcast' is neutral.
If a favourite club is given, frame it from that supporter's side, but stay honest about the facts.
Write the headline and text entirely in the requested language (keep player and club names as they are).
The headline stays at most 60 characters."""

# -- workflow messages ---------------------------------------------------------------------------------------


@dataclass
class Draft:
    brief: Brief
    analysis: Analysis
    attempt: int


@dataclass
class Revise:
    brief: Brief
    analysis: Analysis
    problems: list[str]
    attempt: int


@dataclass
class Approved:
    brief: Brief
    analysis: Analysis
    verified: bool
    problems: list[str] = field(default_factory=list)


async def _structured(agent: Agent, prompt: str, model: type[BaseModel]) -> Any:
    response = await agent.run(prompt, options={"response_format": model})
    value = response.value
    if value is None:
        raise ValueError(f"{agent.name} returned no {model.__name__}")
    return value


def _brief_prompt(brief: Brief) -> str:
    previous = "\n".join(f"  - {h}" for h in brief.previous) or "  (none yet)"
    return (f"{brief.text}\n\nEARLIER INSIGHTS THIS MATCH (don't repeat them):\n{previous}\n\n"
            f"The moment's event seq (as_of) is {brief.seq}.")


# -- executors -----------------------------------------------------------------------------------------------


class Router(Executor):
    @handler
    async def route(self, brief: Brief, ctx: WorkflowContext[Brief]) -> None:
        await ctx.send_message(brief)


class Author(Executor):
    """The analyst or the recap writer: writes an Analysis, and rewrites it when the fact-checker objects."""

    def __init__(self, agent: Agent, id: str) -> None:
        super().__init__(id=id)
        self.agent = agent

    @handler
    async def write(self, brief: Brief, ctx: WorkflowContext[Draft]) -> None:
        analysis = await _structured(self.agent, _brief_prompt(brief), Analysis)
        await ctx.send_message(Draft(brief, analysis, attempt=1))

    @handler
    async def revise(self, msg: Revise, ctx: WorkflowContext[Draft]) -> None:
        problems = "\n".join(f"  - {p}" for p in msg.problems)
        prompt = (f"{_brief_prompt(msg.brief)}\n\nYOUR PREVIOUS ANALYSIS:\n{msg.analysis.model_dump_json(indent=1)}"
                  "\n\nTHE FACT-CHECKER FOUND THESE PROBLEMS; fix them and remove anything you can't support:\n"
                  f"{problems}")
        analysis = await _structured(self.agent, prompt, Analysis)
        await ctx.send_message(Draft(msg.brief, analysis, attempt=msg.attempt + 1))


class FactChecker(Executor):
    def __init__(self, agent: Agent, max_attempts: int, id: str = "fact_checker") -> None:
        super().__init__(id=id)
        self.agent = agent
        self.max_attempts = max_attempts

    @handler
    async def check(self, draft: Draft, ctx: WorkflowContext[Revise | Approved]) -> None:
        prompt = (f"{_brief_prompt(draft.brief)}\n\nANALYSIS TO CHECK:\n{draft.analysis.model_dump_json(indent=1)}")
        verdict: Verdict = await _structured(self.agent, prompt, Verdict)
        if verdict.ok:
            await ctx.send_message(Approved(draft.brief, draft.analysis, verified=True))
        elif draft.attempt < self.max_attempts:
            await ctx.send_message(Revise(draft.brief, draft.analysis, verdict.problems, draft.attempt))
        else:
            log.warning("%s: publishing unverified after %d attempts: %s", draft.brief.insight_id, draft.attempt,
                        verdict.problems)
            await ctx.send_message(Approved(draft.brief, draft.analysis, verified=False, problems=verdict.problems))


class Narrator(Executor):
    def __init__(self, agent: Agent, id: str = "narrator") -> None:
        super().__init__(id=id)
        self.agent = agent

    @handler
    async def narrate(self, msg: Approved, ctx: WorkflowContext[Never, Insight]) -> None:
        a = msg.analysis
        prompt = (f"MOMENT: {msg.brief.kind} at {msg.brief.clock}\n\nAPPROVED ANALYSIS:\n{a.model_dump_json(indent=1)}")
        n: Narration = await _structured(self.agent, prompt, Narration)
        known = set(msg.brief.facts["players"]) | set(a.player_ids)
        insight = make_insight(
            msg.brief, headline=n.headline, text=n.text, why=a.why_it_matters,
            stats={s.label: s.value for s in a.key_stats[:4]}, verified=msg.verified,
            player_ids=[p for p in a.player_ids if p in known] or msg.brief.player_ids,
            event_ids=a.event_ids or msg.brief.event_ids,
        )
        await ctx.yield_output(insight)


# -- public interface ----------------------------------------------------------------------------------------


class AgentWriter:
    """Writes insights with the Foundry-hosted agents; falls back to templates on any failure."""

    def __init__(self, client: Any, ref: Reference, *, tools: Any = None, timeout_s: float = 90.0,
                 max_attempts: int = 2) -> None:
        self.ref = ref
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.analyst = Agent(client, ANALYST, name="analyst", tools=tools)
        self.recap = Agent(client, RECAP, name="recap_writer", tools=tools)
        self.checker = Agent(client, FACT_CHECKER, name="fact_checker", tools=tools)
        self.narrator = Agent(client, NARRATOR, name="narrator")
        self.personalizer = Agent(client, PERSONALIZER, name="personalizer")

    def build(self):
        # A fresh graph per run: several matches can be in flight at once. The agents themselves are shared.
        router = Router(id="router")
        analyst = Author(self.analyst, id="analyst")
        recap = Author(self.recap, id="recap_writer")
        checker = FactChecker(self.checker, self.max_attempts)
        narrator = Narrator(self.narrator)
        is_recap = lambda m: m.brief.kind == "full_time" if hasattr(m, "brief") else m.kind == "full_time"  # noqa: E731
        return (
            WorkflowBuilder(start_executor=router, name="footballfoundry-insight", max_iterations=20)
            .add_edge(router, analyst, condition=lambda b: not is_recap(b))
            .add_edge(router, recap, condition=is_recap)
            .add_edge(analyst, checker)
            .add_edge(recap, checker)
            .add_edge(checker, analyst, condition=lambda m: isinstance(m, Revise) and not is_recap(m))
            .add_edge(checker, recap, condition=lambda m: isinstance(m, Revise) and is_recap(m))
            .add_edge(checker, narrator, condition=lambda m: isinstance(m, Approved))
            .build()
        )

    async def write(self, brief: Brief) -> Insight:
        try:
            result = await asyncio.wait_for(self.build().run(brief), timeout=self.timeout_s)
            outputs = [o for o in result.get_outputs() if isinstance(o, Insight)]
            if outputs:
                return outputs[-1]
            log.warning("%s: workflow produced no insight, using template", brief.insight_id)
        except Exception:  # noqa: BLE001 - any agent/tool/network failure degrades to the template
            log.exception("%s: workflow failed, using template", brief.insight_id)
        return template_insight(brief, self.ref)

    async def personalize(self, insight: Insight, audience: str, language: str, club_id: str | None,
                          player_id: str | None) -> InsightVariant:
        viewer = [f"audience: {audience}", f"language: {language}"]
        if club_id:
            viewer.append(f"favourite club: {self.ref.club_name(club_id)}")
        if player_id:
            viewer.append(f"focus player: {self.ref.player_name(player_id)}")
        players = ", ".join(self.ref.player_name(p) for p in insight.player_ids) or "none"
        prompt = (f"VIEWER: {'; '.join(viewer)}\n\nINSIGHT ({insight.kind} at {insight.clock}):\n"
                  f"headline: {insight.headline}\ntext: {insight.text}\nwhy it matters: {insight.why_it_matters}\n"
                  f"numbers: {insight.stats}\nplayers involved: {players}")
        v: Variant = await asyncio.wait_for(_structured(self.personalizer, prompt, Variant), timeout=self.timeout_s)
        return InsightVariant(audience=audience, language=language, focus_club_id=club_id,
                              focus_player_id=player_id, headline=v.headline[:80], text=v.text)
