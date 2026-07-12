"""
Cliente LLM dual: OpenAI (GPT-4o) o Google Gemini 1.5 Pro.

Si existe GEMINI_API_KEY, usa Gemini por defecto (mayor ventana de contexto,
mejor para cruzar leyes extensas). Si no, fallback a OpenAI.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, TypeVar

import config
from pydantic import BaseModel

# OpenAI
from openai import AsyncOpenAI

# Gemini
from google import genai as genai_client
from google.genai import types as genai_types

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Wrapper unificado para llamadas a LLM (OpenAI o Gemini)."""

    def __init__(self) -> None:
        self._provider: str = "openai"
        self._openai: AsyncOpenAI | None = None
        self._gemini: genai_client.Client | None = None

        # Prioridad: Gemini si hay API key
        if getattr(config, "GEMINI_API_KEY", None):
            self._gemini = genai_client.Client(api_key=config.GEMINI_API_KEY)
            self._provider = "gemini"
        elif config.OPENAI_API_KEY:
            self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
            self._provider = "openai"
        else:
            raise RuntimeError("No hay GEMINI_API_KEY ni OPENAI_API_KEY configuradas.")

    @property
    def provider(self) -> str:
        return self._provider

    async def chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        """Genera una respuesta de chat dada una lista de mensajes OpenAI-style."""
        if self._provider == "gemini" and self._gemini:
            return await self._gemini_chat(model, messages, temperature, max_tokens)
        if self._openai:
            return await self._openai_chat(model, messages, temperature, max_tokens)
        raise RuntimeError("Ningún proveedor LLM está disponible.")

    async def _openai_chat(
        self,
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        m = model or config.OPENAI_MODEL
        response = await self._openai.chat.completions.create(
            model=m,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _split_gemini_messages(messages: list[dict[str, str]]) -> tuple[str, str]:
        """Separa mensajes estilo OpenAI en (system_instruction, contents) para Gemini."""
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
        m = model or "gemini-1.5-pro-latest"
        system_instruction, contents = self._split_gemini_messages(messages)

        config_gemini = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Gemini sync -> async via thread
        response = await asyncio.to_thread(
            self._gemini.models.generate_content,
            model=m,
            contents=contents,
            config=config_gemini,
        )
        return response.text or ""

    # ── Salida estructurada (validada contra un schema Pydantic) ────

    async def chat_completion_structured(
        self,
        *,
        schema: type[T],
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> T:
        """Genera una respuesta validada contra un schema Pydantic.

        Reemplaza el patrón frágil de parsear JSON "a mano" (strip de
        fences markdown + json.loads) usado en decision_engine.interpret_query().
        En OpenAI usa salida estructurada nativa (garantiza JSON válido
        contra el schema); en Gemini usa modo JSON + validación Pydantic.
        Lanza ValueError si el LLM no devuelve algo válido contra `schema`.
        """
        if self._provider == "gemini" and self._gemini:
            return await self._gemini_structured(schema, model, messages, temperature, max_tokens)
        if self._openai:
            return await self._openai_structured(schema, model, messages, temperature, max_tokens)
        raise RuntimeError("Ningún proveedor LLM está disponible.")

    async def _openai_structured(
        self,
        schema: type[T],
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> T:
        m = model or config.OPENAI_MODEL
        response = await self._openai.beta.chat.completions.parse(
            model=m,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"OpenAI no devolvió una salida estructurada válida para {schema.__name__}")
        return parsed

    async def _gemini_structured(
        self,
        schema: type[T],
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> T:
        m = model or "gemini-1.5-pro-latest"
        system_instruction, contents = self._split_gemini_messages(messages)

        config_gemini = genai_types.GenerateContentConfig(
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
            config=config_gemini,
        )
        return schema.model_validate_json(response.text)
