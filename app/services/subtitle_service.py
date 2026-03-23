from __future__ import annotations

from dataclasses import dataclass

from app.schemas import SubtitleLine


def _format_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


@dataclass(slots=True)
class SubtitleService:
    max_words_per_line: int = 8

    def build_timed_lines(self, sections: list[str], target_duration_seconds: int) -> list[SubtitleLine]:
        cleaned_sections = [section.strip() for section in sections if section.strip()]
        total_words = sum(max(len(section.split()), 1) for section in cleaned_sections) or 1
        current = 0.0
        result: list[SubtitleLine] = []
        for section in cleaned_sections:
            words = len(section.split()) or 1
            duration = max((words / total_words) * target_duration_seconds, 1.2)
            end = current + duration
            result.append(SubtitleLine(start=round(current, 2), end=round(end, 2), text=section))
            current = end
        if result:
            result[-1].end = float(target_duration_seconds)
        return result

    def scale_lines(self, lines: list[SubtitleLine], actual_duration_seconds: float) -> list[SubtitleLine]:
        if not lines:
            return []
        estimated_duration = lines[-1].end or actual_duration_seconds
        if estimated_duration <= 0:
            return lines
        scale = actual_duration_seconds / estimated_duration
        return [
            SubtitleLine(
                start=round(line.start * scale, 2),
                end=round(line.end * scale, 2),
                text=line.text,
            )
            for line in lines
        ]

    def to_srt(self, lines: list[SubtitleLine]) -> str:
        blocks = []
        for index, line in enumerate(lines, start=1):
            blocks.append(f"{index}\n{_format_timestamp(line.start)} --> {_format_timestamp(line.end)}\n{line.text}\n")
        return "\n".join(blocks)

    def parse_srt(self, srt_text: str) -> list[SubtitleLine]:
        blocks = [block.strip() for block in srt_text.strip().split("\n\n") if block.strip()]
        result: list[SubtitleLine] = []
        for block in blocks:
            lines = block.splitlines()
            if len(lines) < 3:
                continue
            timestamps = lines[1].split(" --> ")
            start = self._parse_timestamp(timestamps[0])
            end = self._parse_timestamp(timestamps[1])
            text = " ".join(lines[2:])
            result.append(SubtitleLine(start=start, end=end, text=text))
        return result

    @staticmethod
    def _parse_timestamp(value: str) -> float:
        hours, minutes, seconds, milliseconds = value.replace(",", ":").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
