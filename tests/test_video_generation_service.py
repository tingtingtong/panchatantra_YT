from __future__ import annotations

from app.config import Settings
from app.schemas import PromptSpec
from app.services.video_generation_service import OpenAIImageProvider, PlaceholderImageProvider, VideoGenerationService


def test_openai_image_prompt_enforces_visual_quality_rules() -> None:
    prompt = PromptSpec(
        scene_number=1,
        duration_seconds=4.0,
        prompt="The Lion and the Clever Rabbit. Scene 1. A proud lion stalks through a sunlit Indian forest while a small rabbit hides behind roots.",
        negative_prompt="",
    )

    rendered = OpenAIImageProvider.compose_prompt(prompt, (1080, 1920))

    assert "Child-safe Panchatantra animated film keyframe" in rendered
    assert "no text" in rendered.lower()
    assert "no subtitles" in rendered.lower()
    assert "no logos" in rendered.lower()
    assert "golden Indian lion" in rendered
    assert "small ivory rabbit" in rendered
    assert "vertical 9:16 composition" in rendered


def test_video_generation_service_prefers_openai_images_when_enabled() -> None:
    settings = Settings(openai_api_key="test-key", use_openai_image_generation=True, use_openai_video_generation=False)

    service = VideoGenerationService(settings)

    assert isinstance(service.image_provider, OpenAIImageProvider)


def test_video_generation_service_falls_back_to_placeholder_without_key() -> None:
    settings = Settings(openai_api_key=None, use_openai_image_generation=True, use_openai_video_generation=False)

    service = VideoGenerationService(settings)

    assert isinstance(service.image_provider, PlaceholderImageProvider)
