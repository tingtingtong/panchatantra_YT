from __future__ import annotations

from app.config import Settings
from app.schemas import PromptSpec
from app.services.video_generation_service import (
    ImageAnimatedVideoProvider,
    OpenAIImageProvider,
    PlaceholderImageProvider,
    VideoGenerationService,
)


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
    assert "volumetric light" in rendered
    assert "layered foreground elements" in rendered


def test_video_generation_service_prefers_openai_images_when_enabled() -> None:
    settings = Settings(openai_api_key="test-key", use_openai_image_generation=True, use_openai_video_generation=False)

    service = VideoGenerationService(settings)

    assert isinstance(service.image_provider, OpenAIImageProvider)


def test_video_generation_service_falls_back_to_placeholder_without_key() -> None:
    settings = Settings(openai_api_key=None, use_openai_image_generation=True, use_openai_video_generation=False)

    service = VideoGenerationService(settings)

    assert isinstance(service.image_provider, PlaceholderImageProvider)


def test_hero_motion_plan_is_stronger_than_supporting_motion() -> None:
    provider = ImageAnimatedVideoProvider(Settings(), PlaceholderImageProvider())
    hero_prompt = PromptSpec(
        scene_number=1,
        duration_seconds=4.0,
        prompt="Scene 1. Hero confrontation. Camera: dynamic close-up with cinematic parallax.",
        priority="hero",
    )
    supporting_prompt = PromptSpec(
        scene_number=2,
        duration_seconds=4.0,
        prompt="Scene 2. Setup in the forest. Camera: measured cinematic dolly through forest depth.",
        priority="supporting",
    )

    hero_motion = provider._motion_plan(hero_prompt, (1080, 1920))
    supporting_motion = provider._motion_plan(supporting_prompt, (1080, 1920))

    assert hero_motion.zoom_end > supporting_motion.zoom_end
    assert abs(hero_motion.drift_x) >= abs(supporting_motion.drift_x)
    assert "noise=alls=7" in hero_motion.finish_filter
