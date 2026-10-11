import asyncio

from azure.ai.translation.text.models import TranslatedTextItem, TranslationResult, TranslationText
from azure.core.credentials import AzureKeyCredential

from footballfoundry_agents.service import AnalystService, TemplateWriter
from footballfoundry_agents.stores import memory_stores
from footballfoundry_agents.translation import AzureTranslator, protect_names, unprotect


def test_names_are_protected_and_restored():
    html = protect_names("Kenneth Ball & Ball score for Marrowgate <United>", ["Kenneth Ball", "Ball", "Marrowgate"])
    assert html.count('class="notranslate"') == 3
    assert '<span class="notranslate">Kenneth Ball</span>' in html  # longest name wins
    assert "&amp;" in html and "&lt;United&gt;" in html  # the rest is escaped, so it's valid HTML
    assert unprotect(html) == "Kenneth Ball & Ball score for Marrowgate <United>"


class FakeClient:
    """Stands in for TextTranslationClient: records requests, answers with the SDK's own result models."""

    def __init__(self, fail_llm=False):
        self.bodies, self.fail_llm = [], fail_llm

    async def translate(self, body):
        self.bodies.append(body.as_dict())
        target = body.inputs[0].targets[0]
        if self.fail_llm and target.deployment_name:
            raise RuntimeError("deployment not supported for translation")
        return TranslationResult(value=[TranslatedTextItem(translations=[TranslationText(
            language=target.language, text=f"[{target.language}] {item.text}")]) for item in body.inputs])

    async def close(self):
        pass


def translator(fail_llm=False, llm="gpt-6-luna"):
    t = AzureTranslator("https://x.cognitiveservices.azure.com", AzureKeyCredential("unused"), llm_deployment=llm)
    t.client = FakeClient(fail_llm)
    return t


def test_nmt_request_protects_names():
    t = translator(llm=None)
    out = asyncio.run(t.translate(["Ball scores!"], "es", protect=["Ball"], audience="broadcast"))
    item = t.client.bodies[0]["inputs"][0]
    assert item["textType"] == "html" and item["language"] == "en" and item["targets"][0]["language"] == "es"
    assert '<span class="notranslate">Ball</span>' in item["text"]
    assert out == ["[es] Ball scores!"]


def test_casual_fans_get_the_llm_with_an_informal_tone_and_examples():
    t = translator()
    asyncio.run(t.translate(["What a finish!"], "fr", protect=[], audience="casual"))
    target = t.client.bodies[0]["inputs"][0]["targets"][0]
    assert target["deploymentName"] == "gpt-6-luna" and target["tone"] == "informal"
    assert len(target["referenceTextPairs"]) == 2


def test_llm_failure_falls_back_to_nmt():
    t = translator(fail_llm=True)
    out = asyncio.run(t.translate(["What a finish!"], "de", protect=[], audience="casual"))
    assert out == ["[de] What a finish!"] and len(t.client.bodies) == 2


class FailingTranslator:
    async def translate(self, texts, to, *, protect, audience):
        raise ConnectionError("translator unavailable")

    async def close(self):
        pass


def service_with(match, ref, tr):
    service = AnalystService(memory_stores(), TemplateWriter(ref), ref, translator=tr)

    async def go():
        for e in match.events:
            await service.ingest(e)
        await service.drain()
        goal = next(i for i in await service.stores.insights.list("M0001") if i.kind == "goal")
        return goal, await service.variant(goal, "analyst", "es")

    return asyncio.run(go())


def test_service_translates_the_english_variant(match, ref):
    goal, v = service_with(match, ref, translator(llm=None))
    english = goal.variants["analyst|en||"]
    assert v.translated_by == "translator" and v.language == "es"
    assert v.text == f"[es] {english.text}"  # the fact-checkable English, translated


def test_translator_outage_falls_back(match, ref):
    _, v = service_with(match, ref, FailingTranslator())
    assert v.translated_by is None and v.language == "en"  # offline templates can't translate; nothing claimed
