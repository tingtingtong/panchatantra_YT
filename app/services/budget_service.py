from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.models import FormatType
from app.schemas import BudgetFormatPlan, BudgetPlanResponse, StoryAssetBundle


@dataclass(slots=True)
class BudgetTemplate:
    format_type: str
    target_count_per_month: int
    still_images_per_video: int
    hero_video_seconds_per_video: int
    recommendation: str


class BudgetService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_plan(self) -> BudgetPlanResponse:
        monthly_budget_usd = self.settings.monthly_budget_inr / self.settings.usd_to_inr_rate
        available_production_budget_usd = max(
            monthly_budget_usd - self.settings.monthly_llm_reserve_usd - self.settings.monthly_contingency_reserve_usd,
            0.0,
        )

        shorts_plan = self._build_format_plan(self._template_for_format(FormatType.SHORT))
        full_plan = self._build_format_plan(self._template_for_format(FormatType.FULL))

        projected_total_usd = shorts_plan.total_monthly_cost_usd + full_plan.total_monthly_cost_usd
        remaining_usd = available_production_budget_usd - projected_total_usd
        notes = [
            "This plan assumes OpenAI image generation for stills and Runway Gen-4 Turbo for selected hero shots.",
            "If the channel has no traction yet, do not raise hero-shot seconds until shorts retention improves.",
            "If monthly spend exceeds target, reduce full videos first before reducing shorts cadence.",
        ]
        return BudgetPlanResponse(
            monthly_budget_inr=self.settings.monthly_budget_inr,
            monthly_budget_usd=round(monthly_budget_usd, 2),
            usd_to_inr_rate=self.settings.usd_to_inr_rate,
            llm_reserve_usd=self.settings.monthly_llm_reserve_usd,
            contingency_reserve_usd=self.settings.monthly_contingency_reserve_usd,
            available_production_budget_usd=round(available_production_budget_usd, 2),
            available_production_budget_inr=round(available_production_budget_usd * self.settings.usd_to_inr_rate, 2),
            shorts=shorts_plan,
            full=full_plan,
            projected_total_usd=round(projected_total_usd, 2),
            projected_total_inr=round(projected_total_usd * self.settings.usd_to_inr_rate, 2),
            remaining_usd=round(remaining_usd, 2),
            remaining_inr=round(remaining_usd * self.settings.usd_to_inr_rate, 2),
            notes=notes,
        )

    def recommend_generation_mode(self, format_type: FormatType, scene_index: int, total_scenes: int) -> str:
        hero_indexes = self._hero_scene_indexes(format_type, total_scenes)
        return "video_ai" if scene_index in hero_indexes else "image_motion"

    def recommend_priority(self, format_type: FormatType, scene_index: int, total_scenes: int) -> str:
        return "hero" if self.recommend_generation_mode(format_type, scene_index, total_scenes) == "video_ai" else "supporting"

    def hero_video_seconds_per_video(self, format_type: FormatType) -> int:
        template = self._template_for_format(format_type)
        return template.hero_video_seconds_per_video

    def recommend_generation_mode_for_shot(
        self,
        format_type: FormatType,
        scene_index: int,
        total_scenes: int,
        duration_seconds: float,
        allocated_hero_seconds: float,
    ) -> str:
        if self.recommend_generation_mode(format_type, scene_index, total_scenes) != "video_ai":
            return "image_motion"
        hero_limit = float(self.hero_video_seconds_per_video(format_type))
        if allocated_hero_seconds >= hero_limit:
            return "image_motion"
        remaining = hero_limit - allocated_hero_seconds
        if duration_seconds > remaining + 0.25:
            return "image_motion"
        return "video_ai"

    def estimate_scene_cost_usd(self, format_type: FormatType, generation_mode: str) -> float:
        if generation_mode == "video_ai":
            return self.settings.budget_runway_gen4_turbo_usd_per_second
        if format_type == FormatType.SHORT:
            return self.settings.budget_openai_image_high_usd
        return self.settings.budget_openai_image_high_usd

    def estimate_bundle_cost_usd(self, bundle: StoryAssetBundle) -> float:
        total = 0.0
        for prompt in bundle.scene_prompts:
            if prompt.generation_mode == "video_ai":
                total += prompt.duration_seconds * self.settings.budget_runway_gen4_turbo_usd_per_second
            else:
                total += self.settings.budget_openai_image_high_usd
        return round(total, 2)

    def allowed_bundle_cost_usd(self, format_type: FormatType) -> float:
        plan = self.build_plan()
        target = plan.shorts if format_type == FormatType.SHORT else plan.full
        return round(target.total_cost_per_video_usd + self.settings.budget_story_tolerance_usd, 2)

    def _build_format_plan(self, template: BudgetTemplate) -> BudgetFormatPlan:
        image_cost_per_video = template.still_images_per_video * self.settings.budget_openai_image_high_usd
        hero_video_cost_per_video = template.hero_video_seconds_per_video * self.settings.budget_runway_gen4_turbo_usd_per_second
        total_cost_per_video = image_cost_per_video + hero_video_cost_per_video
        total_monthly_cost = total_cost_per_video * template.target_count_per_month
        return BudgetFormatPlan(
            format_type=template.format_type,
            target_count_per_month=template.target_count_per_month,
            still_images_per_video=template.still_images_per_video,
            hero_video_seconds_per_video=template.hero_video_seconds_per_video,
            image_cost_per_video_usd=round(image_cost_per_video, 2),
            hero_video_cost_per_video_usd=round(hero_video_cost_per_video, 2),
            total_cost_per_video_usd=round(total_cost_per_video, 2),
            total_monthly_cost_usd=round(total_monthly_cost, 2),
            total_monthly_cost_inr=round(total_monthly_cost * self.settings.usd_to_inr_rate, 2),
            recommendation=template.recommendation,
        )

    def _template_for_format(self, format_type: FormatType) -> BudgetTemplate:
        if format_type == FormatType.SHORT:
            return BudgetTemplate(
                format_type=FormatType.SHORT.value,
                target_count_per_month=self.settings.monthly_shorts_target,
                still_images_per_video=8,
                hero_video_seconds_per_video=6,
                recommendation=(
                    "Use AI images for most shots. Reserve paid video generation for the first 2 seconds, one reaction shot, "
                    "and the climax jump into the well."
                ),
            )
        return BudgetTemplate(
            format_type=FormatType.FULL.value,
            target_count_per_month=self.settings.monthly_full_videos_target,
            still_images_per_video=20,
            hero_video_seconds_per_video=12,
            recommendation=(
                "Keep the full video mostly image-motion based. Spend paid video seconds only on the hook, confrontation, "
                "and climax."
            ),
        )

    @staticmethod
    def _hero_scene_indexes(format_type: FormatType, total_scenes: int) -> set[int]:
        if total_scenes <= 0:
            return set()
        if format_type == FormatType.SHORT:
            base = {0, max(total_scenes // 2, 1), total_scenes - 2 if total_scenes > 2 else total_scenes - 1}
            return {index for index in base if 0 <= index < total_scenes}
        base = {0, max(total_scenes // 2, 1), total_scenes - 3 if total_scenes > 3 else total_scenes - 1}
        return {index for index in base if 0 <= index < total_scenes}
