from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Story, StoryStatus
from app.services.story_selector import StorySelector


def test_pick_next_prefers_earliest_publish_date(session) -> None:
    now = datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)
    later_story = Story(
        id="blue-jackal",
        title="The Blue Jackal",
        moral="Deception collapses under pressure.",
        source_summary="A jackal dyed blue pretends to be king.",
        language="en",
        status=StoryStatus.QUEUED,
        publish_date=now + timedelta(days=9),
        formats_needed=["short", "full"],
    )
    earlier_story = Story(
        id="lion-rabbit",
        title="The Lion and the Clever Rabbit",
        moral="Intelligence and patience can defeat brute strength.",
        source_summary="A rabbit outsmarts a lion.",
        language="en",
        status=StoryStatus.GENERATED,
        publish_date=now + timedelta(days=2),
        formats_needed=["short", "full"],
    )
    no_date_story = Story(
        id="crow-serpent",
        title="The Crow and the Serpent",
        moral="Persistence and planning win.",
        source_summary="A crow defeats a serpent through strategy.",
        language="en",
        status=StoryStatus.QUEUED,
        publish_date=None,
        formats_needed=["short", "full"],
    )
    session.add_all([later_story, earlier_story, no_date_story])
    session.commit()

    selected = StorySelector(session).pick_next()

    assert selected is not None
    assert selected.id == "lion-rabbit"


def test_pick_next_ignores_non_queue_statuses(session) -> None:
    session.add_all(
        [
            Story(
                id="published-story",
                title="Published",
                moral="Done",
                source_summary="Already live",
                language="en",
                status=StoryStatus.PUBLISHED,
                formats_needed=["short", "full"],
            ),
            Story(
                id="queued-story",
                title="Queued",
                moral="Pending",
                source_summary="Ready next",
                language="en",
                status=StoryStatus.QUEUED,
                formats_needed=["short", "full"],
            ),
        ]
    )
    session.commit()

    selected = StorySelector(session).pick_next()

    assert selected is not None
    assert selected.id == "queued-story"


def test_next_weekly_slot_adds_exactly_seven_days() -> None:
    reference = datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)

    result = StorySelector.next_weekly_slot(reference)

    assert result == reference + timedelta(days=7)
