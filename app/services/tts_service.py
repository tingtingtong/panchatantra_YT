from __future__ import annotations

import io
import logging
import math
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.utils import retry_operation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TTSResult:
    output_path: Path
    duration_seconds: float
    provider: str


class BaseTTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, output_path: Path, language: str) -> TTSResult:
        raise NotImplementedError


class ElevenLabsTTSProvider(BaseTTSProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def synthesize(self, text: str, output_path: Path, language: str) -> TTSResult:
        def _call() -> TTSResult:
            response = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.settings.elevenlabs_voice_id}",
                headers={
                    "xi-api-key": self.settings.elevenlabs_api_key or "",
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
                },
                timeout=120.0,
            )
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)
            duration = max(len(text.split()) / 2.6, 5.0)
            return TTSResult(output_path=output_path, duration_seconds=duration, provider="elevenlabs")

        return retry_operation(
            _call,
            max_attempts=self.settings.max_retry_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
            logger=logger,
            operation_name="elevenlabs_tts",
        )


class LocalPlaceholderTTSProvider(BaseTTSProvider):
    def synthesize(self, text: str, output_path: Path, language: str) -> TTSResult:
        sample_rate = 22050
        words = max(len(text.split()), 1)
        duration_seconds = max(words / 2.5, 4.0)
        total_frames = int(sample_rate * duration_seconds)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            frames = io.BytesIO()
            for frame_index in range(total_frames):
                t = frame_index / sample_rate
                envelope = 0.35 if int(t * 2) % 2 == 0 else 0.18
                frequency = 230 if language == "kn" else 190
                sample = int(16000 * envelope * math.sin(2 * math.pi * frequency * t))
                frames.write(sample.to_bytes(2, byteorder="little", signed=True))
            wav_file.writeframes(frames.getvalue())

        return TTSResult(output_path=output_path, duration_seconds=duration_seconds, provider="local_placeholder")


class TTSService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.elevenlabs_api_key:
            self.provider: BaseTTSProvider = ElevenLabsTTSProvider(settings)
        else:
            self.provider = LocalPlaceholderTTSProvider()

    def synthesize(self, text: str, output_path: Path, language: str) -> TTSResult:
        return self.provider.synthesize(text, output_path, language)
