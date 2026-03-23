from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum as SqlEnum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Base = declarative_base()


class StoryStatus(str, enum.Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATED = "generated"
    RENDERING = "rendering"
    RENDERED = "rendered"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class AssetStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    RENDERED = "rendered"
    UPLOADED = "uploaded"
    PUBLISHED = "published"
    FAILED = "failed"


class FormatType(str, enum.Enum):
    SHORT = "short"
    FULL = "full"


class PublishMode(str, enum.Enum):
    DRAFT = "draft"
    PRIVATE = "private"
    SCHEDULED = "scheduled"
    PUBLIC = "public"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Story(Base):
    __tablename__ = "stories"

    id = Column(String(80), primary_key=True)
    title = Column(String(200), nullable=False)
    moral = Column(Text, nullable=False)
    source_summary = Column(Text, nullable=False)
    language = Column(String(12), default="en", nullable=False)
    status = Column(SqlEnum(StoryStatus), default=StoryStatus.QUEUED, nullable=False)
    publish_date = Column(DateTime(timezone=True), nullable=True)
    formats_needed = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    assets = relationship("StoryAsset", back_populates="story", cascade="all, delete-orphan", lazy="selectin")


class StoryAsset(Base):
    __tablename__ = "story_assets"
    __table_args__ = (UniqueConstraint("story_id", "format_type", "language", name="uq_story_format_language"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(String(80), ForeignKey("stories.id"), nullable=False, index=True)
    format_type = Column(SqlEnum(FormatType), nullable=False)
    language = Column(String(12), default="en", nullable=False)
    status = Column(SqlEnum(AssetStatus), default=AssetStatus.PENDING, nullable=False)
    asset_manifest_path = Column(String(500), nullable=True)
    script_text = Column(Text, nullable=True)
    shot_list = Column(JSON, default=list, nullable=False)
    scene_prompts = Column(JSON, default=list, nullable=False)
    subtitle_lines = Column(JSON, default=list, nullable=False)
    thumbnail_headline = Column(String(200), nullable=True)
    title_text = Column(String(220), nullable=True)
    description_text = Column(Text, nullable=True)
    tags = Column(JSON, default=list, nullable=False)
    cta_lines = Column(JSON, default=list, nullable=False)
    thumbnail_path = Column(String(500), nullable=True)
    audio_path = Column(String(500), nullable=True)
    subtitle_path = Column(String(500), nullable=True)
    render_path = Column(String(500), nullable=True)
    upload_mode = Column(SqlEnum(PublishMode), nullable=True)
    youtube_video_id = Column(String(120), nullable=True)
    processing_status = Column(JSON, default=dict, nullable=False)
    scheduled_publish_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    story = relationship("Story", back_populates="assets")


class JobRecord(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(String(80), nullable=True, index=True)
    format_type = Column(String(24), nullable=True)
    job_type = Column(String(24), nullable=False, index=True)
    status = Column(SqlEnum(JobStatus), default=JobStatus.PENDING, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    payload = Column(JSON, default=dict, nullable=False)
    result = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
