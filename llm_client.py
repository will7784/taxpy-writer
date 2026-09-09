"""
Cliente LLM multi-proveedor: Kimi, Gemini, DeepSeek, OpenAI, o cualquier API OpenAI-compatible.

Prioridad (default configurable via DEFAULT_LLM_PROVIDER):
  0. DeepSeek V4 Flash (por defecto, barato) / V4 Pro (complejo) — OpenAI-compatible
  1. Kimi/Moonshot (1M contexto) — ideal para leyes completas y modo estudio
  2. Gemini (1M contexto) — ideal para leyes completas
  3. OpenAI (128K contexto) — fallback clasico
  4. Custom (configurable) — Qwen, Moonshot, Zhipu, etc.

Cada provider expone su max_context para que el prompt builder ajuste.
"""

from __future__ import annotations

import asyncio
from http_security import tls_context
import httpx
from typing import Any, TypeVar

import config
from pydantic import BaseModel

from openai import AsyncOpenAI, BadRequestError

T = TypeVar("T", bound=BaseModel)

# Ventanas de contexto por provider (tokens)
CONTEXT_WINDOWS: dict[str, int] = {
    "kimi": 1_000_000,
    "gemini": 900_000,
    "deepseek": 128_000,
    "openai": 128_000,
    "custom": 128_000,
}


class LLMOutputTruncatedError(RuntimeError):
    """El proveedor terminó por longitud; nunca representa un JSON completo."""

    def __init__(self, *, provider: str, model: str, max_tokens: int, chars: int):
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens
        self.chars = chars
        super().__init__(f"Salida incompleta de {provider}/{model}: "
                         f"límite de {max_tokens} tokens, {chars} caracteres recibidos.")


class LLMClient:
    """Wrapper unificado para llamadas a LLM."""

    def __init__(self, provider: str | None = None) -> None:
        self._provider: str = "openai"
        self._openai: AsyncOpenAI | None = None
        self._gemini: Any = None
        self._model: str = "gpt-4o"
        self._max_context: int = 128_000

        # Orden de intento de proveedores.
        # - Si se pasa `provider`, se intenta ese; si no está configurado, error.
        # - Si no, se usa DEFAULT_LLM_PROVIDER primero (por defecto 'deepseek', el
        #   más barato) y luego la cadena clásica (kimi > gemini > deepseek > openai > custom).
        default = (getattr(config, "DEFAULT_LLM_PROVIDER", "") or "").strip().lower()
        order = [provider] if provider else [p for p in (default, "kimi", "gemini", "deepseek", "openai", "custom") if p]
        for p in dict.fromkeys(order):  # dedupe manteniendo el orden
            if self._try_configure(p):
                return
        raise RuntimeError(
            "Configura al menos un proveedor LLM: KIMI_API_KEY, GEMINI_API_KEY, "
            "DEEPSEEK_API_KEY, OPENAI_API_KEY, o CUSTOM_LLM_API_KEY + CUSTOM_LLM_BASE_URL."
        )

    def _try_configure(self, provider: str) -> bool:
        """Configura el proveedor indicado si hay API key. True si se configuró."""
        if provider == "kimi" and getattr(config, "KIMI_API_KEY", None):
            self._openai = AsyncOpenAI(
                api_key=config.KIMI_API_KEY,
                http_client=httpx.AsyncClient(verify=tls_context(), timeout=90),
                base_url=getattr(config, "KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
            )
            self._provider = "kimi"
            self._model = getattr(config, "KIMI_MODEL", "kimi-k2-0905-preview")
            self._max_context = getattr(config, "KIMI_MAX_CONTEXT", 1_000_000)
            return True
        if provider == "gemini" and getattr(config, "GEMINI_API_KEY", None):
            from google import genai as genai_client
            self._gemini = genai_client.Client(api_key=config.GEMINI_API_KEY)
            self._provider = "gemini"
            self._model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
            self._max_context = getattr(config, "GEMINI_MAX_CONTEXT", 1_000_000)
            return True
        if provider == "deepseek" and getattr(config, "DEEPSEEK_API_KEY", None):
            self._openai = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                http_client=httpx.AsyncClient(verify=tls_context(), timeout=90),
                base_url="https://api.deepseek.com",
            )
            self._provider = "deepseek"
            self._model = getattr(config, "DEEPSEEK_MODEL", "deepseek-v4-flash")
            self._max_context = getattr(config, "DEEPSEEK_MAX_CONTEXT", 128_000)
            return True
        if provider == "openai" and config.OPENAI_API_KEY:
            self._openai = AsyncOpenAI(api_key=config.OPENAI_API_KEY,
                http_client=httpx.AsyncClient(verify=tls_context(), timeout=90))
            self._provider = "openai"
            self._model = config.OPENAI_MODEL
            self._max_context = 128_000
            return True
        if provider == "custom" and getattr(config, "CUSTOM_LLM_API_KEY", None) and getattr(config, "CUSTOM_LLM_BASE_URL", None):
            self._openai = AsyncOpenAI(
                api_key=config.CUSTOM_LLM_API_KEY,
                http_client=httpx.AsyncClient(verify=tls_context(), timeout=90),
                base_url=config.CUSTOM_LLM_BASE_URL,
            )
            self._provider = "custom"
            self._model = getattr(config, "CUSTOM_LLM_MODEL", "default")
            self._max_context = getattr(config, "CUSTOM_LLM_MAX_CONTEXT", 128_000)
            return True
        return False
    @property
    def provider(self) -> str:
        return self._provider

    async def aclose(self) -> None:
        if self._openai:
            await self._openai.close()
        if self._gemini:
            await asyncio.to_thread(self._gemini.close)

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
        json_mode: bool = False,
        timeout: float = 90,
    ) -> str:
        if self._provider == "gemini" and self._gemini:
            return await self._gemini_chat(model, messages, temperature, max_tokens, json_mode=json_mode)
        if self._openai:
            return await self._openai_chat(model, messages, temperature, max_tokens,
                                           json_mode=json_mode, timeout=timeout)
        raise RuntimeError("Ningun proveedor LLM esta disponible.")

    async def _openai_chat(
        self,
        model: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        *,
        json_mode: bool = False,
        timeout: float = 90,
    ) -> str:
        m = model or self._model
        options = {"response_format": {"type": "json_object"}} if json_mode else {}
        response = await self._openai.chat.completions.create(
            model=m,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **options,
        )
        choice = response.choices[0]
        text = choice.message.content or ""
        if json_mode and choice.finish_reason == "length":
            raise LLMOutputTruncatedError(provider=self._provider, model=m,
                                          max_tokens=max_tokens, chars=len(text))
        return text

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
        *,
        json_mode: bool = False,
    ) -> str:
        from google.genai import types as genai_types
        m = model or self._model
        system_instruction, contents = self._split_gemini_messages(messages)

        options = {"response_mime_type": "application/json"} if json_mode else {}
        gen_config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            **options,
        )
        response = await asyncio.to_thread(
            self._gemini.models.generate_content,
            model=m,
            contents=contents,
            config=gen_config,
        )
        text = response.text or ""
        candidates = response.candidates or []
        if json_mode and candidates:
            reason = candidates[0].finish_reason
            if getattr(reason, "name", reason) == "MAX_TOKENS":
                raise LLMOutputTruncatedError(provider=self._provider, model=m,
                                              max_tokens=max_tokens, chars=len(text))
        return text

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
        try:
            response = await self._openai.beta.chat.completions.parse(
                model=m,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=schema,
            )
            parsed = response.choices[0].message.parsed
            if parsed is not None:
                return parsed
        except BadRequestError:
            pass
        # Fallback para proveedores OpenAI-compatibles que no soportan
        # response_format con schema (p. ej. DeepSeek): pedir JSON puro y validarlo.
        msgs: list[dict[str, str]] = list(messages)
        if "json" not in " ".join(str(x.get("content", "")) for x in msgs).lower():
            msgs.insert(0, {"role": "system", "content": "Responde solo con un objeto JSON valido."})
        response = await self._openai.chat.completions.create(
            model=m,
            messages=msgs,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        parsed = schema.model_validate_json(text)
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
