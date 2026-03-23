from __future__ import annotations

import base64
import logging
import math
import random
import subprocess
import textwrap
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
        self.render_scene_image(image_path, prompt.prompt, resolution)
        self._image_to_video(image_path, output_path, resolution, prompt.duration_seconds)
        return output_path

    def render_scene_image(self, image_path: Path, prompt_text: str, resolution: tuple[int, int]) -> None:
        width, height = resolution
        seed = sum(ord(char) for char in prompt_text)
        random.seed(seed)
        palette = self._pick_palette(prompt_text)

        canvas = Image.new("RGBA", (width, height), (*palette["sky_top"], 255))
        draw = ImageDraw.Draw(canvas)
        self._paint_gradient(draw, width, height, palette["sky_top"], palette["sky_bottom"])
        self._draw_light_source(draw, width, height, palette, prompt_text)
        self._draw_distant_hills(draw, width, height, palette)
        self._draw_forest_layers(draw, width, height, palette)
        self._draw_ground(draw, width, height, palette)
        self._draw_scene_feature(draw, width, height, palette, prompt_text)
        self._draw_characters(draw, width, height, palette, prompt_text)
        self._draw_atmosphere(canvas, width, height, palette)
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.35))
        image_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(image_path)

    def render_title_card_image(self, image_path: Path, text: str, resolution: tuple[int, int]) -> None:
        width, height = resolution
        canvas = Image.new("RGBA", (width, height), color=(20, 36, 28, 255))
        draw = ImageDraw.Draw(canvas)
        self._paint_gradient(draw, width, height, (22, 45, 39), (161, 107, 57))
        self._draw_distant_hills(draw, width, height, self._pick_palette("forest"))
        self._draw_forest_layers(draw, width, height, self._pick_palette("forest"))
        draw.rounded_rectangle(
            (int(width * 0.08), int(height * 0.12), int(width * 0.92), int(height * 0.88)),
            radius=int(min(width, height) * 0.04),
            fill=(10, 20, 24, 220),
            outline=(242, 205, 128),
            width=4,
        )
        title_font = self._load_font(54 if width > height else 60)
        subtitle_font = self._load_font(28 if width > height else 34)
        draw.text((int(width * 0.13), int(height * 0.18)), "PANCHATANTRA", fill=(242, 205, 128), font=subtitle_font)
        y = int(height * 0.32)
        wrap_width = 22 if width < height else 30
        for line in textwrap.wrap(text, width=wrap_width):
            draw.text((int(width * 0.13), y), line, fill=(255, 246, 225), font=title_font)
            y += int(title_font.size * 1.35)
        canvas.convert("RGB").save(image_path)

    def _image_to_video(
        self,
        image_path: Path,
        output_path: Path,
        resolution: tuple[int, int],
        duration_seconds: float,
    ) -> None:
        frames = max(int(round(duration_seconds * 24)), 24)
        zoom_speed = "0.0011" if resolution[1] > resolution[0] else "0.0007"
        filter_chain = (
            f"scale={resolution[0]}:{resolution[1]},"
            f"zoompan=z='min(zoom+{zoom_speed},1.12)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={resolution[0]}x{resolution[1]}:fps=24,"
            "format=yuv420p"
        )
        command = [
            self.settings.ffmpeg_binary,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            filter_chain,
            "-t",
            str(max(duration_seconds, 1.5)),
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

    @staticmethod
    def _pick_palette(prompt_text: str) -> dict[str, tuple[int, int, int]]:
        prompt_text = prompt_text.lower()
        if any(word in prompt_text for word in ("night", "moon", "well", "reflection", "dark")):
            return {
                "sky_top": (20, 34, 64),
                "sky_bottom": (74, 88, 115),
                "hill_far": (49, 79, 83),
                "hill_mid": (33, 67, 58),
                "canopy_dark": (24, 53, 40),
                "canopy_mid": (43, 88, 60),
                "ground": (97, 73, 41),
                "accent": (231, 205, 128),
                "water": (42, 86, 109),
            }
        if any(word in prompt_text for word in ("sun", "amber", "evening", "dusk", "relief")):
            return {
                "sky_top": (244, 176, 92),
                "sky_bottom": (255, 211, 134),
                "hill_far": (139, 122, 89),
                "hill_mid": (89, 105, 61),
                "canopy_dark": (45, 74, 46),
                "canopy_mid": (87, 129, 69),
                "ground": (154, 101, 56),
                "accent": (255, 237, 177),
                "water": (76, 119, 126),
            }
        return {
            "sky_top": (118, 181, 160),
            "sky_bottom": (202, 225, 165),
            "hill_far": (97, 136, 109),
            "hill_mid": (70, 107, 77),
            "canopy_dark": (37, 77, 57),
            "canopy_mid": (70, 130, 84),
            "ground": (161, 109, 65),
            "accent": (244, 219, 145),
            "water": (80, 145, 158),
        }

    @staticmethod
    def _paint_gradient(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        top_color: tuple[int, int, int],
        bottom_color: tuple[int, int, int],
    ) -> None:
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = tuple(int(top_color[index] * (1 - ratio) + bottom_color[index] * ratio) for index in range(3))
            draw.line((0, y, width, y), fill=color)

    def _draw_light_source(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        prompt_text: str,
    ) -> None:
        is_night = any(word in prompt_text.lower() for word in ("night", "moon", "well", "reflection", "dark"))
        radius = int(min(width, height) * (0.08 if is_night else 0.1))
        cx = int(width * 0.78)
        cy = int(height * (0.2 if is_night else 0.18))
        color = (244, 244, 232) if is_night else palette["accent"]
        for offset in range(5, 0, -1):
            glow_radius = radius + offset * 20
            alpha = 20 + offset * 7
            draw.ellipse(
                (cx - glow_radius, cy - glow_radius, cx + glow_radius, cy + glow_radius),
                fill=(*color, alpha),
            )
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)

    @staticmethod
    def _draw_distant_hills(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        hill_base = int(height * 0.6)
        draw.polygon(
            [
                (0, hill_base),
                (int(width * 0.18), int(height * 0.42)),
                (int(width * 0.35), int(height * 0.56)),
                (int(width * 0.58), int(height * 0.38)),
                (int(width * 0.82), int(height * 0.55)),
                (width, int(height * 0.46)),
                (width, height),
                (0, height),
            ],
            fill=palette["hill_far"],
        )
        draw.polygon(
            [
                (0, int(height * 0.72)),
                (int(width * 0.15), int(height * 0.52)),
                (int(width * 0.32), int(height * 0.64)),
                (int(width * 0.5), int(height * 0.5)),
                (int(width * 0.68), int(height * 0.67)),
                (int(width * 0.84), int(height * 0.53)),
                (width, int(height * 0.69)),
                (width, height),
                (0, height),
            ],
            fill=palette["hill_mid"],
        )

    def _draw_forest_layers(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        for index in range(10):
            trunk_x = int((index + 0.2) * width / 10)
            trunk_width = max(width // 80, 10)
            draw.rectangle(
                (trunk_x, int(height * 0.22), trunk_x + trunk_width, height),
                fill=(64, 44, 26),
            )
        for index in range(8):
            offset = index * width // 8
            radius = int(min(width, height) * 0.16)
            y = int(height * (0.14 + (index % 3) * 0.04))
            draw.ellipse(
                (offset - radius // 2, y, offset + radius, y + radius),
                fill=palette["canopy_mid"] if index % 2 else palette["canopy_dark"],
            )
        for index in range(14):
            base_x = int(index * width / 13)
            draw.polygon(
                [
                    (base_x, int(height * 0.82)),
                    (base_x + int(width * 0.02), int(height * 0.66)),
                    (base_x + int(width * 0.04), int(height * 0.82)),
                ],
                fill=palette["canopy_dark"],
            )

    @staticmethod
    def _draw_ground(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        draw.rectangle((0, int(height * 0.72), width, height), fill=palette["ground"])
        for index in range(12):
            x = int(index * width / 11)
            draw.ellipse(
                (x, int(height * 0.8), x + width * 0.06, int(height * 0.88)),
                fill=(112, 83, 47),
            )

    def _draw_scene_feature(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        prompt_text: str,
    ) -> None:
        lowered = prompt_text.lower()
        if any(word in lowered for word in ("well", "reflection", "water", "pond")):
            rim_box = (int(width * 0.54), int(height * 0.56), int(width * 0.8), int(height * 0.82))
            draw.ellipse(rim_box, fill=(78, 79, 86), outline=(190, 180, 160), width=8)
            water_box = (rim_box[0] + 14, rim_box[1] + 14, rim_box[2] - 14, rim_box[3] - 14)
            draw.ellipse(water_box, fill=palette["water"])
            draw.arc(water_box, start=200, end=330, fill=(210, 228, 232), width=5)
        elif any(word in lowered for word in ("banyan", "tree")):
            trunk_left = int(width * 0.65)
            draw.rectangle((trunk_left, int(height * 0.36), trunk_left + int(width * 0.08), height), fill=(88, 58, 37))
            draw.ellipse(
                (int(width * 0.56), int(height * 0.12), int(width * 0.88), int(height * 0.42)),
                fill=palette["canopy_dark"],
            )
        else:
            draw.arc(
                (int(width * 0.2), int(height * 0.7), int(width * 0.9), int(height * 1.06)),
                start=180,
                end=360,
                fill=(214, 188, 141),
                width=10,
            )

    def _draw_characters(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        prompt_text: str,
    ) -> None:
        lowered = prompt_text.lower()
        if "lion" in lowered:
            self._draw_lion(draw, int(width * 0.26), int(height * 0.7), int(min(width, height) * 0.14))
        if "rabbit" in lowered or "hare" in lowered:
            self._draw_rabbit(draw, int(width * 0.7), int(height * 0.78), int(min(width, height) * 0.09))
        if "crocodile" in lowered:
            self._draw_crocodile(draw, int(width * 0.68), int(height * 0.8), int(min(width, height) * 0.11))
        if "monkey" in lowered:
            self._draw_monkey(draw, int(width * 0.3), int(height * 0.55), int(min(width, height) * 0.1))
        if "crow" in lowered or "dove" in lowered or "heron" in lowered:
            self._draw_bird(draw, int(width * 0.6), int(height * 0.34), int(min(width, height) * 0.08))
        if "elephant" in lowered:
            self._draw_elephant(draw, int(width * 0.3), int(height * 0.77), int(min(width, height) * 0.16))
        if "serpent" in lowered or "snake" in lowered:
            self._draw_serpent(draw, int(width * 0.72), int(height * 0.82), int(min(width, height) * 0.1))

    @staticmethod
    def _draw_lion(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        mane_color = (128, 79, 25)
        body_color = (201, 153, 78)
        draw.ellipse((x - size * 0.55, y - size * 0.95, x + size * 0.35, y - size * 0.05), fill=mane_color)
        draw.ellipse((x - size * 0.28, y - size * 0.8, x + size * 0.18, y - size * 0.28), fill=body_color)
        draw.rounded_rectangle((x - size * 0.65, y - size * 0.36, x + size * 0.52, y + size * 0.2), radius=18, fill=body_color)
        for leg_offset in (-0.45, -0.12, 0.2, 0.48):
            lx = x + int(size * leg_offset)
            draw.rectangle((lx, y - int(size * 0.04), lx + int(size * 0.12), y + int(size * 0.56)), fill=body_color)
        draw.arc((x + size * 0.28, y - size * 0.38, x + size * 1.1, y + size * 0.2), start=210, end=20, fill=mane_color, width=6)
        draw.ellipse((x + size * 0.98, y + size * 0.04, x + size * 1.14, y + size * 0.2), fill=mane_color)

    @staticmethod
    def _draw_rabbit(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        fur = (229, 224, 210)
        inner = (241, 180, 180)
        draw.ellipse((x - size * 0.35, y - size * 0.88, x - size * 0.08, y - size * 0.12), fill=fur)
        draw.ellipse((x + size * 0.02, y - size * 0.88, x + size * 0.3, y - size * 0.12), fill=fur)
        draw.ellipse((x - size * 0.28, y - size * 0.76, x - size * 0.16, y - size * 0.2), fill=inner)
        draw.ellipse((x + size * 0.08, y - size * 0.76, x + size * 0.2, y - size * 0.2), fill=inner)
        draw.ellipse((x - size * 0.32, y - size * 0.44, x + size * 0.32, y + size * 0.06), fill=fur)
        draw.ellipse((x - size * 0.24, y - size * 0.04, x + size * 0.42, y + size * 0.64), fill=fur)
        draw.ellipse((x + size * 0.28, y + size * 0.46, x + size * 0.56, y + size * 0.72), fill=fur)

    @staticmethod
    def _draw_monkey(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        fur = (109, 69, 43)
        face = (205, 165, 122)
        draw.ellipse((x - size * 0.3, y - size * 0.65, x + size * 0.26, y - size * 0.12), fill=fur)
        draw.ellipse((x - size * 0.14, y - size * 0.46, x + size * 0.12, y - size * 0.18), fill=face)
        draw.rounded_rectangle((x - size * 0.5, y - size * 0.15, x + size * 0.42, y + size * 0.5), radius=20, fill=fur)
        draw.arc((x + size * 0.18, y - size * 0.25, x + size * 1.02, y + size * 0.56), start=240, end=70, fill=fur, width=6)

    @staticmethod
    def _draw_crocodile(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        color = (68, 106, 72)
        draw.rounded_rectangle((x - size, y - size * 0.2, x + size * 0.9, y + size * 0.22), radius=18, fill=color)
        draw.polygon([(x + size * 0.9, y - size * 0.16), (x + size * 1.4, y), (x + size * 0.9, y + size * 0.2)], fill=color)
        draw.polygon([(x - size, y - size * 0.05), (x - size * 1.45, y - size * 0.26), (x - size * 1.05, y + size * 0.1)], fill=color)

    @staticmethod
    def _draw_bird(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        body = (219, 220, 224)
        wing = (130, 136, 151)
        beak = (221, 171, 71)
        draw.ellipse((x - size * 0.45, y - size * 0.2, x + size * 0.38, y + size * 0.36), fill=body)
        draw.polygon([(x - size * 0.12, y), (x - size * 0.62, y - size * 0.26), (x - size * 0.08, y + size * 0.12)], fill=wing)
        draw.polygon([(x + size * 0.35, y + size * 0.04), (x + size * 0.72, y + size * 0.14), (x + size * 0.38, y + size * 0.22)], fill=beak)

    @staticmethod
    def _draw_elephant(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        body = (124, 132, 143)
        draw.rounded_rectangle((x - size, y - size * 0.55, x + size * 0.72, y + size * 0.2), radius=24, fill=body)
        draw.ellipse((x + size * 0.4, y - size * 0.72, x + size * 0.95, y - size * 0.18), fill=body)
        draw.arc((x + size * 0.68, y - size * 0.25, x + size * 1.22, y + size * 0.56), start=180, end=350, fill=body, width=12)
        for leg_offset in (-0.7, -0.3, 0.1, 0.48):
            lx = x + int(size * leg_offset)
            draw.rectangle((lx, y, lx + int(size * 0.18), y + int(size * 0.72)), fill=body)

    @staticmethod
    def _draw_serpent(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
        color = (63, 101, 52)
        for step in range(5):
            left = x - size + step * size * 0.35
            top = y - step * size * 0.16
            draw.arc((left, top, left + size * 0.9, top + size * 0.9), start=0, end=180, fill=color, width=8)

    @staticmethod
    def _draw_atmosphere(
        canvas: Image.Image,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for index in range(30):
            x = int((index * 137) % width)
            y = int((index * 89) % int(height * 0.7))
            radius = 6 + (index % 4) * 4
            alpha = 18 + (index % 5) * 8
            draw.ellipse((x, y, x + radius, y + radius), fill=(*palette["accent"], alpha))
        blurred = overlay.filter(ImageFilter.GaussianBlur(radius=5))
        canvas.alpha_composite(blurred.convert("RGBA"))

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
