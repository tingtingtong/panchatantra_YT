from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Story, StoryStatus


class StorySelector:
    """Selects the next story from the publishing queue."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def base_query(self):
        return select(Story).where(Story.status.in_([StoryStatus.QUEUED, StoryStatus.GENERATED]))

    def pick_next(self) -> Story | None:
        query = self.base_query().order_by(Story.publish_date.is_(None), Story.publish_date, Story.created_at)
        return self.session.execute(query).scalars().first()

    @staticmethod
    def next_weekly_slot(reference: datetime | None = None) -> datetime:
        current = reference or datetime.now(timezone.utc)
        return current + timedelta(days=7)
