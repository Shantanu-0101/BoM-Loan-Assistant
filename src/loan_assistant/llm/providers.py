"""LLM providers behind one small interface: Groq first, Gemini as the fallback."""

import logging
from typing import Protocol

from google import genai
from google.genai import types
from groq import Groq

from loan_assistant.config import settings

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30
MAX_OUTPUT_TOKENS = 2048  # reasoning models spend part of this on thinking


class ProviderError(RuntimeError):
    """Every provider failed (or none is configured)."""


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class GroqProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.name = f"groq/{model}"
        self._model = model
        self._client = Groq(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=1)

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            max_completion_tokens=MAX_OUTPUT_TOKENS,
            reasoning_effort="low",
        )
        return (response.choices[0].message.content or "").strip()


class GeminiProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.name = f"gemini/{model}"
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_S * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def complete(self, system: str, user: str) -> str:
        response = self._client.models.generate_content(
            model=self._model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0, max_output_tokens=MAX_OUTPUT_TOKENS
            ),
        )
        return (response.text or "").strip()


def default_providers() -> list[LLMProvider]:
    """Providers that have a key, in order of preference."""
    providers: list[LLMProvider] = []
    if settings.groq_api_key:
        providers.append(GroqProvider(settings.groq_api_key, settings.groq_model))
    if settings.gemini_api_key:
        providers.append(GeminiProvider(settings.gemini_api_key, settings.gemini_model))
    return providers


def complete_with_fallback(providers: list[LLMProvider], system: str, user: str) -> tuple[str, str]:
    """Try each provider in turn; return ``(text, provider_name)`` from the first that works."""
    if not providers:
        raise ProviderError(
            "no LLM key found: set GROQ_API_KEY (and optionally GEMINI_API_KEY) in .env"
        )
    errors: list[str] = []
    for provider in providers:
        try:
            text = provider.complete(system, user)
        except Exception as exc:  # network, rate limit, bad key: all mean "try the next one"
            log.warning("%s failed: %s", provider.name, exc)
            errors.append(f"{provider.name}: {type(exc).__name__}")
            continue
        if text:
            return text, provider.name
        errors.append(f"{provider.name}: empty answer")
    raise ProviderError("all providers failed (" + "; ".join(errors) + ")")
