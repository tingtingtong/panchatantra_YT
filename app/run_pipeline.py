from __future__ import annotations

import argparse

from app.config import get_settings
from app.db import init_db, session_scope
from app.logging_utils import configure_logging
from app.models import PublishMode
from app.services.pipeline_service import PipelineService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Panchatantra pipeline for a single story.")
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--upload-mode", default="private", choices=[mode.value for mode in PublishMode])
    parser.add_argument("--publish", action="store_true", help="Upload and publish after render.")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, settings.logs_dir / "pipeline.log")
    init_db()

    with session_scope() as session:
        pipeline = PipelineService(session, settings)
        pipeline.generate_story(args.story_id, languages=[args.lang])
        pipeline.render_story(args.story_id, languages=[args.lang])
        if args.publish:
            mode = PublishMode(args.upload_mode)
            pipeline.upload_story(args.story_id, languages=[args.lang], mode=mode)
            pipeline.publish_story(args.story_id, languages=[args.lang], mode=mode)


if __name__ == "__main__":
    main()
