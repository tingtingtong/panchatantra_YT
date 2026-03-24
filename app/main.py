from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.logging_utils import configure_logging
from app.models import Story
from app.scheduler import build_scheduler
from app.schemas import BudgetPlanResponse, GenerateRequest, PublishRequest, RenderRequest, StoryAssetsResponse, StoryCreate, StoryRead, UploadRequest
from app.services.budget_service import BudgetService
from app.services.pipeline_service import PipelineService

settings = get_settings()
configure_logging(settings.log_level, settings.logs_dir / "app.log")
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
scheduler = build_scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not scheduler.running:
        scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


def get_pipeline(session: Session = Depends(get_session)) -> PipelineService:
    return PipelineService(session, settings)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request, pipeline: PipelineService = Depends(get_pipeline)) -> HTMLResponse:
    stories = pipeline.list_stories()
    jobs = pipeline.list_jobs(limit=20)
    budget_plan = BudgetService(settings).build_plan()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "stories": stories,
            "jobs": jobs,
            "budget_plan": budget_plan,
            "page_title": settings.admin_page_title,
        },
    )


@app.post("/stories", response_model=StoryRead)
def create_story(payload: StoryCreate, session: Session = Depends(get_session)) -> StoryRead:
    if session.get(Story, payload.id):
        raise HTTPException(status_code=409, detail=f"Story '{payload.id}' already exists")
    story = Story(
        id=payload.id,
        title=payload.title,
        moral=payload.moral,
        source_summary=payload.source_summary,
        language=payload.language,
        status=payload.status,
        publish_date=payload.publish_date,
        formats_needed=[item.value for item in payload.formats_needed],
    )
    session.add(story)
    session.commit()
    session.refresh(story)
    return StoryRead.model_validate(story)


@app.get("/stories", response_model=list[StoryRead])
def list_stories(pipeline: PipelineService = Depends(get_pipeline)) -> list[StoryRead]:
    return [StoryRead.model_validate(story) for story in pipeline.list_stories()]


@app.post("/generate/{story_id}")
def generate_story(story_id: str, payload: GenerateRequest, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    try:
        assets = pipeline.generate_story(story_id, languages=payload.languages, formats=payload.formats)
        return {"story_id": story_id, "generated_assets": len(assets)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/render/{story_id}")
def render_story(story_id: str, payload: RenderRequest, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    try:
        assets = pipeline.render_story(
            story_id,
            languages=payload.languages,
            formats=payload.formats,
            burn_subtitles=payload.burn_subtitles,
        )
        return {"story_id": story_id, "rendered_assets": len(assets)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/upload/{story_id}")
def upload_story(story_id: str, payload: UploadRequest, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    try:
        assets = pipeline.upload_story(
            story_id,
            languages=payload.languages,
            formats=payload.formats,
            mode=payload.mode,
            scheduled_publish_at=payload.scheduled_publish_at,
        )
        return {"story_id": story_id, "uploaded_assets": len(assets)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/publish/{story_id}")
def publish_story(story_id: str, payload: PublishRequest, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    try:
        assets = pipeline.publish_story(
            story_id,
            languages=payload.languages,
            formats=payload.formats,
            mode=payload.mode,
            scheduled_publish_at=payload.scheduled_publish_at,
        )
        return {"story_id": story_id, "published_assets": len(assets)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/webhooks/n8n/weekly")
def n8n_weekly_trigger(request: Request, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    secret = request.headers.get("x-webhook-secret")
    if settings.n8n_webhook_secret and secret != settings.n8n_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    return pipeline.run_weekly_cycle()


@app.get("/jobs")
def get_jobs(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict]:
    return [
        {
            "id": job.id,
            "story_id": job.story_id,
            "job_type": job.job_type,
            "status": job.status.value,
            "attempts": job.attempts,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat(),
        }
        for job in pipeline.list_jobs()
    ]


@app.get("/budget", response_model=BudgetPlanResponse)
def get_budget_plan() -> BudgetPlanResponse:
    return BudgetService(settings).build_plan()


@app.get("/assets/{story_id}", response_model=StoryAssetsResponse)
def get_assets(story_id: str, session: Session = Depends(get_session), pipeline: PipelineService = Depends(get_pipeline)) -> StoryAssetsResponse:
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(status_code=404, detail=f"Story '{story_id}' was not found")
    return StoryAssetsResponse(
        story=StoryRead.model_validate(story),
        assets=[asset for asset in pipeline.get_story_assets(story_id)],
    )
