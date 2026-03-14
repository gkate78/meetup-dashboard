# DEP Meetup Calendar Feature (Markdown)

## 🎯 Overview
This update adds a community-focused calendar experience to the DEP Meetup Streamlit analytics app.

### ✅ What’s included

- **Month-grid community calendar** with events placed by date.
- **Per-event cards** include:
  - Event title (clickable), date/time
  - Speaker name(s)
  - Online vs In-person indicator
  - Feedback call-to-action link
- **Feedback indicators**:
  - Daily badge in calendar (`💬 avg`) when ratings exist
  - Event-level `⭐ avg` rating shown per event
- **Responsive view switch**:
  - Month Grid (desktop)
  - List view (mobile-friendly)
- **Success snapshot** (from `data/feedback.csv`): top events by average rating.

## ⚙️ Configuration

Set these environment variables (or Streamlit secrets):

- `MEETUP_TOKEN` – Meetup GraphQL token
- `FEEDBACK_FORM_URL` – Feedback form URL (default placeholder)
- `FEEDBACK_DATA_PATH` – Local feedback CSV path (default: `data/feedback.csv`)

## 🧭 How feedback works

1. Each event includes an external feedback link with `event_id` and `title` query parameters.
2. `data/feedback.csv` can track submissions with columns:
   - `event_id`
   - `rating`
   - `comment` (optional)
3. The app computes average rating per event and displays:
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

If you want a further enhancement, we can add an integrated in-app rating form (instead of external link) and store feedback directly in `data/feedback.csv`, plus a simple admin dashboard for event success trends.
