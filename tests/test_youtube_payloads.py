from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.models import PublishMode
from app.services.youtube_service import LocalYouTubePublisher, YouTubeDataPublisher


def test_local_youtube_publisher_builds_private_payload(sample_asset) -> None:
    publisher = LocalYouTubePublisher()

    payload = publisher.build_upload_body(
        asset=sample_asset,
        mode=PublishMode.PRIVATE,
        scheduled_publish_at=None,
    )

    assert payload["snippet"]["title"] == sample_asset.title_text
    assert payload["status"]["privacyStatus"] == "private"
    assert payload["status"]["selfDeclaredMadeForKids"] is True


def test_youtube_data_publisher_scheduled_payload_uses_publish_at(sample_asset) -> None:
    publisher = YouTubeDataPublisher(Settings())
    scheduled_time = datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc)

    payload = publisher.build_upload_body(
        asset=sample_asset,
        mode=PublishMode.SCHEDULED,
        scheduled_publish_at=scheduled_time,
    )

    assert payload["status"]["privacyStatus"] == "private"
    assert payload["status"]["publishAt"] == "2026-04-01T12:30:00Z"
    assert payload["snippet"]["categoryId"] == "24"


def test_local_upload_video_returns_stable_fake_id(sample_asset, workspace_tmp_dir) -> None:
    publisher = LocalYouTubePublisher()
    video_path = workspace_tmp_dir / "render.mp4"
    video_path.write_bytes(b"video")
    thumbnail_path = workspace_tmp_dir / "thumb.png"
    thumbnail_path.write_bytes(b"image")

    response = publisher.upload_video(
        asset=sample_asset,
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        mode=PublishMode.PUBLIC,
        scheduled_publish_at=None,
    )

    assert response["video_id"] == "local-lion-rabbit-short-en"
    assert response["thumbnail_applied"] is True
    assert response["processing_status"]["privacyStatus"] == "public"
