from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base, Story, StoryStatus


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    seed_database_if_empty()


def seed_database_if_empty() -> None:
    seed_path = Path(settings.content_seed_file)
    if not seed_path.exists():
        return

    with SessionLocal() as session:
        existing = session.execute(select(Story.id).limit(1)).scalar_one_or_none()
        if existing:
            return

        stories = json.loads(seed_path.read_text(encoding="utf-8"))
        for item in stories:
            session.add(
                Story(
                    id=item["id"],
                    title=item["title"],
                    moral=item["moral"],
                    source_summary=item["source_summary"],
                    language=item.get("language", "en"),
                    status=StoryStatus(item.get("status", "queued")),
                    publish_date=item.get("publish_date"),
                    formats_needed=item.get("formats_needed", ["short", "full"]),
                )
            )
        session.commit()
