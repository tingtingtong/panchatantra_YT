from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Generic, TypeVar
import inspect
import re

import pytest
import sqlalchemy
from sqlalchemy import Column, create_engine
from sqlalchemy.ext.declarative import declarative_base
import sqlalchemy.orm as orm
from sqlalchemy.orm import DeclarativeMeta, Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if not hasattr(orm, "DeclarativeBase"):
    class _CompatMeta(DeclarativeMeta):
        def __new__(mcls, name, bases, dct, **kwargs):
            if name == "Base" and "__tablename__" not in dct and "__table__" not in dct:
                dct = dict(dct)
                dct["__abstract__"] = True
            return super().__new__(mcls, name, bases, dct, **kwargs)

    class _CompatDeclarativeBase:
        pass

    orm.DeclarativeBase = declarative_base(  # type: ignore[attr-defined]
        cls=_CompatDeclarativeBase,
        metaclass=_CompatMeta,
    )

    original_relationship = orm.relationship

    def relationship(argument=None, *args, **kwargs):
        if argument is None:
            frame = inspect.currentframe()
            caller_locals = frame.f_back.f_locals if frame and frame.f_back else {}
            annotations = caller_locals.get("__annotations__", {})
            if annotations:
                annotation = next(reversed(annotations.values()))
                match = re.search(r'["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?', str(annotation).split("[")[-1])
                if match:
                    argument = match.group(1)
        return original_relationship(argument, *args, **kwargs)

    orm.relationship = relationship  # type: ignore[attr-defined]

if not hasattr(sqlalchemy, "Select"):
    sqlalchemy.Select = object  # type: ignore[attr-defined]

if not hasattr(orm, "mapped_column"):
    def mapped_column(*args, **kwargs):
        return Column(*args, **kwargs)

    orm.mapped_column = mapped_column  # type: ignore[attr-defined]

if not hasattr(orm, "Mapped"):
    T = TypeVar("T")

    class Mapped(Generic[T]):
        pass

    orm.Mapped = Mapped  # type: ignore[attr-defined]

from app.models import Base, FormatType, Story, StoryAsset, StoryStatus


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as db_session:
        if not hasattr(db_session, "scalars"):
            db_session.scalars = lambda statement: db_session.execute(statement).scalars()  # type: ignore[attr-defined]
        yield db_session


@pytest.fixture()
def workspace_tmp_dir() -> Generator[Path, None, None]:
    path = Path.cwd() / "tests" / "_tmp"
    path.mkdir(parents=True, exist_ok=True)
    yield path


@pytest.fixture()
def sample_story() -> Story:
    return Story(
        id="lion-rabbit",
        title="The Lion and the Clever Rabbit",
        moral="Intelligence and patience can defeat brute strength.",
        source_summary="A lion terrorizes the forest until a rabbit tricks him into jumping into a well.",
        language="en",
        status=StoryStatus.QUEUED,
        publish_date=datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc),
        formats_needed=[FormatType.SHORT.value, FormatType.FULL.value],
    )


@pytest.fixture()
def sample_asset(sample_story: Story) -> StoryAsset:
    return StoryAsset(
        story_id=sample_story.id,
        format_type=FormatType.SHORT,
        language="en",
        title_text="The Lion and the Clever Rabbit | Panchatantra Story #Shorts",
        description_text="A fast, cinematic Panchatantra retelling.",
        tags=["Panchatantra", "kids stories", "Panchatantra"],
    )
