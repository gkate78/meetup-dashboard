# DEP Meetup Dashboard

Streamlit analytics app for Data Engineering Pilipinas Meetup data, powered by Meetup GraphQL API.

## What this app does
- Pulls upcoming and past events from Meetup GraphQL.
- Tracks attendance trends, monthly heatmap, KPI metrics, and speaker leaderboard.
- Includes a community feedback page with runtime feedback storage.
- Includes a Booking Calendar page for the full DEP schedule and speaker booking requests, with popup booking form validation and preserved entries on invalid submit.
- Computes a weighted `Community Pulse Score` for quick health monitoring.
- Uses resilient data loading with retries and snapshot fallback.
- Normalizes speaker names and excludes missing placeholders (for example: `nan`, `none`, `null`, `-`) from ranking.

## Tech stack
- Python 3.11+
- Streamlit
- Pandas
- Plotly
- Requests
- Optional snapshot backend: S3 via boto3

## Requirements
To run the app in a new project, you need:
- A Meetup GraphQL token
- Python 3.11+
- A writable snapshot path or S3 bucket for cached Meetup data
- Persistent storage for feedback, speaker override, and speaker booking CSV files if you want those features enabled

## Local setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run meetup.py
```

You can override the data cache TTL to reduce API calls during development or testing. Example (24h cache):

```bash
DATA_TTL_SECONDS=86400 source .venv/bin/activate && .venv/bin/python -m streamlit run meetup.py
```

If you want automated snapshot refreshes (recommended to avoid live API hits), add a scheduled job (GitHub Actions or cron) that runs the included `fetch_snapshot.py` script. The repository contains a sample GitHub Actions workflow at `.github/workflows/snapshot.yml` that runs nightly and writes the snapshot to the configured backend. By default the workflow uses the file backend and only requires the `MEETUP_TOKEN` secret; S3 is optional and can be enabled later by changing `SNAPSHOT_BACKEND` and providing S3 secrets.

## Data files
The app expects these runtime files to be writable:
- `SNAPSHOT_PATH` for cached dashboard snapshots when `SNAPSHOT_BACKEND=file`
- `FEEDBACK_DATA_PATH` for submitted feedback rows (SQLite `.db` recommended; CSV legacy supported)
- `SPEAKER_OVERRIDES_PATH` for manual speaker normalization overrides
- `EVENT_BOOKINGS_PATH` for speaker booking requests

Recommended initial schemas:
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

Snapshot backend settings:
- `SNAPSHOT_BACKEND=file|s3` (default `file`)
- `SNAPSHOT_PATH` (default `cache/meetup_snapshot.json`)
- `SNAPSHOT_S3_BUCKET` (required if backend is `s3`)
- `SNAPSHOT_S3_KEY` (default `meetup/meetup_snapshot.json`)

Feedback settings:
- `FEEDBACK_FORM_URL` (default empty)
- `FEEDBACK_DATA_PATH` (default `data/feedback.db`; legacy `data/feedback.csv` is also supported)

Speaker overrides for missing past speakers:
- `SPEAKER_OVERRIDES_PATH` (default `data/speaker_overrides.db`; legacy `data/speaker_overrides.csv` is also supported)
- Required columns: `event_id`, `canonical_speakers`
- Optional columns: `source`, `notes`
- Rule: Meetup speaker names are kept; overrides are used only when past event speakers are missing.
- Missing speaker names are rendered as blank in event tables/UI.

Speaker booking requests:
- `EVENT_BOOKINGS_PATH` (default `data/event_bookings.db` for SQLite storage; legacy CSV paths are also supported)
- Required columns: `requested_datetime`, `speaker_name`, `email`, `talk_title`, `submitted_at`
- Optional columns: `duration_minutes`, `talk_summary`, `preferred_format`, `availability_notes`, `status`
- Status values currently used in the app: `Requested`, `Tentative`, `Confirmed`, `Cancelled`
- The Booking Calendar page appends each new request to the storage file and keeps it in persistent storage.
- Future bookings are checked against existing requests and against a default DEP event window to reduce double booking.
- The booking modal preserves entered values when a submission fails validation or conflicts, so users do not lose their input.
- Email addresses are validated before a request is saved.
- The booking form includes a duration in minutes so organizers can avoid overlap on the same time window.
- Booking status can be updated from the Booking Calendar page using the "Update booking status" panel in Recent requests.

## Deployment runbook
### Streamlit Community Cloud
1. Push this folder to GitHub.
2. In Streamlit Cloud, create app with main file: `meetup.py`.
3. Add secret `MEETUP_TOKEN` in app settings.
4. (Optional) Add env vars for S3 snapshot backend.

### Dokploy
Use mounted storage for the runtime files and point the app at those paths:

- `FEEDBACK_DATA_PATH` -> mounted `feedback.db` (SQLite) or `feedback.csv`
- `SPEAKER_OVERRIDES_PATH` -> mounted `speaker_overrides.db` (SQLite) or `speaker_overrides.csv`
- `EVENT_BOOKINGS_PATH` -> mounted `event_bookings.db` (SQLite) or `event_bookings.csv`
- `SNAPSHOT_PATH` -> mounted cache path if you want file-based snapshots

Keep `FEEDBACK_FORM_URL` empty if you want only the in-app feedback page. If you reuse this pattern in another project, the deploy only needs the same environment variables and writable data paths.

### Health checks before go-live
1. Launch app and verify `Data source: Live API` in the caption.
2. Temporarily remove token and confirm fallback works only when snapshot exists.
3. Confirm event links open correctly and no table rendering breaks on special characters.
4. Confirm charts render on desktop and mobile viewport.
5. Confirm speaker leaderboard does not include placeholder values such as `nan` or `-`.
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
meetup.py                  # Streamlit app UI + data loading
meetup_metrics.py          # Analytics functions (testable core)
fetch_snapshot.py          # Scheduled snapshot fetch helper
pages/06_Feedback.py       # Dedicated Feedback page entrypoint
pages/07_Booking_Calendar.py # Dedicated Booking Calendar page entrypoint
tests/test_meetup_metrics.py
.github/workflows/ci.yml
.github/workflows/snapshot.yml
```
