from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import PublishMode, StoryAsset
from app.utils import retry_operation

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:  # pragma: no cover
    Request = Credentials = MediaFileUpload = InstalledAppFlow = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]


SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


class BaseYouTubePublisher(ABC):
    @abstractmethod
    def build_upload_body(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def upload_video(
        self,
        *,
        asset: StoryAsset,
        video_path: Path,
        thumbnail_path: Path | None,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def publish_video(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def poll_processing_status(self, video_id: str) -> dict[str, Any]:
        raise NotImplementedError


class LocalYouTubePublisher(BaseYouTubePublisher):
    def build_upload_body(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        return {
            "snippet": {
                "title": asset.title_text,
                "description": asset.description_text,
                "tags": asset.tags,
                "categoryId": "24",
            },
            "status": {
                "privacyStatus": _privacy_status_for_mode(mode),
                **({"publishAt": _to_utc_iso(scheduled_publish_at)} if scheduled_publish_at and mode == PublishMode.SCHEDULED else {}),
                "selfDeclaredMadeForKids": True,
            },
        }

    def upload_video(
        self,
        *,
        asset: StoryAsset,
        video_path: Path,
        thumbnail_path: Path | None,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        return {
            "video_id": f"local-{asset.story_id}-{asset.format_type.value}-{asset.language}",
            "body": self.build_upload_body(asset=asset, mode=mode, scheduled_publish_at=scheduled_publish_at),
            "thumbnail_applied": bool(thumbnail_path),
            "processing_status": {"uploadStatus": "uploaded", "privacyStatus": _privacy_status_for_mode(mode)},
        }

    def publish_video(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        return {
            "video_id": asset.youtube_video_id,
            "status": {"privacyStatus": _privacy_status_for_mode(mode), "publishAt": _to_utc_iso(scheduled_publish_at)},
        }

    def poll_processing_status(self, video_id: str) -> dict[str, Any]:
        return {"id": video_id, "processingDetails": {"processingStatus": "succeeded"}}


class YouTubeDataPublisher(BaseYouTubePublisher):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service = None

    def build_upload_body(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": _privacy_status_for_mode(mode),
            "selfDeclaredMadeForKids": True,
        }
        if mode == PublishMode.SCHEDULED and scheduled_publish_at:
            status["publishAt"] = _to_utc_iso(scheduled_publish_at)
            status["privacyStatus"] = "private"
        return {
            "snippet": {
                "title": asset.title_text,
                "description": asset.description_text,
                "tags": asset.tags,
                "categoryId": self.settings.youtube_channel_category_id,
            },
            "status": status,
        }

    def upload_video(
        self,
        *,
        asset: StoryAsset,
        video_path: Path,
        thumbnail_path: Path | None,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        body = self.build_upload_body(asset=asset, mode=mode, scheduled_publish_at=scheduled_publish_at)

        def _call() -> dict[str, Any]:
            service = self._get_service()
            request = service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True),
            )
            response = request.execute()
            video_id = response["id"]
            if thumbnail_path:
                service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
            return {
                "video_id": video_id,
                "body": body,
                "processing_status": self.poll_processing_status(video_id),
            }

        return retry_operation(
            _call,
            max_attempts=self.settings.max_retry_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
            logger=logger,
            operation_name="youtube_upload",
        )

    def publish_video(
        self,
        *,
        asset: StoryAsset,
        mode: PublishMode,
        scheduled_publish_at: datetime | None,
    ) -> dict[str, Any]:
        service = self._get_service()
        body = {
            "id": asset.youtube_video_id,
            "status": self.build_upload_body(asset=asset, mode=mode, scheduled_publish_at=scheduled_publish_at)["status"],
        }
        response = service.videos().update(part="status", body=body).execute()
        return {"video_id": response["id"], "status": response["status"]}

    def poll_processing_status(self, video_id: str) -> dict[str, Any]:
        service = self._get_service()
        response = service.videos().list(part="status,processingDetails", id=video_id).execute()
        items = response.get("items", [])
        return items[0] if items else {"id": video_id, "processingDetails": {"processingStatus": "unknown"}}

    def _get_service(self):  # pragma: no cover
        if self._service is not None:
            return self._service
        if not all([Request, Credentials, InstalledAppFlow, build]):
            raise RuntimeError("Google API dependencies are not installed.")

        token_file = Path(self.settings.youtube_token_file)
        credentials = None
        if token_file.exists():
            credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(self.settings.youtube_client_secrets_file, SCOPES)
            credentials = flow.run_local_server(port=0)
            token_file.write_text(credentials.to_json(), encoding="utf-8")
        self._service = build("youtube", "v3", credentials=credentials)
        return self._service


class YouTubeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if Path(settings.youtube_client_secrets_file).exists():
            self.publisher: BaseYouTubePublisher = YouTubeDataPublisher(settings)
        else:
            self.publisher = LocalYouTubePublisher()


def _privacy_status_for_mode(mode: PublishMode) -> str:
    if mode in {PublishMode.DRAFT, PublishMode.PRIVATE, PublishMode.SCHEDULED}:
        return "private"
    return "public"


def _to_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
