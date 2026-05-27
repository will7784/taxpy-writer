"""
Cliente LLM dual: OpenAI (GPT-4o) o Google Gemini 1.5 Pro.

Si existe GEMINI_API_KEY, usa Gemini por defecto (mayor ventana de contexto,
mejor para cruzar leyes extensas). Si no, fallback a OpenAI.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import config

# OpenAI
from openai import AsyncOpenAI

# Gemini
from google import genai as genai_client
from google.genai import types as genai_types


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
        temperature: float = 0.7,
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

    async def _gemini_chat(
        self,
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        m = model or "gemini-1.5-pro-latest"

        # Separar system prompt del resto
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

        contents = "\n\n".join(user_parts)

        config_gemini = genai_types.GenerateContentConfig(
            system_instruction=system_instruction.strip() or None,
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
