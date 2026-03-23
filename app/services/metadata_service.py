from __future__ import annotations

from app.models import FormatType, Story
from app.schemas import MetadataSpec
from app.services.llm_service import LLMService


class MetadataService:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def generate(
        self,
        *,
        story: Story,
        format_type: FormatType,
        language: str,
        script: str,
        thumbnail_headline: str,
        cta_lines: list[str],
    ) -> MetadataSpec:
        def fallback() -> dict:
            base_title = f"{story.title} | Panchatantra Story"
            if format_type == FormatType.SHORT:
                title = f"{base_title} #Shorts"
                description = (
                    f"A fast, cinematic Panchatantra retelling of {story.title}. "
                    f"Moral: {story.moral}."
                )
            else:
                title = f"{story.title} | Cinematic Panchatantra Story with Moral"
                description = (
                    f"Settle in for a premium Panchatantra retelling of {story.title} with Indian forest ambience, "
                    f"clear emotional storytelling, and a strong moral ending.\n\nMoral: {story.moral}\n\n"
                    f"Thumbnail theme: {thumbnail_headline}\n"
                    f"Language: {'Kannada' if language == 'kn' else 'English'}"
                )
            tags = [
                "Panchatantra",
                "kids stories",
                "Indian moral stories",
                story.title,
                "animated storytelling",
                "Kannada stories" if language == "kn" else "English stories",
                "YouTube Shorts" if format_type == FormatType.SHORT else "story video",
            ]
            return {"title": title, "description": description, "tags": tags}

        system_prompt = (
            "You write safe, original YouTube metadata for children's story content. "
            "Return JSON with title, description, and tags."
        )
        user_prompt = (
            f"Story: {story.title}\nLanguage: {language}\nFormat: {format_type.value}\n"
            f"Moral: {story.moral}\nScript excerpt: {script[:1400]}\nThumbnail headline: {thumbnail_headline}\n"
            "Make it SEO-aware but not spammy."
        )
        payload = self.llm_service.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_factory=fallback,
        )
        return MetadataSpec(
            title=payload.get("title", fallback()["title"]),
            description=payload.get("description", fallback()["description"]),
            tags=list(dict.fromkeys(payload.get("tags", fallback()["tags"]))),
            cta_lines=cta_lines,
        )

