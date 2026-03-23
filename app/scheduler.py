from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db import session_scope
from app.services.pipeline_service import PipelineService

logger = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.timezone_name)

    def weekly_job() -> None:
        with session_scope() as session:
            result = PipelineService(session, settings).run_weekly_cycle()
            logger.info("Weekly scheduler result: %s", result)

    scheduler.add_job(
        weekly_job,
        "cron",
        id="weekly_panchatantra_publish",
        day_of_week=settings.weekly_schedule_day,
        hour=settings.weekly_schedule_hour,
        minute=settings.weekly_schedule_minute,
        replace_existing=True,
    )
    return scheduler
