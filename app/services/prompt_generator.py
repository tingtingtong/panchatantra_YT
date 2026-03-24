from __future__ import annotations

from app.models import FormatType, Story
from app.schemas import PromptSpec, ShotSpec


class PromptGenerator:
    style_notes = [
        "Indian forest aesthetic with banyan trees, sandstone textures, warm monsoon light",
        "cinematic storytelling with premium animation feel",
        "child-safe visuals with expressive animal faces and clean silhouettes",
        "high emotional clarity and readable character staging",
    ]

    negative_prompt = (
        "no gore, no horror, no photoreal violence, no weapons, no disturbing anatomy, "
        "no text artifacts, no low-resolution clutter"
    )

    def generate(self, story: Story, format_type: FormatType, language: str, shot_list: list[ShotSpec]) -> list[PromptSpec]:
        orientation = "vertical 9:16 composition, mobile-first framing" if format_type == FormatType.SHORT else "wide 16:9 composition, cinematic staging"
        prompts: list[PromptSpec] = []
        for shot in shot_list:
            text = (
                f"{story.title}. Scene {shot.scene_number}. {shot.visual_summary}. "
                f"Emotion: {shot.emotion}. Camera: {shot.camera_direction}. "
                f"{orientation}. {', '.join(self.style_notes)}. Language context: {language}."
            )
            prompts.append(
                PromptSpec(
                    scene_number=shot.scene_number,
                    duration_seconds=shot.duration_seconds,
                    prompt=text,
                    negative_prompt=self.negative_prompt,
                    generation_mode=shot.generation_mode,
                    priority=shot.priority,
                    estimated_cost_usd=shot.estimated_cost_usd,
                )
            )
        return prompts
