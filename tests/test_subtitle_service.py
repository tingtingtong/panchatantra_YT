from __future__ import annotations

import pytest

from app.schemas import SubtitleLine
from app.services.subtitle_service import SubtitleService


def test_build_timed_lines_reaches_target_duration() -> None:
    service = SubtitleService()

    lines = service.build_timed_lines(
        [
            "A proud lion frightened the entire forest.",
            "A rabbit used patience and timing to outsmart him.",
            "The forest was safe again.",
        ],
        target_duration_seconds=45,
    )

    assert len(lines) == 3
    assert lines[0].start == 0.0
    assert lines[-1].end == pytest.approx(45.0)
    assert all(line.end > line.start for line in lines)


def test_srt_round_trip_preserves_text_and_timestamps() -> None:
    service = SubtitleService()
    original = [
        SubtitleLine(start=0.0, end=2.5, text="First line"),
        SubtitleLine(start=2.5, end=5.0, text="Second line"),
    ]

    srt_text = service.to_srt(original)
    parsed = service.parse_srt(srt_text)

    assert parsed == original


def test_scale_lines_adjusts_to_actual_duration() -> None:
    service = SubtitleService()
    lines = [
        SubtitleLine(start=0.0, end=5.0, text="Intro"),
        SubtitleLine(start=5.0, end=10.0, text="Ending"),
    ]

    scaled = service.scale_lines(lines, actual_duration_seconds=20.0)

    assert scaled[0].start == 0.0
    assert scaled[0].end == 10.0
    assert scaled[1].end == 20.0
