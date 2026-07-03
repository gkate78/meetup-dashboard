# DEP Meetup Calendar Feature

## 🎯 Overview
This document describes the community calendar, feedback, booking, and moderator workflow in the DEP Meetup Streamlit analytics app.
The app uses a fixed light Streamlit theme so the interface stays readable on devices that prefer dark mode.

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
  - One submission per event per persisted feedback store
  - Summary stats and recent submissions from the configured feedback store
- **Responsive view switch**:
  - Month Grid (desktop)
  - List view (mobile-friendly)
- **Booking request support**:
  - Popup booking form for speaker slots on available calendar dates
  - Form entries are preserved on invalid submission, and email input is validated before save
- **Moderator booking management**:
  - The Admin page is available when `ADMIN_PASSWORD` is configured
  - Signed-in moderators can filter booking requests and persist status changes
- **Success snapshot** from the persisted feedback store: top events by average rating.

## ⚙️ Configuration

Set these environment variables (or Streamlit secrets):

- `MEETUP_TOKEN` – Meetup GraphQL token
- `FEEDBACK_FORM_URL` – Optional external feedback form URL (default empty)
- `FEEDBACK_DATA_PATH` – Feedback store path on persistent deploy storage (default: `data/feedback.db`; CSV paths are still supported)
- `EVENT_BOOKINGS_PATH` – Booking request storage path (default: `data/event_bookings.db`)
- `ADMIN_PASSWORD` – Optional password that enables moderator access to the Admin page
- `DEP_EVENT_DURATION_MINUTES` – Default existing Meetup event conflict window in minutes (default: `120`)
- `DEP_EVENT_TZ` – Time zone used for display and naive booking datetimes (default: `Asia/Manila`)

## 🧭 How feedback works

1. When `FEEDBACK_FORM_URL` is set, each event includes an external feedback link with `event_id` and `title` query parameters.
2. The built-in Feedback page writes submissions to the configured feedback store with columns:
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

The current default runtime stores are SQLite `.db` files under `data/`. CSV paths remain supported for legacy deployments.
