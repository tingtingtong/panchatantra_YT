from __future__ import annotations

import base64
import logging
import subprocess
import textwrap
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.schemas import PromptSpec
from app.utils import retry_operation

logger = logging.getLogger(__name__)


class BaseVideoProvider(ABC):
    @abstractmethod
    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        raise NotImplementedError


class OpenAIVideoProvider(BaseVideoProvider):
    """Best-effort wrapper for OpenAI's video endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        def _call() -> Path:
            client = httpx.Client(
                base_url="https://api.openai.com/v1",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                timeout=180.0,
            )
            aspect_ratio = "9:16" if resolution[1] > resolution[0] else "16:9"
            create_response = client.post(
                "/videos",
                json={
                    "model": self.settings.openai_video_model,
                    "prompt": prompt.prompt,
                    "duration": max(int(round(prompt.duration_seconds)), 3),
                    "size": aspect_ratio,
                },
            )
            create_response.raise_for_status()
            payload = create_response.json()
            job_id = payload.get("id")
            if not job_id:
                raise RuntimeError("OpenAI video job id missing")
            for _ in range(40):
                status_response = client.get(f"/videos/{job_id}")
                status_response.raise_for_status()
                status_payload = status_response.json()
                if status_payload.get("status") == "completed":
                    data = status_payload.get("data", [{}])[0]
                    if data.get("b64_json"):
                        output_path.write_bytes(base64.b64decode(data["b64_json"]))
                        return output_path
                    if data.get("url"):
                        video_response = client.get(data["url"])
                        video_response.raise_for_status()
                        output_path.write_bytes(video_response.content)
                        return output_path
                if status_payload.get("status") == "failed":
                    raise RuntimeError(status_payload.get("error", "OpenAI video generation failed"))
                time.sleep(6)
            raise RuntimeError("OpenAI video generation timed out")

        return retry_operation(
            _call,
            max_attempts=self.settings.max_retry_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
            logger=logger,
            operation_name="openai_video_generation",
        )


class PlaceholderVideoProvider(BaseVideoProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image_path = output_path.with_suffix(".png")
        self._render_image(image_path, prompt.prompt, resolution)
        command = [
            self.settings.ffmpeg_binary,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            str(max(prompt.duration_seconds, 1.5)),
            "-vf",
            f"scale={resolution[0]}:{resolution[1]},format=yuv420p",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg is required for placeholder scene clip generation.") from exc
        return output_path

    def _render_image(self, image_path: Path, text: str, resolution: tuple[int, int]) -> None:
        width, height = resolution
        canvas = Image.new("RGB", (width, height), color=(24, 44, 32))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, width, height // 2), fill=(34, 82, 60))
        draw.rectangle((0, height // 2, width, height), fill=(154, 103, 57))

        font_large = self._load_font(42 if width > height else 48)
        font_small = self._load_font(24 if width > height else 30)
        draw.rounded_rectangle((50, 50, width - 50, height - 50), radius=28, fill=(12, 19, 24))
        draw.text((90, 90), "SCENE PROMPT", fill=(235, 204, 139), font=font_small)
        y = 180
        for line in textwrap.wrap(text, width=26 if width < height else 40):
            draw.text((90, y), line, fill=(255, 245, 226), font=font_large)
            y += 64
        canvas.save(image_path)

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for font_name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except OSError:
                continue
        return ImageFont.load_default()


class VideoGenerationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.placeholder_provider = PlaceholderVideoProvider(settings)
        self.provider: BaseVideoProvider = (
            OpenAIVideoProvider(settings) if settings.openai_api_key else self.placeholder_provider
        )

    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        try:
            return self.provider.generate_scene_clip(prompt=prompt, output_path=output_path, resolution=resolution)
        except Exception as exc:
            logger.warning("Primary video provider failed, using placeholder clips instead: %s", exc)
            return self.placeholder_provider.generate_scene_clip(prompt=prompt, output_path=output_path, resolution=resolution)
