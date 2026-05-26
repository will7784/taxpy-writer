"""
Procesamiento de voz para Telegram usando OpenAI API.

STT: Whisper-1 transcribe audio a texto.
TTS: tts-1 genera voz natural desde texto (voz femenina "shimmer").
"""

from __future__ import annotations

import io
from typing import Any

import config
from openai import AsyncOpenAI
from rich.console import Console

console = Console()

try:
    from pydub import AudioSegment

    _PYDUB_AVAILABLE = True
except Exception:
    _PYDUB_AVAILABLE = False
    AudioSegment = None  # type: ignore


class VoiceProcessor:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """
        Transcribe audio a texto usando OpenAI Whisper-1.
        Acepta OGG/Opus directamente (formato de Telegram voice messages).
        """
        try:
            console.print("  [dim]🎙️ Whisper transcribiendo...[/dim]")

            # Whisper soporta múltiples formatos incluyendo ogg
            file_ext = "ogg"
            if "mp3" in mime_type:
                file_ext = "mp3"
            elif "wav" in mime_type:
                file_ext = "wav"
            elif "m4a" in mime_type:
                file_ext = "m4a"

            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=(f"audio.{file_ext}", io.BytesIO(audio_bytes), mime_type),
                language="es",
            )
            return (response.text or "").strip()
        except Exception as e:
            console.print(f"[red]❌ Error STT Whisper: {e}[/red]")
            raise RuntimeError(f"Error en transcripción Whisper: {e}")

    async def synthesize(self, text: str) -> bytes:
        """
        Genera audio OGG/Opus desde texto usando OpenAI TTS.
        Retorna bytes listos para enviar como voice message de Telegram.
        Voz: 'shimmer' (femenina, cálida y natural).
        """
        # Truncar texto muy largo
        if len(text) > 4000:
            text = text[:4000] + "\n\n[El mensaje fue truncado para la versión de voz.]"

        try:
            console.print("  [dim]🔊 OpenAI TTS generando voz...[/dim]")
            response = await self._client.audio.speech.create(
                model="tts-1",
                voice="shimmer",
                input=text,
            )

            mp3_bytes = response.read()

            # Convertir MP3 → OGG/Opus para Telegram voice messages
            return self._mp3_to_opus(mp3_bytes)

        except Exception as e:
            console.print(f"[red]❌ Error TTS OpenAI: {e}[/red]")
            raise RuntimeError(f"Error en síntesis OpenAI TTS: {e}")

    def _mp3_to_opus(self, mp3_bytes: bytes) -> bytes:
        """Convierte MP3 a OGG/Opus (formato nativo de Telegram voice messages)."""
        if not _PYDUB_AVAILABLE or not AudioSegment:
            raise RuntimeError(
                "pydub no está disponible. Instala: pip install pydub"
            )

        try:
            seg = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
            ogg_buf = io.BytesIO()
            seg.export(ogg_buf, format="ogg", codec="libopus")
            ogg_buf.seek(0)
            return ogg_buf.read()
        except Exception as e:
            error_str = str(e).lower()
            if "ffmpeg" in error_str or "avconv" in error_str:
                raise RuntimeError(
                    f"ffmpeg no está instalado. Se requiere para convertir audio a OGG/Opus. "
                    f"Error original: {e}"
                )
            raise RuntimeError(f"Error convirtiendo MP3 a OGG/Opus: {e}")
