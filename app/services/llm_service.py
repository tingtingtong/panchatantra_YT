from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app.config import Settings

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - covered by fallback behavior
    OpenAI = None  # type: ignore[assignment]


class LLMService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key and OpenAI else None

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback_factory: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.client:
            return fallback_factory()

        try:
            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("OpenAI request failed, switching to local composition: %s", exc)
            return fallback_factory()
