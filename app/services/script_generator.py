from __future__ import annotations

from typing import Any

from app.models import FormatType, Story
from app.schemas import ShotSpec, StoryAssetBundle, ThumbnailSpec
from app.services.llm_service import LLMService
from app.services.metadata_service import MetadataService
from app.services.prompt_generator import PromptGenerator
from app.services.subtitle_service import SubtitleService


KANNADA_TITLE_MAP = {
    "lion-rabbit": "ಸಿಂಹ ಮತ್ತು ಚತುರ ಮೊಲ",
    "monkey-crocodile": "ಕೋತಿ ಮತ್ತು ಮೊಸಳೆ",
    "crow-serpent": "ಕಾಗೆ ಮತ್ತು ಹಾವು",
    "blue-jackal": "ನೀಲ ನರಿಯ ಕಥೆ",
    "talkative-tortoise": "ಮಾತುಗಾರ ಆಮೆ",
    "dove-hunter": "ಪಾರಿವಾಳಗಳು ಮತ್ತು ಬೇಟೆಗಾರ",
    "heron-crab": "ಕೊಕ್ಕರೆ ಮತ್ತು ಕೆಕ್ಕರೆಯ ಕಥೆ",
    "three-fish": "ಮೂರು ಮೀನುಗಳು",
    "elephant-hare": "ಆನೆಗಳು ಮತ್ತು ಮೊಲ",
    "brahmin-thieves": "ಬ್ರಾಹ್ಮಣ ಮತ್ತು ಮೂವರು ಕಳ್ಳರು",
    "cat-partridge-rabbit": "ಬೆಕ್ಕು, ಹಕ್ಕಿ ಮತ್ತು ಮೊಲ",
    "goose-golden-eggs": "ಚಿನ್ನದ ಮೊಟ್ಟೆ ಕೊಡುವ ಹಂಸ",
}


class ScriptGenerator:
    def __init__(
        self,
        llm_service: LLMService,
        prompt_generator: PromptGenerator,
        subtitle_service: SubtitleService,
        metadata_service: MetadataService,
    ) -> None:
        self.llm_service = llm_service
        self.prompt_generator = prompt_generator
        self.subtitle_service = subtitle_service
        self.metadata_service = metadata_service

    def generate_bundle(self, story: Story, format_type: FormatType, language: str) -> StoryAssetBundle:
        target_duration = 45 if format_type == FormatType.SHORT else 360
        system_prompt = (
            "You adapt Panchatantra stories into original YouTube-ready scripts. "
            "Return JSON with sections, shot_list, thumbnail_headline, and cta_lines. "
            "Keep it family-friendly, cinematic, emotionally clear, and culturally rooted in Indian forest imagery."
        )
        user_prompt = (
            f"Story id: {story.id}\nTitle: {story.title}\nLanguage: {language}\nFormat: {format_type.value}\n"
            f"Moral: {story.moral}\nSummary: {story.source_summary}\n"
            "Requirements: original retelling, child-safe, premium animation feel, strong hook, clear ending moral, natural Kannada when requested."
        )
        payload = self.llm_service.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_factory=lambda: self._fallback_payload(story, format_type, language),
        )

        sections = payload.get("sections") or self._fallback_payload(story, format_type, language)["sections"]
        shot_list_payload = payload.get("shot_list") or self._fallback_payload(story, format_type, language)["shot_list"]
        cta_lines = payload.get("cta_lines") or self._fallback_cta(language, format_type)
        thumbnail_headline = payload.get("thumbnail_headline") or self._thumbnail_headline(story, language, format_type)

        shot_list = [
            ShotSpec(
                scene_number=item.get("scene_number", index + 1),
                duration_seconds=float(item.get("duration_seconds", target_duration / max(len(sections), 1))),
                visual_summary=item.get("visual_summary", sections[index] if index < len(sections) else story.title),
                camera_direction=item.get("camera_direction", "slow push-in"),
                emotion=item.get("emotion", "wonder"),
            )
            for index, item in enumerate(shot_list_payload)
        ]
        subtitles = self.subtitle_service.build_timed_lines(sections, target_duration_seconds=target_duration)
        prompts = self.prompt_generator.generate(story, format_type, language, shot_list)
        script = "\n\n".join(sections)
        metadata = self.metadata_service.generate(
            story=story,
            format_type=format_type,
            language=language,
            script=script,
            thumbnail_headline=thumbnail_headline,
            cta_lines=cta_lines,
        )
        return StoryAssetBundle(
            story_id=story.id,
            language=language,
            format_type=format_type,
            target_duration_seconds=target_duration,
            style_notes=self.prompt_generator.style_notes,
            script=script,
            shot_list=shot_list,
            scene_prompts=prompts,
            subtitles=subtitles,
            thumbnail=ThumbnailSpec(
                headline=thumbnail_headline,
                prompt=f"{thumbnail_headline}. {story.title}. Indian forest cinematic poster art, premium animation feel.",
            ),
            metadata=metadata,
        )

    def _fallback_payload(self, story: Story, format_type: FormatType, language: str) -> dict[str, Any]:
        title = KANNADA_TITLE_MAP.get(story.id, story.title) if language == "kn" else story.title
        moral_text = story.moral if language == "en" else self._to_kannada_moral(story.moral)
        if format_type == FormatType.SHORT:
            sections = self._short_sections(title, story.source_summary, moral_text, language)
        else:
            sections = self._full_sections(title, story.source_summary, moral_text, language)
        scene_duration = 45 / len(sections) if format_type == FormatType.SHORT else 360 / len(sections)
        shot_list = [
            {
                "scene_number": index + 1,
                "duration_seconds": round(scene_duration, 2),
                "visual_summary": section,
                "camera_direction": "dynamic close-up with cinematic parallax" if format_type == FormatType.SHORT else "measured cinematic dolly through forest depth",
                "emotion": self._emotion_for_index(index, len(sections)),
            }
            for index, section in enumerate(sections)
        ]
        return {
            "sections": sections,
            "shot_list": shot_list,
            "thumbnail_headline": self._thumbnail_headline(story, language, format_type),
            "cta_lines": self._fallback_cta(language, format_type),
        }

    def _short_sections(self, title: str, summary: str, moral: str, language: str) -> list[str]:
        if language == "kn":
            return [
                f"ಕಾಡಿನ ರಾಜನು ತನ್ನ ಬಲದ ಮೇಲೆ ಗರ್ವಪಟ್ಟಾಗ ಎಲ್ಲರೂ ಭಯದಿಂದ ನಡುಗಿದರು.",
                f"ಆದರೆ ಒಂದು ಚಿಕ್ಕ ಮೊಲ ಶಾಂತವಾಗಿ ಯೋಚಿಸಿ ಬಲಕ್ಕಿಂತ ಬುದ್ಧಿ ದೊಡ್ಡದು ಎಂದು ಸಾಬೀತು ಮಾಡಲು ಹೊರಟಿತು.",
                f"ಅದು ಸಿಂಹವನ್ನು ಆಳವಾದ ಬಾವಿಯ ಬಳಿಗೆ ಕರೆದೊಯ್ದು ಅಲ್ಲಿ ಇನ್ನೊಂದು ಸಿಂಹ ಇದೆ ಎಂದು ಹೇಳಿತು.",
                f"ಕೋಪದಿಂದ ಉರಿದ ಸಿಂಹ ನೀರಿನಲ್ಲಿ ತನ್ನ ಪ್ರತಿಬಿಂಬ ನೋಡಿ ಎದುರಾಳಿ ಎಂದು ಭಾವಿಸಿತು.",
                f"ಒಂದು ಜಿಗಿತ, ಒಂದು ತಪ್ಪು, ಮತ್ತು ಕಾಡು ಮತ್ತೆ ಉಸಿರೆಳೆದಿತು.",
                f"ಈ ಕಥೆಯ ನೀತಿ: {moral}",
            ]
        return [
            f"In a sunlit Indian forest, fear ruled because a mighty predator trusted only power.",
            f"Then a tiny rabbit chose calm strategy over panic and promised to end the terror.",
            f"It guided the furious beast to an old stone well and whispered about a rival king.",
            f"When the beast saw its own reflection below, pride turned straight into blind rage.",
            f"One reckless leap later, the forest fell silent, then joyful.",
            f"Moral of the story: {moral}",
        ]

    def _full_sections(self, title: str, summary: str, moral: str, language: str) -> list[str]:
        if language == "kn":
            return [
                f"ಮಳೆಗಾಲದ ಮೃದುವಾದ ಬೆಳಕಿನಲ್ಲಿ ನನೆದ ದಟ್ಟ ಕಾಡಿನಲ್ಲಿ ಪ್ರಾಣಿಗಳು ಪ್ರತಿ ಬೆಳಗ್ಗೆಯೂ ಭಯದಿಂದ ಕಣ್ಣು ತೆರೆಯುತ್ತಿದ್ದರು. ಅವರ ಭಯದ ಕೇಂದ್ರವಾಗಿದ್ದದ್ದು ತನ್ನ ಇಚ್ಛೆಗೇ ನಿಯಮ ಎಂದು ನಂಬಿದ ಅಹಂಕಾರಿ ಸಿಂಹ.",
                f"ಅದು ಪ್ರತಿದಿನ ಬೇಟೆಗೆ ಹೊರಟಾಗ ಕಾಡಿನ ಶಾಂತಿ ತುಂಡಾಗುತ್ತಿತ್ತು. ಕೊನೆಗೆ ಪ್ರಾಣಿಗಳು ಒಟ್ಟಾಗಿ ಹೋಗಿ ಪ್ರತಿದಿನ ಒಬ್ಬರನ್ನೇ ಕಳುಹಿಸುತ್ತೇವೆ, ಉಳಿದವರನ್ನು ಬಿಡು ಎಂದು ವಿನಂತಿಸಿದವು.",
                f"ಕೆಲ ದಿನಗಳು ಕಳೆಯುತ್ತಿದ್ದಂತೆ ಒಮ್ಮೆ ಆ ವಾರೆ ಒಂದು ಚಿಕ್ಕ ಮೊಲದ ಪಾಲಿಗೆ ಬಂತು. ಅದು ನಡುಗಲಿಲ್ಲ. ಅದು ಯೋಚಿಸಿತು. ಈ ಕಾಡನ್ನು ಉಳಿಸಲು ಬಲವಲ್ಲ, ಬುದ್ಧಿಯೇ ಸಾಕು ಎಂದು ಅದು ಮನಸ್ಸು ಕಟ್ಟಿಕೊಂಡಿತು.",
                f"ಸಂಜೆ ಹತ್ತಿರವಾಗುವ ತನಕ ಕಾಯ್ದು ಮೊಲ ನಿಧಾನವಾಗಿ ಸಿಂಹದ ಕಡೆ ಹೊರಟಿತು. ತಡವಾಗಿ ಬಂದದ್ದಕ್ಕೆ ಸಿಂಹ ಗರ್ಜಿಸಿ ಕಾಡನ್ನೇ ನಡುಗಿಸಿತು. ಮೊಲ ವಿನಯದಿಂದ ಮತ್ತೊಂದು ಸಿಂಹ ನನ್ನನ್ನು ತಡೆದಿತು ಎಂದು ಹೇಳಿತು.",
                f"ಅಹಂಕಾರಕ್ಕೆ ಬೆಂಕಿ ತಗುಲಿದಂತಾಯಿತು. ಕಾಡಿನಲ್ಲಿ ತನ್ನಿಗಿಂತ ದೊಡ್ಡ ರಾಜನೇ ಇರಲು ಸಾಧ್ಯವಿಲ್ಲ ಎಂದು ಸಿಂಹ ಕೋಪದಿಂದ ತಲ್ಲಣಿಸಿತು. ಮೊಲ ಅದನ್ನು ಹಳೆಯ ಕಲ್ಲಿನ ಬಾವಿಯ ಬಳಿಗೆ ಕರೆದೊಯ್ದಿತು.",
                f"ಬಾವಿಯ ನೀರು ಗಾಢವಾಗಿತ್ತು. ಅದರೊಳಗೆ ತಲೆ ಹಾಕಿ ನೋಡಿದ ಸಿಂಹಕ್ಕೆ ತನ್ನದೇ ಪ್ರತಿಬಿಂಬ ಕಂಡಿತು. ಪ್ರತಿಬಿಂಬದ ಜೊತೆಗೆ ಮರಳಿ ಕೇಳಿದ ಗರ್ಜನೆ ಅದಕ್ಕೆ ಸವಾಲಿನಂತೆ ಅನಿಸಿತು.",
                f"ಯೋಚನೆಗೆ ಅವಕಾಶ ಕೊಡದೆ ಸಿಂಹ ನೇರವಾಗಿ ಬಾವಿಗೆ ಜಿಗಿತವಿಟ್ಟಿತು. ಭಾರವಾದ ದೇಹ ಆಳಕ್ಕೆ ಬಿದ್ದು ಹೋದ ಕ್ಷಣದಲ್ಲಿ ಕಾಡಿನ ಹೃದಯದಿಂದ ಒತ್ತಿಕೊಂಡಿದ್ದ ಭಯ ನಿಧಾನವಾಗಿ ಕಳಚತೊಡಗಿತು.",
                f"ಮೊಲ ಮರಳಿ ಬಂದಾಗ ಎಲ್ಲಾ ಪ್ರಾಣಿಗಳ ಮುಖದಲ್ಲಿ ಆಶ್ಚರ್ಯ, ನಂತರ ನೆಮ್ಮದಿ, ನಂತರ ಸಂತೋಷ ತುಂಬಿತು. ಚಿಕ್ಕದಾದರೂ ಸ್ಪಷ್ಟವಾದ ಬುದ್ಧಿ ಸಮೂಹದ ಬದುಕನ್ನು ಉಳಿಸಿತು.",
                f"ಆ ರಾತ್ರಿ ಕಾಡಿನಲ್ಲಿ ಗಾಳಿ ಹಗುರವಾಗಿತ್ತು. ಮಕ್ಕಳು ಭಯವಿಲ್ಲದೆ ಆಡಿದರು. ಹಿರಿಯರು ಮೊಲದ ಸಮಾಧಾನ ಮತ್ತು ಸಮಯದ ಲೆಕ್ಕಾಚಾರವನ್ನು ಹೊಗಳಿದರು.",
                f"ಈ ಕಥೆಯ ನೀತಿ: {moral}. ಬಲ ಕ್ಷಣಿಕ, ಆದರೆ ಶಾಂತ ಚಿಂತನೆ ಮತ್ತು ಜಾಣ್ಮೆ ದೀರ್ಘಕಾಲದ ಜಯ ತರುತ್ತವೆ.",
            ]
        return [
            f"In a dense Indian forest washed with monsoon light, every creature woke with the same fear. A proud lion believed the jungle existed only to serve his hunger, and every roar tightened that fear into silence.",
            f"To survive, the animals gathered beneath the banyan trees and made a painful agreement. They would send one animal each day so the rest could live. The lion accepted, because cruelty often mistakes convenience for wisdom.",
            f"Days passed until the turn fell to a small rabbit. The forest expected tears, but the rabbit chose observation. It studied the lion's pride, the forest paths, and one abandoned stone well hidden in the undergrowth.",
            f"Instead of arriving on time, the rabbit waited until the sun dipped low. By the time it reached the lion, the predator was blazing with anger. His mane shook, his claws tore the earth, and his patience vanished.",
            f"The rabbit bowed and spoke carefully. Another lion, it said, had stopped me and claimed to be the true ruler of this forest. Those few words did what strength could not: they turned pride into a trap.",
            f"Unable to tolerate even the idea of a rival, the lion ordered the rabbit to lead the way. Through rustling grass and amber evening light, the tiny guide brought the mighty beast to the edge of the well.",
            f"When the lion looked down, he saw his own reflection in the dark water. He heard his own roar returning from the stone walls. But anger clouds judgment, and he believed an enemy had challenged him face to face.",
            f"With a violent leap, the lion hurled himself into the well. Water crashed. Echoes dissolved. Then the forest became still in a new way, not with fear, but with relief. Power had destroyed itself.",
            f"The rabbit returned to the waiting animals, and hope spread from face to face. Children ran again, deer lifted their heads, and the forest seemed to breathe deeper under the evening sky.",
            f"Moral of the story: {moral}. Calm intelligence can defeat brute force, especially when courage is guided by timing, patience, and clarity.",
        ]

    @staticmethod
    def _emotion_for_index(index: int, total: int) -> str:
        if index == 0:
            return "suspense"
        if index >= total - 2:
            return "relief"
        if index >= total // 2:
            return "tension"
        return "anticipation"

    @staticmethod
    def _thumbnail_headline(story: Story, language: str, format_type: FormatType) -> str:
        if language == "kn":
            return "ಚಿಕ್ಕ ಮೊಲದ ದೊಡ್ಡ ಜಯ" if story.id == "lion-rabbit" else f"{story.title} ಕಥೆ"
        return "Tiny Rabbit Outsmarts a Lion" if format_type == FormatType.SHORT else f"{story.title}: Wisdom Beats Power"

    @staticmethod
    def _fallback_cta(language: str, format_type: FormatType) -> list[str]:
        if language == "kn":
            return [
                "ಇಂತಹ ಇನ್ನಷ್ಟು ಕಥೆಗಳಿಗಾಗಿ ಚಾನೆಲ್‌ನ್ನು ಸಬ್‌ಸ್ಕ್ರೈಬ್ ಮಾಡಿ.",
                "ನಿಮ್ಮ ಮಕ್ಕಳಿಗೆ ಈ ಕಥೆ ಇಷ್ಟವಾದರೆ ಹಂಚಿಕೊಳ್ಳಿ.",
            ]
        if format_type == FormatType.SHORT:
            return ["Subscribe for a new Panchatantra Short every week.", "Share this moral story with a young listener."]
        return ["Subscribe for weekly Panchatantra stories.", "Comment with the next animal fable you want to hear."]

    @staticmethod
    def _to_kannada_moral(moral: str) -> str:
        return {
            "Intelligence and patience can defeat brute strength.": "ಜಾಣ್ಮೆ ಮತ್ತು ಸಹನೆ ದೌರ್ಜನ್ಯ ಬಲವನ್ನೂ ಸೋಲಿಸಬಲ್ಲವು.",
        }.get(moral, "ಶಾಂತವಾದ ಜಾಣ್ಮೆ ಬಲಕ್ಕಿಂತ ದೊಡ್ಡದಾಗಿದೆ.")

