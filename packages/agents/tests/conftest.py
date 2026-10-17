import json
from typing import Any

import pytest
from agent_framework import BaseChatClient, ChatResponse, Message

from footballfoundry.season import play_fixture
from footballfoundry.world import generate_fixtures, generate_league
from footballfoundry_agents.reference import Reference


@pytest.fixture(scope="session")
def league():
    return generate_league(42)


@pytest.fixture(scope="session")
def match(league):
    """M0001 of the seed-42 season."""
    return play_fixture(league, generate_fixtures(league)[0])


@pytest.fixture(scope="session")
def ref(league):
    return Reference(league)


@pytest.fixture
def scripted():
    return ScriptedClient


class ScriptedClient(BaseChatClient):
    """A chat client that answers by the requested structured-output type, so the real Agent Framework agents
    and workflow run without a model. `verdicts` scripts the fact-checker: one bool per call, then True."""

    def __init__(self, verdicts: list[bool] | None = None, fail: set[str] | None = None) -> None:
        super().__init__()
        self.verdicts = list(verdicts or [])
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []
        self.instructions: list[str] = []

    async def _respond(self, messages, options) -> ChatResponse:
        fmt = options.get("response_format")
        name = fmt.__name__ if isinstance(fmt, type) else "text"
        prompt = "\n".join(m.text or "" for m in messages)
        self.calls.append((name, prompt))
        self.instructions.append(str(options.get("instructions") or "") + "".join(
            m.text or "" for m in messages if m.role == "system"))
        if name in self.fail:
            raise RuntimeError(f"scripted failure for {name}")
        body: dict[str, Any]
        if name == "Analysis":
            revised = "FACT-CHECKER FOUND" in prompt
            body = {"headline": "Revised" if revised else "Draft", "explanation": "A flowing move.",
                    "why_it_matters": "It puts them ahead.", "key_stats": [{"label": "xG", "value": "0.27"}],
                    "player_ids": ["P2026"], "event_ids": ["M0001-00500"],
                    "visuals": [{"type": "xg_race", "caption": "They have been creating more all half."},
                                {"type": "compare", "caption": "Not offered for every moment, so it may be dropped."}]}
        elif name == "Verdict":
            ok = self.verdicts.pop(0) if self.verdicts else True
            body = {"ok": ok, "problems": [] if ok else ["The minute is wrong"]}
        elif name == "Narration":
            body = {"headline": "Vertooren strikes", "text": "Vertooren finishes a flowing move."}
        elif name == "Story":
            body = {"text": "They call him the Rocket.", "es": "Le llaman el Cohete.", "fr": "On l'appelle la Fusée.",
                    "pt": "Chamam-lhe o Foguete.", "de": "Sie nennen ihn die Rakete."}
        elif name == "Variant":
            body = {"headline": "¡Gol de Vertooren!", "text": "Vertooren marca."}
        else:
            body = {}
        return ChatResponse(messages=[Message("assistant", [json.dumps(body)])], response_format=fmt)

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        return self._respond(messages, options)
