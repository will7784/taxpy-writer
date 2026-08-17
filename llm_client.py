"""
Cliente LLM multi-proveedor: Kimi, Gemini, DeepSeek, OpenAI, o cualquier API OpenAI-compatible.

Prioridad:
  1. Kimi/Moonshot (1M contexto) — ideal para leyes completas y modo estudio
  2. Gemini (1M contexto) — ideal para leyes completas
  3. DeepSeek (128K contexto) — barato, OpenAI-compatible
  4. OpenAI (128K contexto) — fallback clasico
  5. Custom (configurable) — Qwen, Moonshot, Zhipu, etc.

Cada provider expone su max_context para que el prompt builder ajuste.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

import config
from pydantic import BaseModel

from openai import AsyncOpenAI

T = TypeVar("T", bound=BaseModel)

# Ventanas de contexto por provider (tokens)
CONTEXT_WINDOWS: dict[str, int] = {
    "kimi": 1_000_000,
    "gemini": 900_000,
    "deepseek": 128_000,
    "openai": 128_000,
    "custom": 128_000,
}


class LLMClient:
    """Wrapper unificado para llamadas a LLM."""

    def __init__(self) -> None:
        self._provider: str = "openai"
        self._openai: AsyncOpenAI | None = None
        self._gemini: Any = None
        self._model: str = "gpt-4o"
        self._max_context: int = 128_000

        # Kimi / Moonshot (1M contexto, OpenAI-compatible)
        if getattr(config, "KIMI_API_KEY", None):
            self._openai = AsyncOpenAI(
                api_key=config.KIMI_API_KEY,
                base_url=getattr(config, "KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
            )
            self._provider = "kimi"
            self._model = getattr(config, "KIMI_MODEL", "kimi-k2-0905-preview")
            self._max_context = getattr(config, "KIMI_MAX_CONTEXT", 1_000_000)
        # Gemini (1M contexto)
        elif getattr(config, "GEMINI_API_KEY", None):
            from google import genai as genai_client
            self._gemini = genai_client.Client(api_key=config.GEMINI_API_KEY)
            self._provider = "gemini"
            self._model = "gemini-1.5-pro-latest"
            self._max_context = 900_000
        # DeepSeek (OpenAI-compatible)
        elif getattr(config, "DEEPSEEK_API_KEY", None):
            self._openai = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            self._provider = "deepseek"
            self._model = getattr(config, "DEEPSEEK_MODEL", "deepseek-chat")
            self._max_context = 128_000
        # OpenAI
        elif config.OPENAI_API_KEY:
            self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
            self._provider = "openai"
            self._model = config.OPENAI_MODEL
            self._max_context = 128_000
        # Custom OpenAI-compatible (Qwen, Moonshot, Zhipu, etc.)
        elif getattr(config, "CUSTOM_LLM_API_KEY", None) and getattr(config, "CUSTOM_LLM_BASE_URL", None):
            self._openai = AsyncOpenAI(
                api_key=config.CUSTOM_LLM_API_KEY,
                base_url=config.CUSTOM_LLM_BASE_URL,
            )
            self._provider = "custom"
            self._model = getattr(config, "CUSTOM_LLM_MODEL", "default")
            self._max_context = getattr(config, "CUSTOM_LLM_MAX_CONTEXT", 128_000)
        else:
            raise RuntimeError(
                "Configura al menos un proveedor LLM: KIMI_API_KEY, GEMINI_API_KEY, "
                "DEEPSEEK_API_KEY, OPENAI_API_KEY, o CUSTOM_LLM_API_KEY + CUSTOM_LLM_BASE_URL."
            )

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_context(self) -> int:
        return self._max_context

    async def chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        if self._provider == "gemini" and self._gemini:
            return await self._gemini_chat(model, messages, temperature, max_tokens)
        if self._openai:
            return await self._openai_chat(model, messages, temperature, max_tokens)
        raise RuntimeError("Ningun proveedor LLM esta disponible.")

    async def _openai_chat(
        self,
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        m = model or self._model
        response = await self._openai.chat.completions.create(
            model=m,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _split_gemini_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
        system_instruction = ""
        user_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_instruction += content + "\n"
            elif role == "user":
                user_parts.append(content)
            elif role == "assistant":
                user_parts.append(f"[Respuesta anterior]: {content}")
        return system_instruction.strip(), "\n\n".join(user_parts)

    async def _gemini_chat(
        self,
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        from google.genai import types as genai_types
        m = model or self._model
        system_instruction, contents = self._split_gemini_messages(messages)

        gen_config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = await asyncio.to_thread(
            self._gemini.models.generate_content,
            model=m,
            contents=contents,
            config=gen_config,
        )
        return response.text or ""

    async def chat_completion_structured(
        self,
        *,
        schema: type[T],
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> T:
        if self._provider == "gemini" and self._gemini:
            return await self._gemini_structured(schema, model, messages, temperature, max_tokens)
        if self._openai:
            return await self._openai_structured(schema, model, messages, temperature, max_tokens)
        raise RuntimeError("Ningun proveedor LLM esta disponible.")

    async def _openai_structured(
        self,
        schema: type[T],
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> T:
        m = model or self._model
        response = await self._openai.beta.chat.completions.parse(
            model=m,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"No se obtuvo salida estructurada valida para {schema.__name__}")
        return parsed

    async def _gemini_structured(
        self,
        schema: type[T],
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> T:
        from google.genai import types as genai_types
        m = model or self._model
        system_instruction, contents = self._split_gemini_messages(messages)

        gen_config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )
        response = await asyncio.to_thread(
            self._gemini.models.generate_content,
            model=m,
            contents=contents,
            config=gen_config,
        )
        return schema.model_validate_json(response.text)
