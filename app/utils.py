from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "item"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def retry_operation(
    func: Callable[[], T],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    logger: logging.Logger,
    operation_name: str,
    retry_exceptions: Iterable[type[Exception]] = (Exception,),
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except tuple(retry_exceptions) as exc:
            if attempt >= max_attempts:
                logger.error("%s failed after %s attempts: %s", operation_name, attempt, exc)
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning("%s failed on attempt %s, retrying in %.1fs: %s", operation_name, attempt, delay, exc)
            time.sleep(delay)


def chunk_text(text: str, chunk_size: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    for index in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[index : index + chunk_size]))
    return [chunk for chunk in chunks if chunk]

