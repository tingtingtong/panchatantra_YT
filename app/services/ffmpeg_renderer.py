from __future__ import annotations

import logging
import math
import subprocess
import wave
from pathlib import Path

from app.config import Settings
from app.models import FormatType
from app.schemas import StoryAssetBundle, SubtitleLine
from app.services.video_generation_service import VideoGenerationService

logger = logging.getLogger(__name__)


class FFmpegRenderer:
    def __init__(self, settings: Settings, video_generation_service: VideoGenerationService) -> None:
        self.settings = settings
        self.video_generation_service = video_generation_service

    def render(
        self,
        *,
        bundle: StoryAssetBundle,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
        burn_subtitles: bool,
    ) -> Path:
        resolution = (
            self.settings.default_short_resolution
            if bundle.format_type == FormatType.SHORT
            else self.settings.default_full_resolution
        )
        work_dir = output_path.parent / f"{bundle.story_id}_{bundle.language}_{bundle.format_type.value}_render"
        work_dir.mkdir(parents=True, exist_ok=True)

        clip_paths = [
            self.video_generation_service.generate_scene_clip(
                prompt=prompt,
                output_path=work_dir / f"scene_{prompt.scene_number:02}.mp4",
                resolution=resolution,
            )
            for prompt in bundle.scene_prompts
        ]
        intro_clip = self._create_title_card(
            text=bundle.metadata.title,
            output_path=work_dir / "intro.mp4",
            resolution=resolution,
            duration=self.settings.intro_duration_seconds,
        )
        outro_clip = self._create_title_card(
            text=bundle.metadata.cta_lines[0] if bundle.metadata.cta_lines else "Subscribe for more Panchatantra stories",
            output_path=work_dir / "outro.mp4",
            resolution=resolution,
            duration=self.settings.outro_duration_seconds,
        )
        concat_file = work_dir / "clips.txt"
        concat_file.write_text(
            "\n".join([f"file '{path.as_posix()}'" for path in [intro_clip, *clip_paths, outro_clip]]),
            encoding="utf-8",
        )
        silent_video = work_dir / "base_video.mp4"
        self._run(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(silent_video),
            ]
        )

        background_music = work_dir / "ambient.wav"
        self._create_background_music(background_music, bundle.target_duration_seconds + 12)

        command = [
            self.settings.ffmpeg_binary,
            "-y",
            "-i",
            str(audio_path),
            "-i",
            str(background_music),
            "-i",
            str(silent_video),
            "-filter_complex",
            (
                f"[1:a]volume={self.settings.default_music_volume}[music];"
                "[0:a][music]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=350[mixed]"
            ),
        ]

        if burn_subtitles:
            subtitle_filter = subtitle_path.as_posix().replace(":", "\\:")
            command.extend(
                [
                    "-vf",
                    f"subtitles='{subtitle_filter}':force_style='FontName=Arial,FontSize=22,BorderStyle=3,Outline=1'",
                ]
            )

        command.extend(
            [
                "-map",
                "2:v:0",
                "-map",
                "[mixed]",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(output_path),
            ]
        )
        self._run(command)
        return output_path

    def probe_duration(self, media_path: Path) -> float:
        command = [
            self.settings.ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        return float(completed.stdout.strip())

    def _create_title_card(
        self,
        *,
        text: str,
        output_path: Path,
        resolution: tuple[int, int],
        duration: float,
    ) -> Path:
        image_path = output_path.with_suffix(".png")
        self.video_generation_service.illustrator.render_title_card_image(image_path, text, resolution)
        self._run(
            [
                self.settings.ffmpeg_binary,
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                str(duration),
                "-vf",
                f"scale={resolution[0]}:{resolution[1]},format=yuv420p",
                "-c:v",
                "libx264",
                str(output_path),
            ]
        )
        return output_path

    def _create_background_music(self, path: Path, duration_seconds: int) -> None:
        sample_rate = 22050
        total_frames = sample_rate * duration_seconds
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            frames = bytearray()
            for frame in range(total_frames):
                t = frame / sample_rate
                harmonic = math.sin(2 * math.pi * 110 * t) + 0.4 * math.sin(2 * math.pi * 220 * t)
                sample = int(2500 * harmonic)
                frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
            wav_file.writeframes(bytes(frames))

    @staticmethod
    def write_subtitle_file(lines: list[SubtitleLine], path: Path) -> Path:
        from app.services.subtitle_service import SubtitleService

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SubtitleService().to_srt(lines), encoding="utf-8")
        return path

    def _run(self, command: list[str]) -> None:
        try:
            subprocess.run(command, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg/FFprobe is not installed or not available on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else str(exc.stderr)
            logger.error("FFmpeg command failed: %s", stderr)
            raise RuntimeError(stderr) from exc
