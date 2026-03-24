from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shutil

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Panchatantra Studio"
    environment: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'panchatantra.db').as_posix()}"
    log_level: str = "INFO"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_video_model: str = "sora-2"
    openai_image_model: str = "gpt-image-1"
    openai_image_quality: str = "high"
    openai_image_style: str = "vivid"
    use_openai_video_generation: bool = False
    use_openai_image_generation: bool = True
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"

    youtube_client_secrets_file: str = str(BASE_DIR / "data" / "youtube_client_secret.json")
    youtube_token_file: str = str(BASE_DIR / "data" / "youtube_token.json")
    youtube_channel_category_id: str = "24"

    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    burn_subtitles: bool = True
    default_music_volume: float = 0.16

    weekly_schedule_day: str = "mon"
    weekly_schedule_hour: int = 9
    weekly_schedule_minute: int = 0
    timezone_name: str = "Asia/Kolkata"

    n8n_webhook_secret: str | None = None
    max_retry_attempts: int = 3
    retry_base_delay_seconds: float = 1.5

    monthly_budget_inr: int = 3000
    usd_to_inr_rate: float = 91.0
    monthly_shorts_target: int = 4
    monthly_full_videos_target: int = 1
    monthly_llm_reserve_usd: float = 4.0
    monthly_contingency_reserve_usd: float = 4.0
    budget_openai_image_high_usd: float = 0.052
    budget_runway_gen4_turbo_usd_per_second: float = 0.05
    enforce_story_budget: bool = True
    budget_story_tolerance_usd: float = 0.5

    intro_duration_seconds: float = 3.0
    outro_duration_seconds: float = 4.0
    short_target_seconds: tuple[int, int] = Field(default=(35, 50))
    full_target_seconds: tuple[int, int] = Field(default=(300, 420))

    default_short_resolution: tuple[int, int] = Field(default=(1080, 1920))
    default_full_resolution: tuple[int, int] = Field(default=(1920, 1080))

    admin_page_title: str = "Panchatantra Channel Admin"
    content_seed_file: str = str(BASE_DIR / "data" / "seed_stories.json")
    asset_schema_file: str = str(BASE_DIR / "data" / "story_asset.schema.json")

    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def output_dir(self) -> Path:
        return BASE_DIR / "output"

    @property
    def shorts_dir(self) -> Path:
        return self.output_dir / "shorts"

    @property
    def full_dir(self) -> Path:
        return self.output_dir / "full"

    @property
    def audio_dir(self) -> Path:
        return self.output_dir / "audio"

    @property
    def subtitle_dir(self) -> Path:
        return self.output_dir / "subtitles"

    @property
    def thumbnail_dir(self) -> Path:
        return self.output_dir / "thumbnails"

    @property
    def logs_dir(self) -> Path:
        return self.output_dir / "logs"

    def ensure_directories(self) -> None:
        for path in (
            self.base_dir / "data",
            self.output_dir,
            self.shorts_dir,
            self.full_dir,
            self.audio_dir,
            self.subtitle_dir,
            self.thumbnail_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_binaries(self) -> None:
        self.ffmpeg_binary = self._resolve_binary(self.ffmpeg_binary, "ffmpeg.exe")
        self.ffprobe_binary = self._resolve_binary(self.ffprobe_binary, "ffprobe.exe")

    @staticmethod
    def _resolve_binary(configured_value: str, windows_binary_name: str) -> str:
        found = shutil.which(configured_value)
        if found:
            return found
        try:
            if Path(configured_value).exists():
                return configured_value
        except PermissionError:
            return configured_value

        winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        if winget_root.exists():
            for directory in winget_root.glob("Gyan.FFmpeg*"):
                for candidate in (
                    directory / "ffmpeg-8.1-full_build" / "bin" / windows_binary_name,
                    directory / "ffmpeg-master-latest-win64-gpl-shared" / "bin" / windows_binary_name,
                    directory / "ffmpeg-master-latest-win64-gpl" / "bin" / windows_binary_name,
                ):
                    try:
                        exists = candidate.exists()
                    except PermissionError:
                        return str(candidate)
                    if exists:
                        return str(candidate)
                try:
                    matches = list(directory.rglob(windows_binary_name))
                except PermissionError:
                    candidate = directory / "ffmpeg-8.1-full_build" / "bin" / windows_binary_name
                    if candidate.exists():
                        return str(candidate)
                    continue
                if matches:
                    return str(matches[0])
        return configured_value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    settings.resolve_binaries()
    return settings
