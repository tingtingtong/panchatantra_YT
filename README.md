# Panchatantra Studio

Production-oriented automation for a faceless YouTube channel that publishes weekly Panchatantra stories in two formats from the same source story:

- `short`: vertical `9:16`, target `35-50s`
- `full`: horizontal `16:9`, target `5-7m`

The system uses FastAPI for admin/API access, SQLite for local state, FFmpeg for media assembly, OpenAI for story/script/prompt/metadata generation, an abstracted TTS layer with ElevenLabs primary and local fallback, YouTube Data API v3 for upload/publishing, and a weekly scheduler.

## Features

- Story library and queue stored in SQLite
- Weekly scheduler that selects the next queued story
- Generates both Shorts and full-length assets from one story
- English and Kannada generation support
- Asset bundle generation:
  - script
  - shot list
  - scene prompts
  - timestamped subtitles
  - thumbnail headline and prompt
  - title, description, tags
  - CTA lines
- FFmpeg render pipeline:
  - intro/outro cards
  - narration
  - background music under voice
  - SRT output
  - optional burned subtitles
- YouTube upload flow:
  - draft
  - private
  - scheduled
  - public
- n8n-compatible weekly webhook trigger
- CLI runner for a single story pipeline

## Project Layout

```text
app/
  main.py
  config.py
  db.py
  models.py
  scheduler.py
  run_pipeline.py
  services/
    story_selector.py
    script_generator.py
    prompt_generator.py
    tts_service.py
    video_generation_service.py
    ffmpeg_renderer.py
    subtitle_service.py
    thumbnail_service.py
    youtube_service.py
    metadata_service.py
    pipeline_service.py
  templates/
  static/
data/
  seed_stories.json
  story_asset.schema.json
  weekly_workflow.yaml
output/
  shorts/
  full/
  audio/
  subtitles/
  thumbnails/
  logs/
tests/
```

## Requirements

- Python `3.12`
- FFmpeg and FFprobe available on `PATH`
- Optional:
  - OpenAI API key for model-backed generation and OpenAI video provider attempts
  - ElevenLabs API key for TTS
  - Google OAuth client secret for real YouTube uploads

## Local Setup

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in the values you plan to use.

```powershell
Copy-Item .env.example .env
```

4. Start the API server.

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

5. Open `http://127.0.0.1:8000/admin`.

On first startup the app creates the SQLite database in `data/panchatantra.db` and seeds it from `data/seed_stories.json`.

## Environment Variables

See `.env.example` for the full list. The key values are:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_VIDEO_MODEL`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`
- `YOUTUBE_CLIENT_SECRETS_FILE`
- `YOUTUBE_TOKEN_FILE`
- `FFMPEG_BINARY`
- `FFPROBE_BINARY`
- `WEEKLY_SCHEDULE_DAY`
- `WEEKLY_SCHEDULE_HOUR`
- `WEEKLY_SCHEDULE_MINUTE`
- `TIMEZONE_NAME`
- `N8N_WEBHOOK_SECRET`

If `OPENAI_API_KEY` is not configured, the app falls back to deterministic local story/script/metadata composition. If `ELEVENLABS_API_KEY` is not configured, the app falls back to the local placeholder TTS adapter. If YouTube OAuth files are not configured, uploads fall back to the local publisher adapter that stores synthetic video ids for pipeline testing.

## FFmpeg Install

### Windows

1. Download FFmpeg from the official builds page.
2. Extract it to a stable folder such as `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to `PATH`.
4. Confirm:

```powershell
ffmpeg -version
ffprobe -version
```

## YouTube OAuth Setup

1. Go to Google Cloud Console.
2. Create or select a project.
3. Enable `YouTube Data API v3`.
4. Configure the OAuth consent screen.
5. Create an OAuth client for a desktop app.
6. Save the client JSON file to:

```text
data/youtube_client_secret.json
```

7. Start the app and trigger an upload. The Google OAuth flow will create:

```text
data/youtube_token.json
```

## Running the Pipeline for One Story

Seed data includes a fully wired sample story with id `lion-rabbit`.

Generate and render only:

```powershell
python -m app.run_pipeline --story-id lion-rabbit --lang en
```

Generate, render, upload, and publish using the selected mode:

```powershell
python -m app.run_pipeline --story-id lion-rabbit --lang en --publish --upload-mode private
```

Kannada run:

```powershell
python -m app.run_pipeline --story-id lion-rabbit --lang kn
```

## Weekly Scheduling

There are two supported ways to run weekly publishing:

1. Built-in scheduler in the FastAPI process
2. External automation calling the webhook or CLI

The built-in scheduler uses:

- `WEEKLY_SCHEDULE_DAY`
- `WEEKLY_SCHEDULE_HOUR`
- `WEEKLY_SCHEDULE_MINUTE`
- `TIMEZONE_NAME`

The scheduler picks the next queued story and runs generate, render, upload, and publish with `scheduled` mode.

An example external YAML workflow is provided at `data/weekly_workflow.yaml`.

## API Endpoints

- `POST /stories`
- `GET /stories`
- `POST /generate/{story_id}`
- `POST /render/{story_id}`
- `POST /upload/{story_id}`
- `POST /publish/{story_id}`
- `GET /jobs`
- `GET /assets/{story_id}`
- `POST /webhooks/n8n/weekly`

## cURL Examples

Create a story:

```bash
curl -X POST http://127.0.0.1:8000/stories \
  -H "Content-Type: application/json" \
  -d '{
    "id": "lion-rabbit",
    "title": "The Lion and the Clever Rabbit",
    "moral": "Intelligence and patience can defeat brute strength.",
    "source_summary": "A tyrannical lion forces the forest animals to send him one victim daily. A rabbit delays its arrival, tricks the lion into looking into a well, and the lion leaps at his own reflection.",
    "language": "en",
    "formats_needed": ["short", "full"]
  }'
```

List stories:

```bash
curl http://127.0.0.1:8000/stories
```

Generate both formats for English:

```bash
curl -X POST http://127.0.0.1:8000/generate/lion-rabbit \
  -H "Content-Type: application/json" \
  -d '{"languages":["en"],"formats":["short","full"]}'
```

Render:

```bash
curl -X POST http://127.0.0.1:8000/render/lion-rabbit \
  -H "Content-Type: application/json" \
  -d '{"languages":["en"],"formats":["short","full"],"burn_subtitles":true}'
```

Upload privately:

```bash
curl -X POST http://127.0.0.1:8000/upload/lion-rabbit \
  -H "Content-Type: application/json" \
  -d '{"languages":["en"],"formats":["short","full"],"mode":"private"}'
```

Schedule publication:

```bash
curl -X POST http://127.0.0.1:8000/publish/lion-rabbit \
  -H "Content-Type: application/json" \
  -d '{
    "languages":["en"],
    "formats":["short","full"],
    "mode":"scheduled",
    "scheduled_publish_at":"2026-03-31T03:30:00Z"
  }'
```

Fetch assets:

```bash
curl http://127.0.0.1:8000/assets/lion-rabbit
```

Trigger the weekly webhook:

```bash
curl -X POST http://127.0.0.1:8000/webhooks/n8n/weekly \
  -H "x-webhook-secret: change-me"
```

## Output Files

Typical generated files:

- `output/shorts/{story_id}_{language}_short_assets.json`
- `output/full/{story_id}_{language}_full_assets.json`
- `output/shorts/{story_id}_{language}_short.mp4`
- `output/full/{story_id}_{language}_full.mp4`
- `output/audio/{story_id}_{language}_{format}.wav`
- `output/subtitles/{story_id}_{language}_{format}.srt`
- `output/thumbnails/{story_id}_{language}_{format}.png`
- `output/logs/app.log`
- `output/logs/pipeline.log`

## Testing

Run the test suite with:

```powershell
pytest
```

## Notes

- The OpenAI video provider is implemented as a best-effort adapter and falls back to placeholder scene clips if it cannot complete.
- The local TTS fallback intentionally generates placeholder narration audio for offline development.
- For YouTube scheduling, the implementation maps `scheduled` to YouTube private upload with `publishAt`, which matches the YouTube API behavior.

