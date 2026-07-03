# DEP Meetup Dashboard

Streamlit analytics app for Data Engineering Pilipinas Meetup data, powered by Meetup GraphQL API.

## What this app does
- Pulls upcoming and past events from Meetup GraphQL.
- Tracks attendance trends, monthly heatmap, KPI metrics, and speaker leaderboard.
- Includes a community feedback page with runtime feedback storage.
- Includes a Booking Calendar page for the full DEP schedule and speaker booking requests, with popup booking form validation and preserved entries on invalid submit.
- Computes a weighted `Community Pulse Score` for quick health monitoring.
- Uses resilient data loading with retries and snapshot fallback.
- Normalizes speaker names, separates co-speakers joined by commas/`and`/`&`, preserves credential suffixes, and excludes missing placeholders (for example: `nan`, `none`, `null`, `-`) from ranking.

## Tech stack
- Python 3.11+
- Streamlit
- Pandas
- Plotly
- Requests
- Optional snapshot backend: S3 via boto3
- Fixed light Streamlit theme for consistent readability across devices

## Requirements
To run this app, you need:
- A Meetup GraphQL token
- Python 3.11+
- A writable snapshot path or S3 bucket for cached Meetup data
- Persistent storage for feedback, speaker overrides, and speaker booking data if you want those features enabled

## Local setup
1. Copy example environment variables:

```bash
cp .env.example .env
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the app locally:

```bash
make run
```

Alternative local run with Streamlit directly:

```bash
streamlit run meetup.py
```

The app is configured to render in light mode regardless of the device theme.

You can also use Docker Compose for local development:

```bash
docker compose up --build
```

You can override the data cache TTL to reduce API calls during development or testing. Example (24h cache):

```bash
source .venv/bin/activate
DATA_TTL_SECONDS=86400 .venv/bin/python -m streamlit run meetup.py
```

If you want automated snapshot refreshes (recommended to avoid live API hits), add a scheduled job (GitHub Actions or cron) that runs the included `fetch_snapshot.py` script. The repository contains a sample GitHub Actions workflow at `.github/workflows/snapshot.yml` that runs nightly and writes the snapshot to the configured backend. By default the workflow uses the file backend and only requires the `MEETUP_TOKEN` secret; S3 is optional and can be enabled later by changing `SNAPSHOT_BACKEND` and providing S3 secrets.

## Data files
The app expects these runtime files to be writable:
- `SNAPSHOT_PATH` for cached dashboard snapshots when `SNAPSHOT_BACKEND=file`
- `FEEDBACK_DATA_PATH` for submitted feedback rows (SQLite `.db` recommended; CSV legacy supported)
- `SPEAKER_OVERRIDES_PATH` for manual speaker normalization overrides
- `EVENT_BOOKINGS_PATH` for speaker booking requests

SQLite `.db` paths are the current defaults and are created automatically when needed. If you point any of these settings to a CSV file instead, use these CSV-compatible schemas:
```csv
# feedback.csv
event_id,event_title,rating,comment,submitted_at
```

```csv
# speaker_overrides.csv
event_id,canonical_speakers,source,notes
```

```csv
# event_bookings.csv
requested_datetime,duration_minutes,speaker_name,email,talk_title,talk_summary,preferred_format,availability_notes,status,submitted_at
```

## Secrets and environment variables
Set at least one Meetup token source:
- `MEETUP_TOKEN` (env var), or
- `st.secrets["MEETUP_TOKEN"]` on Streamlit Cloud.

Optional reliability/config knobs:
- `REQUEST_CONNECT_TIMEOUT` (default `5`)
- `REQUEST_READ_TIMEOUT` (default `30`)
- `API_MAX_RETRIES` (default `4`)
- `API_RETRY_BASE_SECONDS` (default `1.5`)
- `DATA_TTL_SECONDS` (default `86400`)

Snapshot backend settings:
- `SNAPSHOT_BACKEND=file|s3` (default `file`)
- `SNAPSHOT_PATH` (default `cache/meetup_snapshot.db`, legacy `cache/meetup_snapshot.json` still supported)
- `SNAPSHOT_S3_BUCKET` (required if backend is `s3`)
- `SNAPSHOT_S3_KEY` (default `meetup/meetup_snapshot.json`)

Feedback settings:
- `FEEDBACK_FORM_URL` (default empty)
- `FEEDBACK_DATA_PATH` (default `data/feedback.db`; legacy `data/feedback.csv` is also supported)

Moderator access:
- `ADMIN_PASSWORD` (optional) enables the admin dashboard page for booking management

Speaker overrides for missing past speakers:
- `SPEAKER_OVERRIDES_PATH` (default `data/speaker_overrides.db`; legacy `data/speaker_overrides.csv` is also supported)
- Required columns: `event_id`, `canonical_speakers`
- Optional columns: `source`, `notes`
- Rule: Meetup speaker names are kept; overrides are used only when past event speakers are missing.
- Missing speaker names are rendered as blank in event tables/UI.
- Leaderboard parsing separates co-speakers while keeping credential suffixes such as `MSDS` with the speaker name.

Speaker booking requests:
- `EVENT_BOOKINGS_PATH` (default `data/event_bookings.db` for SQLite storage; legacy CSV paths are also supported)
- `DEP_EVENT_DURATION_MINUTES` (default `120`) controls the default existing Meetup event conflict window
- `DEP_EVENT_TZ` (default `Asia/Manila`) controls booking display and naive datetime localization
- Required columns: `requested_datetime`, `speaker_name`, `email`, `talk_title`, `submitted_at`
- Optional columns: `duration_minutes`, `talk_summary`, `preferred_format`, `availability_notes`, `status`
- Status values currently used in the app: `Requested`, `Approved`, `Tentative`, `Confirmed`, `Cancelled`
- `ADMIN_PASSWORD` enables the Admin page, where signed-in moderators can review booking requests, filter by status, and update request status.
- Existing stored status values are preserved if they do not match the built-in status list; moderators can still move those requests to a built-in status.
- The Booking Calendar page appends each new request to the configured store and keeps it in persistent storage.
- Future bookings are checked against existing requests and against a default DEP event window to reduce double booking.
- The booking modal preserves entered values when a submission fails validation or conflicts, so users do not lose their input.
- Email addresses are validated before a request is saved.
- The booking form includes a duration in minutes so organizers can avoid overlap on the same time window.
- Booking status changes are persisted back to `EVENT_BOOKINGS_PATH`.

## Deployment runbook
### Streamlit Community Cloud
1. Push this folder to GitHub.
2. In Streamlit Cloud, create app with main file: `meetup.py`.
3. Add secret `MEETUP_TOKEN` in app settings.
4. (Optional) Add env vars for S3 snapshot backend.

### Dokploy
Use mounted storage for the runtime files and point the app at those paths. A typical Dokploy setup mounts persistent storage at `/app/data` and, if using file snapshots, `/app/cache`.

#### Deployment checklist
1. Set the runtime secret `MEETUP_TOKEN` in Dokploy.
2. Mount persistent volumes at `/app/data` and `/app/cache`.
3. Keep the app port set to `8501` (or map Dokploy's `$PORT` to the container's `8501`).
4. Leave `SNAPSHOT_BACKEND=file` unless you also configure S3 credentials.
5. Verify `/app/data` contains writable SQLite DBs for feedback, speaker overrides, and bookings.
6. Confirm the health endpoint `/_stcore/health` responds with `200` after startup.

The repository's compose file already uses named volumes for this layout:
- `meetup_data` -> `/app/data`
- `meetup_cache` -> `/app/cache`

- The container listens on `$PORT` when Dokploy provides it, and falls back to `8501`.
- If you configure the proxy manually, route traffic to the same internal port (`8501` by default).
- Health endpoint: `/_stcore/health` should return `ok` once Streamlit is ready.
- If Dokploy shows Bad Gateway, first verify the app is running, the proxy target matches `$PORT`/`8501`, and the container logs include `Uvicorn server started on 0.0.0.0:<port>`.
- `FEEDBACK_DATA_PATH=/app/data/feedback.db` -> mounted `feedback.db` (SQLite) or `feedback.csv`
- `SPEAKER_OVERRIDES_PATH=/app/data/speaker_overrides.db` -> mounted `speaker_overrides.db` (SQLite) or `speaker_overrides.csv`
- `EVENT_BOOKINGS_PATH=/app/data/event_bookings.db` -> mounted `event_bookings.db` (SQLite) or `event_bookings.csv`
- `SNAPSHOT_PATH=/app/cache/meetup_snapshot.db` -> mounted cache path if you want file-based snapshots (SQLite `.db` or legacy `.json` supported)

The speaker override DB is runtime data and is intentionally ignored by Git and Docker build context. Upload or restore `speaker_overrides.db` into the mounted `/app/data` volume before launch; otherwise the app will still run, but missing-speaker overrides will not be applied. Keep `FEEDBACK_FORM_URL` empty if you want only the in-app feedback page. If you reuse this pattern in another project, the deploy only needs the same environment variables and writable data paths.

### Health checks before go-live
1. Launch app and verify `Data source: Live API` in the caption.
2. Temporarily remove token and confirm fallback works only when snapshot exists.
3. Confirm event links open correctly and no table rendering breaks on special characters.
4. Confirm charts render on desktop and mobile viewport.
5. Confirm speaker leaderboard does not include placeholder values such as `nan` or `-`, and check logs/config to verify `SPEAKER_OVERRIDES_PATH` points at the mounted override DB.
6. Confirm the Booking Calendar page can append a booking request to persistent storage.

## Quality gates
This repo includes CI checks:
- `ruff check .`
- `black --check .`
- `mypy`
- `pytest -q`

To run locally:
```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
black --check .
mypy
pytest -q
```

## Project layout
```text
src/meetup_dashboard/
  ├── __init__.py
  ├── app.py                # Streamlit app UI + data loading
  ├── bookings.py           # Speaker booking persistence and conflict helpers
  ├── metrics.py            # Speaker leaderboard and pulse scoring
  └── snapshot.py           # Snapshot refresh helper
meetup.py                  # Root Streamlit entrypoint wrapper
fetch_snapshot.py          # Root snapshot fetch wrapper
pages/01_Meetup_Events.py
pages/02_KPI_Overview.py
pages/03_Insights.py
pages/04_Analytics.py
pages/05_Speakers.py
pages/06_Feedback.py       # Dedicated Feedback page entrypoint
pages/07_Booking_Calendar.py # Dedicated Booking Calendar page entrypoint
pages/08_Admin.py          # Moderator booking management page
docs/
tests/
.github/workflows/ci.yml
.github/workflows/snapshot.yml
```
