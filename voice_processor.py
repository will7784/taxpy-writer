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
        Intenta primero con OGG original; si falla, convierte a WAV.
        """
        console.print("  [dim]🎙️ Whisper transcribiendo...[/dim]")

        # Intento 1: OGG original directo (formato nativo de Telegram)
        try:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=("audio.ogg", io.BytesIO(audio_bytes), "audio/ogg"),
                language="es",
            )
            result = (response.text or "").strip()
            if result and len(result) > 5:
                return result
            console.print("[yellow]⚠️ Whisper devolvió texto vacío o muy corto con OGG. Intentando WAV...[/yellow]")
        except Exception as e1:
            console.print(f"[yellow]⚠️ Whisper con OGG falló: {e1}. Intentando WAV...[/yellow]")

        # Intento 2: convertir a WAV mono 16kHz y reintentar
        try:
            wav_bytes = self._normalize_audio(audio_bytes, mime_type)
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=("audio.wav", io.BytesIO(wav_bytes), "audio/wav"),
                language="es",
            )
            return (response.text or "").strip()
        except Exception as e2:
            console.print(f"[red]❌ Error STT Whisper: {e2}[/red]")
            raise RuntimeError(f"Error en transcripción Whisper: {e2}")

    def _normalize_audio(self, audio_bytes: bytes, mime_type: str) -> bytes:
        """Convierte audio a WAV mono 16kHz para Whisper."""
        if not _PYDUB_AVAILABLE or not AudioSegment:
            # Si no hay pydub, devolver original y cruzar dedos
            return audio_bytes

        try:
            fmt = "ogg"
            if "mp3" in mime_type:
                fmt = "mp3"
            elif "wav" in mime_type:
                fmt = "wav"
            elif "m4a" in mime_type:
                fmt = "mp4"

            seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
            seg = seg.set_frame_rate(16000).set_channels(1)
            buf = io.BytesIO()
            seg.export(buf, format="wav")
            return buf.getvalue()
        except Exception as e:
            error_str = str(e).lower()
            if "ffmpeg" in error_str or "avconv" in error_str:
                raise RuntimeError(
                    f"ffmpeg no está instalado. Se requiere para convertir audio. "
                    f"Error original: {e}"
                )
            # Si falla la normalización, devolver original como fallback
            console.print(f"[yellow]⚠️ Falló normalización de audio, usando original: {e}[/yellow]")
            return audio_bytes

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
