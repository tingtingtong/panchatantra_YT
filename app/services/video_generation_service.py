from __future__ import annotations

import base64
import logging
import random
import subprocess
import textwrap
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.config import Settings
from app.schemas import PromptSpec
from app.utils import retry_operation

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


class BaseImageProvider(ABC):
    @abstractmethod
    def generate_scene_image(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        raise NotImplementedError


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


@dataclass(slots=True)
class MotionPlan:
    zoom_start: float
    zoom_end: float
    drift_x: int
    drift_y: int
    overlay_shift_x: int
    overlay_shift_y: int
    color_treatment: str
    finish_filter: str


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


class OpenAIImageProvider(BaseImageProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if OpenAI and settings.openai_api_key else None

    def generate_scene_image(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        if not self.client:
            raise RuntimeError("OpenAI client is not configured for image generation.")

        rendered_prompt = self.compose_prompt(prompt, resolution)
        size = self._image_size(resolution)

        def _call() -> Path:
            response = self.client.images.generate(
                model=self.settings.openai_image_model,
                prompt=rendered_prompt,
                size=size,
                quality=self.settings.openai_image_quality,
            )
            if not response.data:
                raise RuntimeError("OpenAI image response did not contain any image data.")
            image_data = response.data[0].b64_json
            if not image_data:
                raise RuntimeError("OpenAI image response was missing b64_json data.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(image_data))
            return output_path

        return retry_operation(
            _call,
            max_attempts=self.settings.max_retry_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
            logger=logger,
            operation_name="openai_image_generation",
        )

    @staticmethod
    def _image_size(resolution: tuple[int, int]) -> str:
        return "1024x1536" if resolution[1] > resolution[0] else "1536x1024"

    @staticmethod
    def compose_prompt(prompt: PromptSpec, resolution: tuple[int, int]) -> str:
        orientation = (
            "vertical 9:16 composition with strong foreground/background separation"
            if resolution[1] > resolution[0]
            else "horizontal 16:9 cinematic composition with rich environmental depth"
        )
        continuity = (
            "Character continuity rules: if a lion appears, render a majestic golden Indian lion with a dark chestnut mane, "
            "expressive amber eyes, and premium animated-film proportions. If a rabbit or hare appears, render a small ivory rabbit "
            "with pink inner ears, bright alert eyes, and a calm clever expression."
        )
        safety = (
            "Child-safe Panchatantra animated film keyframe. No gore, no horror, no text, no subtitles, no logos, no watermark, "
            "no UI, no split panels, no photoreal humans."
        )
        style = (
            "Indian forest aesthetic, warm cinematic lighting, premium family-animation look, detailed foliage, atmospheric depth, "
            "volumetric light, layered foreground elements, expressive posing, clear silhouettes, emotionally readable staging."
        )
        return (
            f"{style} {orientation}. {continuity} {safety} "
            f"Scene objective: {prompt.prompt}"
        )


class PlaceholderImageProvider(BaseImageProvider):
    def generate_scene_image(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        Illustrator().render_scene_image(output_path, prompt.prompt, resolution)
        return output_path


class ImageAnimatedVideoProvider(BaseVideoProvider):
    def __init__(self, settings: Settings, image_provider: BaseImageProvider, illustrator: Illustrator | None = None) -> None:
        self.settings = settings
        self.image_provider = image_provider
        self.illustrator = illustrator or Illustrator()

    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        image_path = output_path.with_suffix(".png")
        self.image_provider.generate_scene_image(prompt=prompt, output_path=image_path, resolution=resolution)
        self._image_to_video(image_path, output_path, resolution, prompt)
        return output_path

    def _image_to_video(
        self,
        image_path: Path,
        output_path: Path,
        resolution: tuple[int, int],
        prompt: PromptSpec,
    ) -> None:
        duration_seconds = prompt.duration_seconds
        frames = max(int(round(duration_seconds * 24)), 24)
        motion_plan = self._motion_plan(prompt, resolution)
        overlay_path = output_path.with_name(f"{output_path.stem}_overlay.png")
        self.illustrator.render_depth_overlay(overlay_path, prompt.prompt, resolution, prompt.priority)
        base_width, base_height = self._scaled_resolution(resolution, 1.24 if prompt.priority == "hero" else 1.16)
        overlay_width, overlay_height = self._scaled_resolution(resolution, 1.08 if prompt.priority == "hero" else 1.04)
        zoom_step = max((motion_plan.zoom_end - motion_plan.zoom_start) / max(frames - 1, 1), 0.0001)
        filter_chain = (
            f"[0:v]scale={base_width}:{base_height},"
            f"zoompan=z='if(lte(on,1),{motion_plan.zoom_start:.3f},min({motion_plan.zoom_end:.3f},zoom+{zoom_step:.5f}))':"
            f"x='(iw-iw/zoom)/2+({motion_plan.drift_x}*(on/{frames}))':"
            f"y='(ih-ih/zoom)/2+({motion_plan.drift_y}*(on/{frames}))':"
            f"d={frames}:s={resolution[0]}x{resolution[1]}:fps=24,"
            f"{motion_plan.color_treatment},"
            "unsharp=5:5:0.75:3:3:0.0,"
            "format=rgba[base];"
            f"[1:v]scale={overlay_width}:{overlay_height},format=rgba[overlay_src];"
            f"[base][overlay_src]overlay="
            f"x='({resolution[0]}-{overlay_width})/2+({motion_plan.overlay_shift_x}*(t/{max(duration_seconds, 1.5):.3f}))':"
            f"y='({resolution[1]}-{overlay_height})/2+({motion_plan.overlay_shift_y}*sin(t*0.8))':"
            f"format=auto,{motion_plan.finish_filter},format=yuv420p"
        )
        command = [
            self.settings.ffmpeg_binary,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-loop",
            "1",
            "-i",
            str(overlay_path),
            "-filter_complex",
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
            raise RuntimeError("FFmpeg is required for scene clip generation.") from exc

    @staticmethod
    def _scaled_resolution(resolution: tuple[int, int], factor: float) -> tuple[int, int]:
        width = max(int(round(resolution[0] * factor / 2) * 2), resolution[0])
        height = max(int(round(resolution[1] * factor / 2) * 2), resolution[1])
        return width, height

    def _motion_plan(self, prompt: PromptSpec, resolution: tuple[int, int]) -> MotionPlan:
        camera = prompt.prompt.lower()
        horizontal_bias = -1 if prompt.scene_number % 2 == 0 else 1
        is_vertical = resolution[1] > resolution[0]
        if prompt.priority == "hero":
            zoom_start = 1.04 if "wide" in camera else 1.07
            zoom_end = 1.18 if "dynamic" in camera or "close-up" in camera else 1.15
            drift_x = 42 * horizontal_bias
            drift_y = -14 if is_vertical else -8
            overlay_shift_x = -32 * horizontal_bias
            overlay_shift_y = 9
            color_treatment = "eq=saturation=1.16:contrast=1.08:brightness=0.025"
            finish_filter = "vignette=PI/5:mode=backward,noise=alls=7:allf=t"
        elif "dolly" in camera or "push" in camera:
            zoom_start = 1.02
            zoom_end = 1.11
            drift_x = 24 * horizontal_bias
            drift_y = -8 if is_vertical else -5
            overlay_shift_x = -18 * horizontal_bias
            overlay_shift_y = 6
            color_treatment = "eq=saturation=1.11:contrast=1.04:brightness=0.012"
            finish_filter = "vignette=PI/5.8:mode=backward,noise=alls=4:allf=t"
        elif "reveal" in camera or "wide" in camera:
            zoom_start = 1.0
            zoom_end = 1.07
            drift_x = 30 * horizontal_bias
            drift_y = -4
            overlay_shift_x = -14 * horizontal_bias
            overlay_shift_y = 4
            color_treatment = "eq=saturation=1.09:contrast=1.03"
            finish_filter = "vignette=PI/6:mode=backward,noise=alls=3:allf=t"
        else:
            zoom_start = 1.01
            zoom_end = 1.09
            drift_x = 18 * horizontal_bias
            drift_y = -6 if is_vertical else -4
            overlay_shift_x = -12 * horizontal_bias
            overlay_shift_y = 5
            color_treatment = "eq=saturation=1.10:contrast=1.03"
            finish_filter = "vignette=PI/6:mode=backward,noise=alls=3:allf=t"
        return MotionPlan(
            zoom_start=zoom_start,
            zoom_end=zoom_end,
            drift_x=drift_x,
            drift_y=drift_y,
            overlay_shift_x=overlay_shift_x,
            overlay_shift_y=overlay_shift_y,
            color_treatment=color_treatment,
            finish_filter=finish_filter,
        )


class Illustrator:
    def render_scene_image(self, image_path: Path, prompt_text: str, resolution: tuple[int, int]) -> None:
        width, height = resolution
        seed = sum(ord(char) for char in prompt_text)
        random.seed(seed)
        palette = self._pick_palette(prompt_text)

        canvas = Image.new("RGBA", (width, height), (*palette["sky_top"], 255))
        background = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        midground = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        characters = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        highlights = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        self._paint_gradient(ImageDraw.Draw(canvas), width, height, palette["sky_top"], palette["sky_bottom"])
        self._draw_light_source(ImageDraw.Draw(background), width, height, palette, prompt_text)
        self._draw_distant_hills(ImageDraw.Draw(background), width, height, palette)
        self._draw_forest_layers(ImageDraw.Draw(midground), width, height, palette)
        self._draw_ground(ImageDraw.Draw(midground), width, height, palette)
        self._draw_ground_texture(ImageDraw.Draw(midground), width, height, palette)
        self._draw_scene_feature(ImageDraw.Draw(midground), width, height, palette, prompt_text)
        self._draw_character_shadows(ImageDraw.Draw(midground), width, height, prompt_text)
        self._draw_characters(ImageDraw.Draw(characters), width, height, palette, prompt_text)
        self._draw_light_rays(highlights, width, height, palette, prompt_text)
        self._draw_atmosphere(highlights, width, height, palette)
        self._draw_edge_framing(ImageDraw.Draw(highlights), width, height, palette)

        canvas.alpha_composite(background.filter(ImageFilter.GaussianBlur(radius=0.8)))
        canvas.alpha_composite(midground)
        canvas.alpha_composite(characters.filter(ImageFilter.GaussianBlur(radius=0.25)))
        canvas.alpha_composite(highlights.filter(ImageFilter.GaussianBlur(radius=0.6)))
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.25))
        canvas = ImageEnhance.Color(canvas).enhance(1.08)
        canvas = ImageEnhance.Contrast(canvas).enhance(1.05)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(image_path)

    def render_depth_overlay(
        self,
        image_path: Path,
        prompt_text: str,
        resolution: tuple[int, int],
        priority: str,
    ) -> None:
        width, height = resolution
        palette = self._pick_palette(prompt_text)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        self._draw_foreground_foliage(overlay_draw, width, height, palette, prompt_text, priority)
        self._draw_depth_particles(overlay_draw, width, height, palette, priority)
        self._draw_soft_vignette(overlay, width, height)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(image_path)

    def render_title_card_image(self, image_path: Path, text: str, resolution: tuple[int, int]) -> None:
        width, height = resolution
        canvas = Image.new("RGBA", (width, height), color=(20, 36, 28, 255))
        draw = ImageDraw.Draw(canvas)
        self._paint_gradient(draw, width, height, (22, 45, 39), (161, 107, 57))
        self._draw_distant_hills(draw, width, height, self._pick_palette("forest"))
        self._draw_forest_layers(draw, width, height, self._pick_palette("forest"))
        self._draw_light_rays(canvas, width, height, self._pick_palette("forest"), "golden dawn forest")
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

    @staticmethod
    def _draw_ground_texture(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        for index in range(14):
            x = int((index * 97) % width)
            y = int(height * 0.78) + (index % 4) * 18
            draw.arc(
                (x, y, x + int(width * 0.1), y + int(height * 0.08)),
                start=180,
                end=360,
                fill=tuple(max(channel - 18, 0) for channel in palette["ground"]),
                width=4,
            )
        for index in range(22):
            x = int((index * 53) % width)
            y = int(height * 0.74) + (index % 6) * 20
            draw.line(
                (x, y, x + 12, y - 20),
                fill=(91, 121, 69, 160),
                width=3,
            )

    @staticmethod
    def _draw_character_shadows(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        prompt_text: str,
    ) -> None:
        lowered = prompt_text.lower()
        shadow_color = (38, 28, 20, 72)
        if "lion" in lowered:
            draw.ellipse((int(width * 0.12), int(height * 0.74), int(width * 0.43), int(height * 0.83)), fill=shadow_color)
        if "rabbit" in lowered or "hare" in lowered:
            draw.ellipse((int(width * 0.61), int(height * 0.8), int(width * 0.79), int(height * 0.87)), fill=shadow_color)

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

    def _draw_light_rays(
        self,
        canvas: Image.Image,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        prompt_text: str,
    ) -> None:
        lowered = prompt_text.lower()
        if any(word in lowered for word in ("night", "moon", "dark")):
            return
        rays = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(rays)
        for index in range(4):
            left = int(width * (0.12 + index * 0.16))
            draw.polygon(
                [
                    (left, 0),
                    (left + int(width * 0.08), 0),
                    (left + int(width * 0.2), int(height * 0.9)),
                    (left - int(width * 0.02), int(height * 0.9)),
                ],
                fill=(*palette["accent"], 24 if index % 2 else 18),
            )
        canvas.alpha_composite(rays.filter(ImageFilter.GaussianBlur(radius=18)))

    @staticmethod
    def _draw_edge_framing(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
    ) -> None:
        leaf_color = (*palette["canopy_dark"], 120)
        draw.ellipse((-int(width * 0.18), int(height * 0.02), int(width * 0.18), int(height * 0.34)), fill=leaf_color)
        draw.ellipse((int(width * 0.82), int(height * 0.06), int(width * 1.08), int(height * 0.38)), fill=leaf_color)
        draw.ellipse((int(width * 0.72), int(height * 0.72), int(width * 1.05), int(height * 1.04)), fill=(*palette["ground"], 72))

    @staticmethod
    def _draw_foreground_foliage(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        prompt_text: str,
        priority: str,
    ) -> None:
        density = 6 if priority == "hero" else 4
        alpha = 156 if priority == "hero" else 112
        for index in range(density):
            span = int(width * (0.18 if index % 2 else 0.13))
            x = -int(width * 0.08) + index * int(width * 0.08)
            y = int(height * (0.05 + index * 0.04))
            draw.polygon(
                [
                    (x, y + span),
                    (x + span, y),
                    (x + span * 2, y + span * 2),
                ],
                fill=(*palette["canopy_dark"], alpha),
            )
        for index in range(density):
            span = int(width * 0.12)
            x = int(width * 0.78) + index * int(width * 0.05)
            y = int(height * (0.02 + index * 0.05))
            draw.ellipse(
                (x, y, x + span, y + int(height * 0.18)),
                fill=(*palette["canopy_mid"], alpha - 18),
            )
        if "well" in prompt_text.lower():
            draw.ellipse(
                (int(width * 0.48), int(height * 0.75), int(width * 0.92), int(height * 1.02)),
                fill=(18, 31, 42, 52),
            )

    @staticmethod
    def _draw_depth_particles(
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        palette: dict[str, tuple[int, int, int]],
        priority: str,
    ) -> None:
        count = 48 if priority == "hero" else 24
        for index in range(count):
            x = int((index * 149) % width)
            y = int((index * 89) % height)
            radius = 4 + (index % 3) * 2
            alpha = 42 if priority == "hero" else 24
            draw.ellipse(
                (x, y, x + radius, y + radius),
                fill=(*palette["accent"], alpha),
            )

    @staticmethod
    def _draw_soft_vignette(
        canvas: Image.Image,
        width: int,
        height: int,
    ) -> None:
        vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(vignette)
        draw.ellipse(
            (-int(width * 0.18), -int(height * 0.14), int(width * 1.18), int(height * 1.12)),
            outline=(12, 14, 18, 56),
            width=int(min(width, height) * 0.08),
        )
        canvas.alpha_composite(vignette.filter(ImageFilter.GaussianBlur(radius=26)))

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
        self.illustrator = Illustrator()
        self.placeholder_image_provider = PlaceholderImageProvider()
        self.image_provider: BaseImageProvider = (
            OpenAIImageProvider(settings)
            if settings.openai_api_key and settings.use_openai_image_generation
            else self.placeholder_image_provider
        )
        self.animated_image_provider = ImageAnimatedVideoProvider(settings, self.image_provider, self.illustrator)
        self.openai_video_provider = OpenAIVideoProvider(settings)

    def generate_scene_clip(
        self,
        *,
        prompt: PromptSpec,
        output_path: Path,
        resolution: tuple[int, int],
    ) -> Path:
        if prompt.generation_mode == "video_ai" and self.settings.openai_api_key and self.settings.use_openai_video_generation:
            try:
                return self.openai_video_provider.generate_scene_clip(
                    prompt=prompt,
                    output_path=output_path,
                    resolution=resolution,
                )
            except Exception as exc:
                logger.warning("OpenAI video generation failed, falling back to animated scene images: %s", exc)
        try:
            return self.animated_image_provider.generate_scene_clip(
                prompt=prompt,
                output_path=output_path,
                resolution=resolution,
            )
        except Exception as exc:
            logger.warning("Primary image provider failed, falling back to local illustrated scenes: %s", exc)
            fallback_provider = ImageAnimatedVideoProvider(self.settings, self.placeholder_image_provider, self.illustrator)
            return fallback_provider.generate_scene_clip(prompt=prompt, output_path=output_path, resolution=resolution)
