from __future__ import annotations

from app.models import FormatType
from app.services.metadata_service import MetadataService


class FallbackOnlyLLM:
    def generate_json(self, *, system_prompt: str, user_prompt: str, fallback_factory):
        return fallback_factory()


class CustomLLM:
    def generate_json(self, *, system_prompt: str, user_prompt: str, fallback_factory):
        return {
            "title": "Custom Kannada Title",
            "description": "Custom description",
            "tags": ["Panchatantra", "Kannada stories", "Panchatantra"],
        }


def test_metadata_service_fallback_builds_short_metadata(sample_story) -> None:
    service = MetadataService(FallbackOnlyLLM())

    metadata = service.generate(
        story=sample_story,
        format_type=FormatType.SHORT,
        language="en",
        script="A clever rabbit turns the lion's pride against him.",
        thumbnail_headline="Tiny Rabbit Outsmarts a Lion",
        cta_lines=["Subscribe for more stories."],
    )

    assert metadata.title.endswith("#Shorts")
    assert "Moral:" in metadata.description
    assert "YouTube Shorts" in metadata.tags
    assert metadata.cta_lines == ["Subscribe for more stories."]


def test_metadata_service_uses_llm_payload_and_deduplicates_tags(sample_story) -> None:
    service = MetadataService(CustomLLM())

    metadata = service.generate(
        story=sample_story,
        format_type=FormatType.FULL,
        language="kn",
        script="ಒಂದು ಚಿಕ್ಕ ಮೊಲ ತನ್ನ ಜಾಣ್ಮೆಯಿಂದ ಕಾಡನ್ನು ಉಳಿಸುತ್ತದೆ.",
        thumbnail_headline="ಚಿಕ್ಕ ಮೊಲದ ದೊಡ್ಡ ಜಯ",
        cta_lines=["ಇನ್ನಷ್ಟು ಕಥೆಗಳಿಗಾಗಿ ಸಬ್‌ಸ್ಕ್ರೈಬ್ ಮಾಡಿ."],
    )

    assert metadata.title == "Custom Kannada Title"
    assert metadata.description == "Custom description"
    assert metadata.tags == ["Panchatantra", "Kannada stories"]
