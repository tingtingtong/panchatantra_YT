from __future__ import annotations

from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

from app.models import FormatType
from app.schemas import StoryAssetBundle


class ThumbnailService:
    def create_thumbnail(self, bundle: StoryAssetBundle, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas = Image.new("RGB", (1280, 720), color=(18, 51, 39))
        draw = ImageDraw.Draw(canvas)

        for band, color in enumerate([(31, 82, 64), (161, 106, 55), (226, 188, 110)]):
            draw.rectangle((0, band * 240, 1280, (band + 1) * 240), fill=color)

        title_font = self._load_font(44)
        tag_font = self._load_font(20)

        draw.rounded_rectangle((70, 70, 1210, 650), radius=36, fill=(12, 25, 31))
        draw.text((110, 110), "PANCHATANTRA", fill=(245, 224, 166), font=tag_font)
        y = 220
        for line in wrap(bundle.thumbnail.headline, width=22):
            draw.text((110, y), line, fill=(255, 248, 226), font=title_font)
            y += 70
        draw.text((110, 580), bundle.format_type.value.upper(), fill=(245, 224, 166), font=tag_font)
        canvas.save(output_path, format="PNG")
        return output_path

    @staticmethod
    def default_path(base_dir: Path, story_id: str, language: str, format_type: FormatType) -> Path:
        return base_dir / f"{story_id}_{language}_{format_type.value}.png"

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for font_name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except OSError:
                continue
        return ImageFont.load_default()
