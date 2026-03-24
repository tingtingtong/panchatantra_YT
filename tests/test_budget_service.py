from __future__ import annotations

from app.config import Settings
from app.models import FormatType
from app.services.budget_service import BudgetService


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
