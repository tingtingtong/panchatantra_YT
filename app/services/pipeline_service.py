from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AssetStatus,
    FormatType,
    JobRecord,
    JobStatus,
    PublishMode,
    Story,
    StoryAsset,
    StoryStatus,
)
from app.schemas import StoryAssetBundle
from app.services.ffmpeg_renderer import FFmpegRenderer
from app.services.budget_service import BudgetService
from app.services.llm_service import LLMService
from app.services.metadata_service import MetadataService
from app.services.prompt_generator import PromptGenerator
from app.services.script_generator import ScriptGenerator
from app.services.story_selector import StorySelector
from app.services.subtitle_service import SubtitleService
from app.services.thumbnail_service import ThumbnailService
from app.services.tts_service import TTSService
from app.services.video_generation_service import VideoGenerationService
from app.services.youtube_service import YouTubeService
from app.utils import write_json


class PipelineService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.llm_service = LLMService(self.settings)
        self.prompt_generator = PromptGenerator()
        self.subtitle_service = SubtitleService()
        self.metadata_service = MetadataService(self.llm_service)
        self.script_generator = ScriptGenerator(
            llm_service=self.llm_service,
            prompt_generator=self.prompt_generator,
            subtitle_service=self.subtitle_service,
            metadata_service=self.metadata_service,
        )
        self.thumbnail_service = ThumbnailService()
        self.tts_service = TTSService(self.settings)
        self.video_generation_service = VideoGenerationService(self.settings)
        self.renderer = FFmpegRenderer(self.settings, self.video_generation_service)
        self.youtube_service = YouTubeService(self.settings)
        self.budget_service = BudgetService(self.settings)

    def generate_story(self, story_id: str, languages: list[str] | None = None, formats: list[FormatType] | None = None) -> list[StoryAsset]:
        story = self._require_story(story_id)
        job = self._start_job("generate", story_id=story_id)
        assets: list[StoryAsset] = []
        try:
            story.status = StoryStatus.GENERATING
            for language in self._resolve_languages(story, languages):
                for format_type in self._resolve_formats(story, formats):
                    bundle = self.script_generator.generate_bundle(story, format_type, language)
                    asset = self._get_or_create_asset(story.id, format_type, language)
                    asset_manifest_path = self._asset_manifest_path(bundle)
                    subtitle_path = self._subtitle_path(bundle)
                    thumbnail_path = ThumbnailService.default_path(self.settings.thumbnail_dir, story.id, language, format_type)

                    FFmpegRenderer.write_subtitle_file(bundle.subtitles, subtitle_path)
                    self.thumbnail_service.create_thumbnail(bundle, thumbnail_path)
                    write_json(asset_manifest_path, bundle.model_dump(mode="json"))

                    asset.asset_manifest_path = str(asset_manifest_path)
                    asset.script_text = bundle.script
                    asset.shot_list = [item.model_dump(mode="json") for item in bundle.shot_list]
                    asset.scene_prompts = [item.model_dump(mode="json") for item in bundle.scene_prompts]
                    asset.subtitle_lines = [item.model_dump(mode="json") for item in bundle.subtitles]
                    asset.thumbnail_headline = bundle.thumbnail.headline
                    asset.title_text = bundle.metadata.title
                    asset.description_text = bundle.metadata.description
                    asset.tags = bundle.metadata.tags
                    asset.cta_lines = bundle.metadata.cta_lines
                    asset.thumbnail_path = str(thumbnail_path)
                    asset.subtitle_path = str(subtitle_path)
                    asset.status = AssetStatus.GENERATED
                    asset.error_message = None
                    assets.append(asset)
            story.status = StoryStatus.GENERATED
            self._complete_job(job, {"assets_created": len(assets)})
            self.session.commit()
            return assets
        except Exception as exc:
            story.status = StoryStatus.FAILED
            self._fail_job(job, exc)
            self.session.commit()
            raise

    def render_story(
        self,
        story_id: str,
        languages: list[str] | None = None,
        formats: list[FormatType] | None = None,
        burn_subtitles: bool | None = None,
    ) -> list[StoryAsset]:
        story = self._require_story(story_id)
        job = self._start_job("render", story_id=story_id)
        rendered_assets: list[StoryAsset] = []
        try:
            story.status = StoryStatus.RENDERING
            for asset in self._find_assets(story, languages, formats):
                if not asset.asset_manifest_path:
                    raise ValueError(f"Asset {asset.id} has not been generated yet")
                bundle = StoryAssetBundle.model_validate(json.loads(Path(asset.asset_manifest_path).read_text(encoding="utf-8")))
                self._enforce_bundle_budget(bundle)
                audio_path = self._audio_path(bundle)
                tts_result = self.tts_service.synthesize(
                    bundle.script,
                    audio_path,
                    bundle.language,
                    target_duration_seconds=bundle.target_duration_seconds,
                )
                scaled_subtitles = self.subtitle_service.scale_lines(bundle.subtitles, tts_result.duration_seconds)
                subtitle_path = self._subtitle_path(bundle)
                FFmpegRenderer.write_subtitle_file(scaled_subtitles, subtitle_path)
                bundle.subtitles = scaled_subtitles
                write_json(self._asset_manifest_path(bundle), bundle.model_dump(mode="json"))
                render_path = self._render_path(bundle)
                self.renderer.render(
                    bundle=bundle,
                    audio_path=tts_result.output_path,
                    subtitle_path=subtitle_path,
                    output_path=render_path,
                    burn_subtitles=burn_subtitles if burn_subtitles is not None else self.settings.burn_subtitles,
                )
                asset.audio_path = str(audio_path)
                asset.subtitle_path = str(subtitle_path)
                asset.render_path = str(render_path)
                asset.status = AssetStatus.RENDERED
                asset.error_message = None
                rendered_assets.append(asset)
            story.status = StoryStatus.RENDERED
            self._complete_job(job, {"assets_rendered": len(rendered_assets)})
            self.session.commit()
            return rendered_assets
        except Exception as exc:
            story.status = StoryStatus.FAILED
            self._fail_job(job, exc)
            self.session.commit()
            raise

    def upload_story(
        self,
        story_id: str,
        *,
        languages: list[str] | None = None,
        formats: list[FormatType] | None = None,
        mode: PublishMode = PublishMode.PRIVATE,
        scheduled_publish_at: datetime | None = None,
    ) -> list[StoryAsset]:
        story = self._require_story(story_id)
        job = self._start_job("upload", story_id=story_id, payload={"mode": mode.value})
        uploaded_assets: list[StoryAsset] = []
        try:
            story.status = StoryStatus.UPLOADING
            for asset in self._find_assets(story, languages, formats):
                if not asset.render_path:
                    raise ValueError(f"Asset {asset.id} has not been rendered yet")
                response = self.youtube_service.publisher.upload_video(
                    asset=asset,
                    video_path=Path(asset.render_path),
                    thumbnail_path=Path(asset.thumbnail_path) if asset.thumbnail_path else None,
                    mode=mode,
                    scheduled_publish_at=scheduled_publish_at,
                )
                asset.youtube_video_id = response["video_id"]
                asset.processing_status = response.get("processing_status", {})
                asset.upload_mode = mode
                asset.scheduled_publish_at = scheduled_publish_at
                asset.status = AssetStatus.UPLOADED
                uploaded_assets.append(asset)
            story.status = StoryStatus.UPLOADED
            self._complete_job(job, {"assets_uploaded": len(uploaded_assets)})
            self.session.commit()
            return uploaded_assets
        except Exception as exc:
            story.status = StoryStatus.FAILED
            self._fail_job(job, exc)
            self.session.commit()
            raise

    def publish_story(
        self,
        story_id: str,
        *,
        languages: list[str] | None = None,
        formats: list[FormatType] | None = None,
        mode: PublishMode = PublishMode.PUBLIC,
        scheduled_publish_at: datetime | None = None,
    ) -> list[StoryAsset]:
        story = self._require_story(story_id)
        job = self._start_job("publish", story_id=story_id, payload={"mode": mode.value})
        published_assets: list[StoryAsset] = []
        try:
            story.status = StoryStatus.PUBLISHING
            for asset in self._find_assets(story, languages, formats):
                if not asset.youtube_video_id:
                    raise ValueError(f"Asset {asset.id} has not been uploaded yet")
                response = self.youtube_service.publisher.publish_video(
                    asset=asset,
                    mode=mode,
                    scheduled_publish_at=scheduled_publish_at,
                )
                asset.processing_status = response.get("status", {})
                asset.upload_mode = mode
                asset.scheduled_publish_at = scheduled_publish_at
                asset.status = AssetStatus.PUBLISHED
                published_assets.append(asset)
            story.status = StoryStatus.PUBLISHED
            self._complete_job(job, {"assets_published": len(published_assets)})
            self.session.commit()
            return published_assets
        except Exception as exc:
            story.status = StoryStatus.FAILED
            self._fail_job(job, exc)
            self.session.commit()
            raise

    def run_weekly_cycle(self) -> dict[str, Any]:
        selector = StorySelector(self.session)
        story = selector.pick_next()
        if story is None:
            return {"message": "No queued stories available"}
        scheduled_publish_at = story.publish_date or StorySelector.next_weekly_slot(datetime.now(timezone.utc))
        self.generate_story(story.id, languages=[story.language])
        self.render_story(story.id, languages=[story.language])
        self.upload_story(
            story.id,
            languages=[story.language],
            mode=PublishMode.SCHEDULED,
            scheduled_publish_at=scheduled_publish_at,
        )
        self.publish_story(
            story.id,
            languages=[story.language],
            mode=PublishMode.SCHEDULED,
            scheduled_publish_at=scheduled_publish_at,
        )
        story.publish_date = scheduled_publish_at
        self.session.commit()
        return {"story_id": story.id, "scheduled_publish_at": scheduled_publish_at.isoformat()}

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        return list(self.session.execute(select(JobRecord).order_by(desc(JobRecord.created_at)).limit(limit)).scalars())

    def list_stories(self) -> list[Story]:
        return list(self.session.execute(select(Story).order_by(Story.created_at)).scalars())

    def get_story_assets(self, story_id: str) -> list[StoryAsset]:
        return list(
            self.session.execute(select(StoryAsset).where(StoryAsset.story_id == story_id).order_by(StoryAsset.id)).scalars()
        )

    def _require_story(self, story_id: str) -> Story:
        story = self.session.get(Story, story_id)
        if story is None:
            raise ValueError(f"Story '{story_id}' was not found")
        return story

    def _resolve_formats(self, story: Story, formats: list[FormatType] | None) -> list[FormatType]:
        if formats:
            return formats
        return [FormatType(value) for value in story.formats_needed]

    @staticmethod
    def _resolve_languages(story: Story, languages: list[str] | None) -> list[str]:
        return languages or [story.language]

    def _find_assets(self, story: Story, languages: list[str] | None, formats: list[FormatType] | None) -> list[StoryAsset]:
        resolved_languages = set(self._resolve_languages(story, languages))
        resolved_formats = {item.value for item in self._resolve_formats(story, formats)}
        return [
            asset
            for asset in story.assets
            if asset.language in resolved_languages and asset.format_type.value in resolved_formats
        ]

    def _get_or_create_asset(self, story_id: str, format_type: FormatType, language: str) -> StoryAsset:
        statement = select(StoryAsset).where(
            StoryAsset.story_id == story_id,
            StoryAsset.format_type == format_type,
            StoryAsset.language == language,
        )
        asset = self.session.execute(statement).scalars().first()
        if asset:
            return asset
        asset = StoryAsset(story_id=story_id, format_type=format_type, language=language)
        self.session.add(asset)
        self.session.flush()
        return asset

    def _start_job(self, job_type: str, story_id: str | None = None, payload: dict[str, Any] | None = None) -> JobRecord:
        job = JobRecord(
            story_id=story_id,
            job_type=job_type,
            status=JobStatus.RUNNING,
            attempts=1,
            payload=payload or {},
        )
        self.session.add(job)
        self.session.flush()
        return job

    @staticmethod
    def _complete_job(job: JobRecord, result: dict[str, Any]) -> None:
        job.status = JobStatus.COMPLETED
        job.result = result

    @staticmethod
    def _fail_job(job: JobRecord, exc: Exception) -> None:
        job.status = JobStatus.FAILED
        job.error_message = str(exc)

    def _asset_manifest_path(self, bundle: StoryAssetBundle) -> Path:
        base_dir = self.settings.shorts_dir if bundle.format_type == FormatType.SHORT else self.settings.full_dir
        return base_dir / f"{bundle.story_id}_{bundle.language}_{bundle.format_type.value}_assets.json"

    def _subtitle_path(self, bundle: StoryAssetBundle) -> Path:
        return self.settings.subtitle_dir / f"{bundle.story_id}_{bundle.language}_{bundle.format_type.value}.srt"

    def _audio_path(self, bundle: StoryAssetBundle) -> Path:
        suffix = ".mp3" if self.settings.elevenlabs_api_key else ".wav"
        return self.settings.audio_dir / f"{bundle.story_id}_{bundle.language}_{bundle.format_type.value}{suffix}"

    def _render_path(self, bundle: StoryAssetBundle) -> Path:
        base_dir = self.settings.shorts_dir if bundle.format_type == FormatType.SHORT else self.settings.full_dir
        return base_dir / f"{bundle.story_id}_{bundle.language}_{bundle.format_type.value}.mp4"

    def _enforce_bundle_budget(self, bundle: StoryAssetBundle) -> None:
        if not self.settings.enforce_story_budget:
            return
        estimated = self.budget_service.estimate_bundle_cost_usd(bundle)
        allowed = self.budget_service.allowed_bundle_cost_usd(bundle.format_type)
        if estimated > allowed:
            raise ValueError(
                f"Estimated render cost ${estimated:.2f} exceeds the configured cap of ${allowed:.2f} "
                f"for {bundle.format_type.value} videos."
            )
