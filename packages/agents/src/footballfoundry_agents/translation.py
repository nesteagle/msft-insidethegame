"""Translate insights with Azure Translator (API 2026-06-06).

The personaliser agent rewrites an insight for a viewer in English, where it can still be fact-checked; Translator
then turns that English into the viewer's language. Standard neural translation (NMT) only translates, so it can't
add or change facts, and player and club names are marked "don't translate" so they come through exactly.

For casual fans and player focus, an LLM deployment (TRANSLATOR_LLM_DEPLOYMENT) can be used instead, with an
informal tone and a few reference translations of football commentary to set the style. If it fails, NMT is used.

Translator is reached through the Foundry (AI Services) account's endpoint with Microsoft Entra ID: no keys.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Protocol

log = logging.getLogger(__name__)

LLM_AUDIENCES = {"casual", "player"}

# A few lines of football commentary per language: the LLM path matches this style (adaptive custom translation).
REFERENCE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "es": [("He bends it into the top corner. What a finish!", "La coloca en la escuadra. ¡Qué definición!"),
           ("They've been camped in the opposition half for ten minutes.",
            "Llevan diez minutos instalados en campo rival.")],
    "fr": [("He bends it into the top corner. What a finish!", "Il l'enroule dans la lucarne. Quelle finition !"),
           ("They've been camped in the opposition half for ten minutes.",
            "Ils campent dans le camp adverse depuis dix minutes.")],
    "pt": [("He bends it into the top corner. What a finish!", "Ele coloca no ângulo. Que finalização!"),
           ("They've been camped in the opposition half for ten minutes.",
            "Estão há dez minutos instalados no campo adversário.")],
    "de": [("He bends it into the top corner. What a finish!", "Er zirkelt ihn in den Winkel. Was für ein Abschluss!"),
           ("They've been camped in the opposition half for ten minutes.",
            "Seit zehn Minuten haben sie sich in der gegnerischen Hälfte festgesetzt.")],
}


class Translator(Protocol):
    async def translate(self, texts: list[str], to: str, *, protect: list[str], audience: str) -> list[str]: ...

    async def close(self) -> None: ...


def protect_names(text: str, names: list[str]) -> str:
    """Plain text -> HTML with each name wrapped as not-to-translate (longest names first, so 'Kenneth Ball'
    wins over 'Ball')."""
    out = html.escape(text, quote=False)
    candidates = sorted({html.escape(n, quote=False) for n in names if n and len(n) > 2}, key=len, reverse=True)
    if not candidates:
        return out
    pattern = re.compile("|".join(re.escape(n) for n in candidates))
    return pattern.sub(lambda m: f'<span class="notranslate">{m.group(0)}</span>', out)


def unprotect(text: str) -> str:
    """Translated HTML -> plain text."""
    return html.unescape(re.sub(r"</?span[^>]*>", "", text))


class AzureTranslator:
    def __init__(self, endpoint: str, credential, *, llm_deployment: str | None = None) -> None:
        from azure.ai.translation.text.aio import TextTranslationClient

        self.client = TextTranslationClient(endpoint=endpoint, credential=credential)
        self.llm_deployment = llm_deployment or None

    async def translate(self, texts: list[str], to: str, *, protect: list[str], audience: str) -> list[str]:
        if self.llm_deployment and audience in LLM_AUDIENCES:
            try:
                return await self._llm(texts, to)
            except Exception:  # noqa: BLE001 - fall back to NMT, which is always available
                log.exception("LLM translation to %s failed; using NMT", to)
        return await self._nmt(texts, to, protect)

    async def _nmt(self, texts: list[str], to: str, protect: list[str]) -> list[str]:
        from azure.ai.translation.text.models import TranslateBody, TranslateInputItem, TranslationTarget

        body = TranslateBody(inputs=[
            TranslateInputItem(text=protect_names(t, protect), language="en", text_type="html",
                               targets=[TranslationTarget(language=to)])
            for t in texts])
        result = await self.client.translate(body)
        return [unprotect(item.translations[0].text) for item in result.value]

    async def _llm(self, texts: list[str], to: str) -> list[str]:
        from azure.ai.translation.text.models import (ReferenceTextPair, TranslateBody, TranslateInputItem,
                                                      TranslationTarget)

        pairs = [ReferenceTextPair(source=s, target=t) for s, t in REFERENCE_PAIRS.get(to.split("-")[0], [])]
        target = TranslationTarget(language=to, deployment_name=self.llm_deployment, tone="informal",
                                   reference_text_pairs=pairs or None)
        body = TranslateBody(inputs=[TranslateInputItem(text=t, language="en", targets=[target]) for t in texts])
        result = await self.client.translate(body)
        return [item.translations[0].text for item in result.value]

    async def close(self) -> None:
        await self.client.close()
