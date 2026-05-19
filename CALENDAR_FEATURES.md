# DEP Meetup Calendar Feature (Markdown)

## 🎯 Overview
This update adds a community-focused calendar and feedback experience to the DEP Meetup Streamlit analytics app.

### ✅ What’s included

- **Month-grid community calendar** with events placed by date.
- **Per-event cards** include:
  - Event title (clickable), date/time
  - Speaker name(s)
  - Online vs In-person indicator
  - Feedback call-to-action link when `FEEDBACK_FORM_URL` is set
- **Feedback indicators**:
  - Daily badge in calendar (`💬 avg`) when ratings exist
  - Event-level `⭐ avg` rating shown per event
- **Community feedback page**:
  - In-app rating form for each event
  - One submission per event per persisted feedback file
  - Summary stats and recent submissions from the configured feedback store
- **Responsive view switch**:
  - Month Grid (desktop)
  - List view (mobile-friendly)
- **Success snapshot** from the persisted feedback store: top events by average rating.

## ⚙️ Configuration

Set these environment variables (or Streamlit secrets):

- `MEETUP_TOKEN` – Meetup GraphQL token
- `FEEDBACK_FORM_URL` – Optional external feedback form URL (default empty)
- `FEEDBACK_DATA_PATH` – Feedback CSV path stored on persistent deploy storage (default: `data/feedback.csv`)

## 🧭 How feedback works

1. When `FEEDBACK_FORM_URL` is set, each event includes an external feedback link with `event_id` and `title` query parameters.
2. The built-in Feedback page writes submissions to the configured feedback CSV with columns:
   - `event_id`
   - `event_title`
   - `rating`
   - `comment` (optional)
   - `submitted_at`
3. Duplicate submissions for the same event are blocked in-app.
4. The app computes average rating per event and displays:
   - Day-level badge in calendar
   - Event-level star summary in card
   - Summary table under calendar

## 🧪 Validation commands

```bash
python -m py_compile meetup.py
ruff check .
python -m pytest -q
streamlit run meetup.py
```

## 📌 Notes

This feature is now self-contained in-app. Future enhancements could add richer filtering or an admin dashboard for event success trends.
