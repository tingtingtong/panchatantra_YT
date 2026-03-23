from __future__ import annotations

from pathlib import Path

from app.services.tts_service import LocalPlaceholderTTSProvider


def test_local_placeholder_tts_honors_target_duration(workspace_tmp_dir: Path) -> None:
    provider = LocalPlaceholderTTSProvider()
    output_path = workspace_tmp_dir / "placeholder.wav"

    result = provider.synthesize(
        "A clever rabbit saves the forest through patience and timing.",
        output_path,
        "en",
        target_duration_seconds=45.0,
    )

    assert output_path.exists()
    assert result.duration_seconds == 45.0
