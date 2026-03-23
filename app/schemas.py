from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AssetStatus, FormatType, JobStatus, PublishMode, StoryStatus


class StoryCreate(BaseModel):
    id: str
    title: str
    moral: str
    source_summary: str
    language: str = "en"
    status: StoryStatus = StoryStatus.QUEUED
    publish_date: datetime | None = None
    formats_needed: list[FormatType] = Field(default_factory=lambda: [FormatType.SHORT, FormatType.FULL])


class StoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    moral: str
    source_summary: str
    language: str
    status: StoryStatus
    publish_date: datetime | None
    formats_needed: list[str]


class GenerateRequest(BaseModel):
    languages: list[str] = Field(default_factory=list)
    formats: list[FormatType] = Field(default_factory=list)


class RenderRequest(BaseModel):
    languages: list[str] = Field(default_factory=list)
    formats: list[FormatType] = Field(default_factory=list)
    burn_subtitles: bool | None = None


class UploadRequest(BaseModel):
    languages: list[str] = Field(default_factory=list)
    formats: list[FormatType] = Field(default_factory=list)
    mode: PublishMode = PublishMode.PRIVATE
    scheduled_publish_at: datetime | None = None


class PublishRequest(BaseModel):
    languages: list[str] = Field(default_factory=list)
    formats: list[FormatType] = Field(default_factory=list)
    mode: PublishMode = PublishMode.PUBLIC
    scheduled_publish_at: datetime | None = None


class SubtitleLine(BaseModel):
    start: float
    end: float
    text: str


class ShotSpec(BaseModel):
    scene_number: int
    duration_seconds: float
    visual_summary: str
    camera_direction: str
    emotion: str


class PromptSpec(BaseModel):
    scene_number: int
    duration_seconds: float
    prompt: str
    negative_prompt: str = ""


class MetadataSpec(BaseModel):
    title: str
    description: str
    tags: list[str]
    cta_lines: list[str]


class ThumbnailSpec(BaseModel):
    headline: str
    prompt: str
    image_path: str | None = None


class StoryAssetBundle(BaseModel):
    story_id: str
    language: str
    format_type: FormatType
    target_duration_seconds: int
    style_notes: list[str]
    script: str
    shot_list: list[ShotSpec]
    scene_prompts: list[PromptSpec]
    subtitles: list[SubtitleLine]
    thumbnail: ThumbnailSpec
    metadata: MetadataSpec


class StoryAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    story_id: str
    format_type: FormatType
    language: str
    status: AssetStatus
    title_text: str | None
    thumbnail_headline: str | None
    youtube_video_id: str | None
    render_path: str | None
    asset_manifest_path: str | None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    story_id: str | None
    format_type: str | None
    job_type: str
    status: JobStatus
    attempts: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class StoryAssetsResponse(BaseModel):
    story: StoryRead
    assets: list[StoryAssetRead]


class AdminSummary(BaseModel):
    stories: list[StoryRead]
    jobs: list[JobRead]
