# Release Checklist

This checklist is for releasing the DEP Meetup Dashboard from `f:\KATA\Meetup Scraping`.

## 1) Verify repo is connected to GitHub

```powershell
cd "f:\KATA\Meetup Scraping"
git rev-parse --is-inside-work-tree
git remote -v
```

If this folder is not a git repo yet:

```powershell
git init
git branch -M main
git remote add origin <your-github-repo-url>
```

## 2) Install dependencies

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

## 3) Run quality gates

```powershell
ruff check .
black --check .
mypy
pytest -q
```

## 4) Commit and push

```powershell
git rm --cached past.json
git add meetup.py pages/06_Feedback.py README.md CALENDAR_FEATURES.md RELEASE.md .gitignore .dockerignore
git commit -m "Add in-app feedback page and keep feedback data out of git"
git push -u origin main
```

## 5) Deploy to Streamlit Community Cloud

1. Connect the GitHub repository in Streamlit Cloud.
2. Set main file path to `meetup.py`.
3. Add app secret:
   - `MEETUP_TOKEN`
4. Optional environment variables:
   - `SNAPSHOT_BACKEND`
   - `SNAPSHOT_PATH`
   - `SNAPSHOT_S3_BUCKET`
   - `SNAPSHOT_S3_KEY`
   - `REQUEST_CONNECT_TIMEOUT`
   - `REQUEST_READ_TIMEOUT`
   - `API_MAX_RETRIES`
   - `API_RETRY_BASE_SECONDS`
   - `SPEAKER_OVERRIDES_PATH`
   - `FEEDBACK_DATA_PATH`
   - `FEEDBACK_FORM_URL`

## 6) Post-deploy smoke tests

1. Confirm header shows `Data source: Live API`.
2. Confirm speaker leaderboard excludes placeholders like `nan`, `none`, `null`, `-`.
3. Confirm missing speaker names appear blank in event tables.
4. Confirm event links open correctly.
5. Confirm charts and tables render on desktop and mobile.
6. Confirm the feedback CSV is stored on persistent deployment storage, not in git.
7. Confirm the speaker overrides CSV is stored on persistent deployment storage, not in git.

## 7) Rollback option

If deploy is bad:

```powershell
git log --oneline -n 5
git revert <bad_commit_sha>
git push
```
