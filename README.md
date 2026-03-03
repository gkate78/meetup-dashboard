# DEP Meetup Dashboard

Streamlit analytics app for Data Engineering Pilipinas Meetup data, powered by Meetup GraphQL API.

## What this app does
- Pulls upcoming and past events from Meetup GraphQL.
- Tracks attendance trends, monthly heatmap, KPI metrics, and speaker leaderboard.
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

## Local setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run meetup.py
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

Speaker overrides for missing past speakers:
- `SPEAKER_OVERRIDES_PATH` (default `data/speaker_overrides.csv`)
- Required columns: `event_id`, `canonical_speakers`
- Optional columns: `source`, `notes`
- Rule: Meetup speaker names are kept; overrides are used only when past event speakers are missing.
- Missing speaker names are rendered as blank in event tables/UI.

## Deployment runbook
### Streamlit Community Cloud
1. Push this folder to GitHub.
2. In Streamlit Cloud, create app with main file: `meetup.py`.
3. Add secret `MEETUP_TOKEN` in app settings.
4. (Optional) Add env vars for S3 snapshot backend.

### Health checks before go-live
1. Launch app and verify `Data source: Live API` in the caption.
2. Temporarily remove token and confirm fallback works only when snapshot exists.
3. Confirm event links open correctly and no table rendering breaks on special characters.
4. Confirm charts render on desktop and mobile viewport.
5. Confirm speaker leaderboard does not include placeholder values such as `nan` or `-`.

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
tests/test_meetup_metrics.py
.github/workflows/ci.yml
```
