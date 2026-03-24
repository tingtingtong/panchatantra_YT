from __future__ import annotations

from app.config import Settings
from app.models import FormatType
from app.schemas import MetadataSpec, PromptSpec, ShotSpec, StoryAssetBundle, SubtitleLine, ThumbnailSpec
from app.services.budget_service import BudgetService
from app.services.pipeline_service import PipelineService


def _build_bundle(
    format_type: FormatType,
    prompts: list[PromptSpec],
) -> StoryAssetBundle:
    return StoryAssetBundle(
        story_id="lion-rabbit",
        language="en",
        format_type=format_type,
        target_duration_seconds=45 if format_type == FormatType.SHORT else 360,
        style_notes=["Indian forest aesthetic", "child-safe visuals"],
        script="A clever rabbit tricks a lion into leaping into a well.",
        shot_list=[
            ShotSpec(
                scene_number=prompt.scene_number,
                duration_seconds=prompt.duration_seconds,
                visual_summary=f"Scene {prompt.scene_number}",
                camera_direction="slow push-in",
                emotion="tense",
                generation_mode=prompt.generation_mode,
                priority=prompt.priority,
                estimated_cost_usd=prompt.estimated_cost_usd,
            )
            for prompt in prompts
        ],
        scene_prompts=prompts,
        subtitles=[SubtitleLine(start=0.0, end=2.0, text="A clever rabbit saves the forest.")],
        thumbnail=ThumbnailSpec(
            headline="Rabbit Outsmarts Lion",
            prompt="Animated Panchatantra thumbnail with lion, rabbit, and well.",
        ),
        metadata=MetadataSpec(
            title="The Lion and the Clever Rabbit",
            description="Original Panchatantra retelling.",
            tags=["Panchatantra", "Lion", "Rabbit"],
            cta_lines=["Subscribe for more Panchatantra stories."],
        ),
    )


def test_budget_plan_fits_under_3000_inr_target() -> None:
    settings = Settings(
        monthly_budget_inr=3000,
        usd_to_inr_rate=91.0,
        monthly_shorts_target=4,
        monthly_full_videos_target=1,
        monthly_llm_reserve_usd=4.0,
        monthly_contingency_reserve_usd=4.0,
        budget_openai_image_high_usd=0.052,
        budget_runway_gen4_turbo_usd_per_second=0.05,
    )
    service = BudgetService(settings)

    plan = service.build_plan()

    assert plan.projected_total_inr < plan.available_production_budget_inr
    assert plan.shorts.target_count_per_month == 4
    assert plan.full.target_count_per_month == 1
    assert plan.remaining_inr > 0


def test_budget_service_marks_hero_shots_for_shorts() -> None:
    service = BudgetService(Settings())

    priorities = [
        service.recommend_priority(FormatType.SHORT, index, 6)
        for index in range(6)
    ]

    assert priorities.count("hero") >= 2
    assert priorities[0] == "hero"
    assert priorities[-1] == "supporting"


def test_budget_service_estimates_cost_modes() -> None:
    settings = Settings()
    service = BudgetService(settings)

    image_cost = service.estimate_scene_cost_usd(FormatType.FULL, "image_motion")
    video_cost = service.estimate_scene_cost_usd(FormatType.FULL, "video_ai")

    assert image_cost == settings.budget_openai_image_high_usd
    assert video_cost == settings.budget_runway_gen4_turbo_usd_per_second


def test_budget_service_downgrades_hero_shot_when_duration_exceeds_budget() -> None:
    service = BudgetService(Settings())

    mode = service.recommend_generation_mode_for_shot(
        format_type=FormatType.SHORT,
        scene_index=0,
        total_scenes=6,
        duration_seconds=7.5,
        allocated_hero_seconds=0.0,
    )

    assert mode == "image_motion"


def test_budget_service_estimates_bundle_cost_with_hero_and_supporting_shots() -> None:
    settings = Settings(
        budget_openai_image_high_usd=0.052,
        budget_runway_gen4_turbo_usd_per_second=0.05,
    )
    service = BudgetService(settings)
    bundle = _build_bundle(
        FormatType.SHORT,
        [
            PromptSpec(scene_number=1, duration_seconds=3.0, prompt="Hero hook", generation_mode="video_ai", priority="hero"),
            PromptSpec(scene_number=2, duration_seconds=4.0, prompt="Supporting beat", generation_mode="image_motion", priority="supporting"),
            PromptSpec(scene_number=3, duration_seconds=3.0, prompt="Hero climax", generation_mode="video_ai", priority="hero"),
        ],
    )

    estimated = service.estimate_bundle_cost_usd(bundle)

    assert estimated == 0.35


def test_pipeline_service_blocks_render_when_bundle_exceeds_budget(session) -> None:
    settings = Settings(
        enforce_story_budget=True,
        budget_story_tolerance_usd=0.0,
        budget_openai_image_high_usd=0.052,
        budget_runway_gen4_turbo_usd_per_second=0.05,
    )
    pipeline = PipelineService(session, settings)
    over_budget_bundle = _build_bundle(
        FormatType.SHORT,
        [
            PromptSpec(scene_number=1, duration_seconds=8.0, prompt="Hook", generation_mode="video_ai", priority="hero"),
            PromptSpec(scene_number=2, duration_seconds=8.0, prompt="Conflict", generation_mode="video_ai", priority="hero"),
            PromptSpec(scene_number=3, duration_seconds=8.0, prompt="Climax", generation_mode="video_ai", priority="hero"),
        ],
    )

    try:
        pipeline._enforce_bundle_budget(over_budget_bundle)
    except ValueError as exc:
        assert "exceeds the configured cap" in str(exc)
    else:
        raise AssertionError("Expected bundle budget enforcement to reject an over-budget render.")


def test_pipeline_service_allows_render_when_bundle_matches_budget(session) -> None:
    settings = Settings(
        enforce_story_budget=True,
        budget_story_tolerance_usd=0.0,
        budget_openai_image_high_usd=0.052,
        budget_runway_gen4_turbo_usd_per_second=0.05,
    )
    pipeline = PipelineService(session, settings)
    in_budget_bundle = _build_bundle(
        FormatType.SHORT,
        [
            PromptSpec(scene_number=1, duration_seconds=2.0, prompt="Hook", generation_mode="video_ai", priority="hero"),
            PromptSpec(scene_number=2, duration_seconds=4.0, prompt="Setup", generation_mode="image_motion", priority="supporting"),
            PromptSpec(scene_number=3, duration_seconds=2.0, prompt="Climax", generation_mode="video_ai", priority="hero"),
            PromptSpec(scene_number=4, duration_seconds=4.0, prompt="Moral", generation_mode="image_motion", priority="supporting"),
        ],
    )

    pipeline._enforce_bundle_budget(in_budget_bundle)
