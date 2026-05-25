import base64
import html
import json
import logging
import os
import random
import re
import time
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def _resolve_data_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    value = os.path.expanduser(value)
    return value if os.path.isabs(value) else os.path.abspath(os.path.join(ROOT_DIR, value))


def _load_local_dotenv() -> None:
    dotenv_path = os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(dotenv_path):
        return

    try:
        with open(dotenv_path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                if key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_local_dotenv()

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit.errors import StreamlitSecretNotFoundError

from .bookings import (
    DEFAULT_EVENT_DURATION_MINUTES,
    DEP_EVENT_TZ,
    booking_conflict_mask,
    first_booking_conflict,
    first_event_conflict_row,
    format_booking_conflict_message,
    format_event_conflict_message,
    load_event_bookings,
    save_event_booking,
    save_event_bookings,
    slot_conflict_mask,
    update_event_booking_status,
    _display_timestamp,
)
from .metrics import build_speaker_leaderboard, compute_pulse, safe_metric


def sanitize_title(title):
    # Remove emojis
    emoji_pattern = re.compile(
        "["
        "\U0001f600-\U0001f64f"  # emoticons
        "\U0001f300-\U0001f5ff"  # symbols & pictographs
        "\U0001f680-\U0001f6ff"  # transport & map symbols
        "\U0001f1e0-\U0001f1ff"  # flags (iOS)
        "\U00002700-\U000027bf"  # Dingbats
        "\U000024c2-\U0001f251"
        "]+",
        flags=re.UNICODE,
    )
    clean = emoji_pattern.sub(r"", str(title))
    clean = clean.replace("\t", " ").replace("\n", " ").strip()
    clean = clean.replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("`", "\\`")
    return clean


def get_viewport_width():
    params = st.query_params if hasattr(st, "query_params") else st.experimental_get_query_params()
    raw_vw = params.get("vw")
    if isinstance(raw_vw, list):
        raw_vw = raw_vw[0] if raw_vw else None
    try:
        vw = int(raw_vw) if raw_vw is not None else None
    except (TypeError, ValueError):
        vw = None

    if vw is None:
        components.html(
            """
            <script>
              const params = new URLSearchParams(window.location.search);
              if (!params.get("vw")) {
                params.set("vw", window.innerWidth);
                window.location.search = params.toString();
              }
            </script>
            """,
            height=0,
        )
    return vw


def safe_rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    elif hasattr(st, "experimental_rerun"):
        st.experimental_rerun()
    else:
        raise RuntimeError("Streamlit rerun is unavailable")


def is_moderator_authenticated() -> bool:
    return bool(st.session_state.get("is_moderator", False))


def render_moderator_sidebar():
    with st.sidebar.expander("Moderator access", expanded=True):
        if ADMIN_PASSWORD:
            if st.session_state.get("is_moderator", False):
                st.success("Moderator signed in")
                if st.button("Sign out", key="admin_sign_out"):
                    st.session_state.pop("is_moderator", None)
                    st.session_state.pop("admin_password_input", None)
                    safe_rerun()
            else:
                password = st.text_input(
                    "Moderator password",
                    type="password",
                    key="admin_password_input",
                )
                if st.button("Sign in", key="admin_sign_in"):
                    if password.strip() == ADMIN_PASSWORD:
                        st.session_state["is_moderator"] = True
                        st.session_state.pop("admin_password_input", None)
                        safe_rerun()
                    else:
                        st.error("Invalid moderator password")
        else:
            st.info("ADMIN_PASSWORD not configured. Admin console is disabled.")


# -------------------
# Config
# -------------------

API_URL = "https://api.meetup.com/gql-ext"
URLNAME = "data-engineering-pilipinas"
SPEAKER_OVERRIDES_PATH = _resolve_data_path(os.getenv("SPEAKER_OVERRIDES_PATH", "data/speaker_overrides.db"))
SNAPSHOT_PATH = _resolve_data_path(os.getenv("SNAPSHOT_PATH", "cache/meetup_snapshot.db"))
SNAPSHOT_BACKEND = os.getenv("SNAPSHOT_BACKEND", "file").strip().lower()
SNAPSHOT_S3_BUCKET = os.getenv("SNAPSHOT_S3_BUCKET", "").strip()
SNAPSHOT_S3_KEY = os.getenv("SNAPSHOT_S3_KEY", "meetup/meetup_snapshot.json").strip()
REQUEST_TIMEOUT = (
    float(os.getenv("REQUEST_CONNECT_TIMEOUT", "5")),
    float(os.getenv("REQUEST_READ_TIMEOUT", "30")),
)
MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "4"))
RETRY_BASE_SECONDS = float(os.getenv("API_RETRY_BASE_SECONDS", "1.5"))
PAGE_VIEW = (
    st.session_state.get("DEP_PAGE")
    if "DEP_PAGE" in st.session_state
    else os.getenv("DEP_PAGE", "all")
).strip().lower()

# How long to cache dashboard data (seconds). Increase to reduce API calls.
# Default to 24 hours since the meetup data doesn't change frequently.
DATA_TTL_SECONDS = int(os.getenv("DATA_TTL_SECONDS", "86400"))

logger = logging.getLogger("dep_meetup")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

MEETUP_TOKEN = os.getenv("MEETUP_TOKEN", "").strip()
FEEDBACK_FORM_URL = os.getenv("FEEDBACK_FORM_URL", "").strip()
FEEDBACK_DATA_PATH = _resolve_data_path(os.getenv("FEEDBACK_DATA_PATH", "data/feedback.db"))
EVENT_BOOKINGS_PATH = _resolve_data_path(os.getenv("EVENT_BOOKINGS_PATH", "data/event_bookings.db"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
if not MEETUP_TOKEN:
    try:
        MEETUP_TOKEN = st.secrets["MEETUP_TOKEN"].strip()
    except (StreamlitSecretNotFoundError, KeyError):
        MEETUP_TOKEN = ""

if not ADMIN_PASSWORD:
    try:
        ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"].strip()
    except (StreamlitSecretNotFoundError, KeyError):
        ADMIN_PASSWORD = ""

# -------------------
# GraphQL Queries
# -------------------
QUERY_MEMBERS = """
query getGroupMembers($urlname: String!) {
  groupByUrlname(urlname: $urlname) {
    stats {
      memberCounts {
        all
      }
    }
  }
}
"""

QUERY_UPCOMING = """
query getUpcomingGroupEvents($urlname: String!, $first: Int!) {
  groupByUrlname(urlname: $urlname) {
    events(first: $first, status: ACTIVE, sort: ASC) {
      totalCount
      edges {
        node {
          id
          title
          eventUrl
          dateTime
          eventType
          rsvps {
            yesCount
          }
          speakerDetails {
            name
            description
          }
        }
      }
    }
  }
}
"""

QUERY_PAST = """
query getPastGroupEvents($urlname: String!, $first: Int!, $after: String) {
  groupByUrlname(urlname: $urlname) {
    events(first: $first, after: $after, status: PAST, sort: DESC) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          id
          title
          eventUrl
          dateTime
          eventType
          rsvps {
            yesCount
          }
          speakerDetails {
            name
            description
          }
        }
      }
    }
  }
}
"""


def _is_sqlite_path(path: str) -> bool:
    return os.path.splitext(str(path).lower())[1] in {".db", ".sqlite", ".sqlite3"}


def _ensure_feedback_sqlite_schema(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                event_id TEXT,
                event_title TEXT,
                rating REAL,
                comment TEXT,
                submitted_at TEXT
            )
            """
        )
        conn.commit()


def _load_feedback_from_sqlite(path: str) -> pd.DataFrame:
    import sqlite3

    if not os.path.exists(path):
        return pd.DataFrame(columns=["event_id", "event_title", "rating", "comment", "submitted_at"])
    _ensure_feedback_sqlite_schema(path)
    try:
        with sqlite3.connect(path) as conn:
            return pd.read_sql_query("SELECT * FROM feedback", conn)
    except Exception as exc:
        logger.warning("Unable to load feedback data from SQLite %s: %s", path, exc)
        return pd.DataFrame(columns=["event_id", "event_title", "rating", "comment", "submitted_at"])


def _ensure_snapshot_sqlite_schema(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshot (
                saved_at TEXT,
                member_count INTEGER,
                payload_json TEXT
            )
            """
        )
        conn.commit()


def _save_snapshot_to_sqlite(path: str, payload: dict[str, Any]) -> None:
    import sqlite3

    _ensure_snapshot_sqlite_schema(path)
    json_text = json.dumps(payload, ensure_ascii=False)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM snapshot")
        conn.execute(
            "INSERT INTO snapshot (saved_at, member_count, payload_json) VALUES (?, ?, ?)",
            (payload.get("saved_at"), int(payload.get("member_count", 0)), json_text),
        )
        conn.commit()


def _load_snapshot_from_sqlite(path: str) -> dict[str, Any] | None:
    import sqlite3

    if not os.path.exists(path):
        return None
    try:
        _ensure_snapshot_sqlite_schema(path)
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT payload_json FROM snapshot LIMIT 1").fetchone()
        if not row or not row[0]:
            return None
        return json.loads(row[0])
    except Exception as exc:
        logger.warning("Unable to load snapshot data from SQLite %s: %s", path, exc)
        return None


def load_feedback_data(path):
    columns = ["event_id", "event_title", "rating", "comment", "submitted_at"]
    if _is_sqlite_path(path):
        fb = _load_feedback_from_sqlite(path)
        if "event_id" not in fb.columns or "rating" not in fb.columns:
            return pd.DataFrame(columns=columns)
        fb["event_id"] = fb["event_id"].astype(str).str.strip()
        return fb

    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    try:
        fb = pd.read_csv(path)
        if "event_id" not in fb.columns or "rating" not in fb.columns:
            fb = pd.read_csv(path, header=None, names=columns)
        fb = fb.rename(
            columns={
                "event_id": "event_id",
                "event_title": "event_title",
                "rating": "rating",
                "comment": "comment",
                "submitted_at": "submitted_at",
            }
        )
        if "event_id" not in fb.columns or "rating" not in fb.columns:
            return pd.DataFrame(columns=columns)
        fb["event_id"] = fb["event_id"].astype(str).str.strip()
        return fb
    except Exception as e:
        logger.warning("Unable to load feedback data: %s", e)
        return pd.DataFrame(columns=columns)


def save_feedback_data(path, record):
    columns = ["event_id", "event_title", "rating", "comment", "submitted_at"]
    if _is_sqlite_path(path):
        _ensure_feedback_sqlite_schema(path)
        import sqlite3

        df = pd.DataFrame([record], columns=columns)
        try:
            with sqlite3.connect(path) as conn:
                df.to_sql("feedback", conn, if_exists="append", index=False)
        except Exception as exc:
            logger.warning("Unable to save feedback data to SQLite %s: %s", path, exc)
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame([record], columns=columns)
    header = not os.path.exists(path) or os.path.getsize(path) == 0
    df.to_csv(path, mode="a", index=False, header=header)


def format_speakers(speakers):
    if not speakers:
        return ""
    if isinstance(speakers, dict):
        speakers = [speakers]
    names = []
    seen = set()
    for s in speakers:
        raw_name = str(s.get("name", "")).strip()
        # Keep only readable name characters for cleaner table output.
        clean_name = re.sub(r"[^A-Za-z0-9Ññ .,'-]", "", raw_name)
        clean_name = re.sub(r"\s+", " ", clean_name).strip(" -_,.")
        key = clean_name.casefold()
        if clean_name and key not in seen:
            seen.add(key)
            names.append(clean_name)
    return ", ".join(names) if names else ""


def normalize_speaker_text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value)
    if text.strip().casefold() in {"", "-", "nan", "none", "null", "na", "n/a"}:
        return ""
    text = text.replace("<br/>", ",").replace("<br>", ",")
    text = re.sub(r"<[^>]+>", " ", text)
    parts = re.split(r"[,;\n]+", text)
    names = []
    seen = set()
    for part in parts:
        part = re.sub(r"^\s*[-*]+\s*", "", part)
        part = re.sub(r"[^A-Za-z0-9Ññ .,'-]", "", part)
        part = re.sub(r"\s+", " ", part).strip(" -_,.")
        key = part.casefold()
        if part and key not in seen and key not in {"nan", "none", "null", "na", "n/a"}:
            seen.add(key)
            names.append(part)
    return ", ".join(names) if names else ""


def normalize_speaker_column(df):
    if df is not None and not df.empty and "Speakers" in df.columns:
        df["Speakers"] = df["Speakers"].apply(normalize_speaker_text)
    return df


def _ensure_speaker_overrides_sqlite_schema(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS speaker_overrides (
                event_id TEXT,
                canonical_speakers TEXT
            )
            """
        )
        conn.commit()


def _load_speaker_overrides_from_sqlite(path: str):
    import sqlite3

    if not os.path.exists(path):
        return {}
    _ensure_speaker_overrides_sqlite_schema(path)
    try:
        with sqlite3.connect(path) as conn:
            raw = pd.read_sql_query("SELECT * FROM speaker_overrides", conn)
    except Exception as e:
        logger.warning("Unable to read speaker overrides from SQLite %s: %s", path, e)
        return {}

    required = {"event_id", "canonical_speakers"}
    if not required.issubset(set(raw.columns)):
        logger.warning("Speaker overrides file missing required columns: %s", sorted(required))
        return {}

    clean = raw.copy()
    clean["event_id"] = clean["event_id"].astype(str).str.strip()
    clean["canonical_speakers"] = clean["canonical_speakers"].apply(normalize_speaker_text)
    clean = clean[(clean["event_id"] != "") & (clean["canonical_speakers"] != "")]
    if clean.empty:
        return {}
    return dict(zip(clean["event_id"], clean["canonical_speakers"], strict=False))


def load_speaker_overrides(path):
    if _is_sqlite_path(path):
        return _load_speaker_overrides_from_sqlite(path)

    if not os.path.exists(path):
        return {}
    try:
        raw = pd.read_csv(path)
    except Exception as e:
        logger.warning("Unable to read speaker overrides from %s: %s", path, e)
        return {}

    required = {"event_id", "canonical_speakers"}
    if not required.issubset(set(raw.columns)):
        logger.warning("Speaker overrides file missing required columns: %s", sorted(required))
        return {}

    clean = raw.copy()
    clean["event_id"] = clean["event_id"].astype(str).str.strip()
    clean["canonical_speakers"] = clean["canonical_speakers"].apply(normalize_speaker_text)
    clean = clean[(clean["event_id"] != "") & (clean["canonical_speakers"] != "")]
    if clean.empty:
        return {}
    return dict(zip(clean["event_id"], clean["canonical_speakers"], strict=False))


def apply_missing_speaker_overrides(df, overrides):
    if df is None or df.empty or not overrides:
        return df, 0
    if "Event ID" not in df.columns or "Speakers" not in df.columns:
        return df, 0

    out = df.copy()
    out["Event ID"] = out["Event ID"].astype(str).str.strip()
    missing_mask = (
        out["Speakers"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
        .isin({"", "-", "nan", "none", "null", "na", "n/a"})
    )
    mapped = out["Event ID"].map(overrides)
    fill_mask = missing_mask & mapped.notna()
    out.loc[fill_mask, "Speakers"] = mapped[fill_mask]
    return out, int(fill_mask.sum())


def render_responsive_table(df, allow_html_columns=None):
    allow_html_columns = set(allow_html_columns or [])
    safe_df = df.copy()
    for col in safe_df.columns:
        if col in allow_html_columns:
            continue
        safe_df[col] = safe_df[col].apply(lambda v: "" if pd.isna(v) else html.escape(str(v)))
    table_html = safe_df.to_html(index=False, escape=False, classes="dep-table")
    st.markdown(f'<div class="table-wrap">{table_html}</div>', unsafe_allow_html=True)


def render_monthly_calendar(
    df_events, selected_year, selected_month, feedback_df=None, view_mode="grid"
):
    import calendar

    if df_events.empty:
        st.info("No events to display in the community calendar yet.")
        return

    df = df_events.copy()
    df["Date"] = pd.to_datetime(df["Date and Time"], errors="coerce").dt.date
    month_start = pd.Timestamp(selected_year, selected_month, 1).date()
    month_end = pd.Timestamp(
        selected_year, selected_month, calendar.monthrange(selected_year, selected_month)[1]
    ).date()
    month_df = df[df["Date"].between(month_start, month_end)]
    month_df = month_df.sort_values(["Date", "Date and Time"])

    # Precompute feedback by event
    feedback_by_event = {}
    if (
        feedback_df is not None
        and not feedback_df.empty
        and "event_id" in feedback_df.columns
        and "rating" in feedback_df.columns
    ):
        fb = feedback_df.copy()
        fb["rating"] = pd.to_numeric(fb["rating"], errors="coerce")
        fb = fb.dropna(subset=["rating"])
        feedback_by_event = fb.groupby("event_id", observed=True)["rating"].mean().to_dict()

    events_by_day = (
        month_df.groupby("Date", observed=True)
        .apply(lambda d: d.to_dict(orient="records"))
        .to_dict()
    )

    if view_mode == "list":
        st.markdown("### Events List View")
        for day, events in sorted(events_by_day.items()):
            st.markdown(f"**{day.strftime('%b %d, %Y')}**")
            for event in events:
                title = sanitize_title(event.get("Event Title", "Untitled"))
                url = event.get("Event URL", "#")
                typ = event.get("Type", "Past")
                online = event.get("Online?", False)
                dt = pd.to_datetime(event.get("Date and Time"), errors="coerce")
                time_label = dt.strftime("%I:%M %p") if not pd.isna(dt) else "TBD"
                speaker = sanitize_title(event.get("Speakers", ""))
                mode = "🌐 Online" if online else "🏢 In-person"
                event_id = str(event.get("Event ID", "")).strip()
                feedback_url = ""
                if FEEDBACK_FORM_URL:
                    feedback_url = (
                        f"{FEEDBACK_FORM_URL}?event_id={quote_plus(event_id)}"
                        f"&title={quote_plus(title)}"
                    )
                feedback_badge = ""
                if event_id in feedback_by_event:
                    feedback_badge = f" ⭐ {feedback_by_event[event_id]:.1f}"
                st.markdown(
                    f"- [{title}]({html.escape(url)}) [{typ}] — {time_label} — {mode} {f'• {speaker}' if speaker else ''}{feedback_badge} "
                    f"[💬 Feedback]({html.escape(feedback_url)})"
                )
        return

    st.info("Use Month Grid view on the calendar page for interactive booking.")


def _localize_to_event_tz(dt):
    if pd.isna(dt):
        return dt
    if dt.tzinfo is None:
        return dt.tz_localize(DEP_EVENT_TZ)
    return dt.tz_convert(DEP_EVENT_TZ)


def _event_day_tooltip(events, feedback_by_event):
    lines = []
    for event in events:
        title = sanitize_title(event.get("Event Title", "Untitled"))
        dt = pd.to_datetime(event.get("Date and Time"), errors="coerce")
        if not pd.isna(dt):
            dt = _localize_to_event_tz(dt)
        time_label = dt.strftime("%B %d, %Y %I:%M %p") if not pd.isna(dt) else "TBD"
        lines.append(f"{time_label} — {title}")
    return html.escape(" • ".join(lines))


def _calendar_day_state(day, selected_month, events_by_day, today):
    if day.month != selected_month:
        return "other-month"
    has_events = day in events_by_day
    if day < today:
        return "past-event" if has_events else "past"
    if has_events:
        return "event"
    return "open"


def _past_day_tooltip(day):
    return html.escape(f"{day.strftime('%B %d, %Y')} — past date (not bookable)")


def _booking_requests_tooltip(bookings):
    if not bookings:
        return ""
    lines = []
    for booking in bookings:
        start_ts = pd.to_datetime(booking.get("requested_datetime"), errors="coerce")
        if pd.isna(start_ts):
            continue
        start_ts = _localize_to_event_tz(start_ts)
        duration_minutes = int(booking.get("duration_minutes") or DEFAULT_EVENT_DURATION_MINUTES)
        end_ts = start_ts + pd.Timedelta(minutes=duration_minutes)
        speaker = sanitize_title(booking.get("speaker_name", "")).strip()
        status = str(booking.get("status", "Requested")).strip()
        speaker_part = f" by {speaker}" if speaker else ""
        lines.append(
            f"{start_ts.strftime('%I:%M %p')}–{end_ts.strftime('%I:%M %p')}{speaker_part} [{status}]"
        )
    return html.escape(" • ".join(lines))


def render_booking_request_form(events_df, default_date=None, form_key="booking_request_form"):
    bookings = load_event_bookings(EVENT_BOOKINGS_PATH)
    default_day = default_date or date.today()
    if isinstance(default_day, str):
        default_day = pd.to_datetime(default_date).date()

    reset_flag = f"{form_key}_reset"
    saved_flag = "booking_request_saved"
    success_message = st.session_state.pop(saved_flag, False)

    if st.session_state.get(reset_flag):
        for field in [
            f"{form_key}_date",
            f"{form_key}_time",
            f"{form_key}_duration",
            f"{form_key}_speaker",
            f"{form_key}_email",
            f"{form_key}_title",
            f"{form_key}_summary",
            f"{form_key}_format",
            f"{form_key}_notes",
        ]:
            st.session_state.pop(field, None)
        st.session_state.pop(reset_flag, None)

    with st.form(form_key):
        if success_message:
            st.success("Booking request saved.")

        booking_date = st.date_input(
            "Booking date",
            value=st.session_state.get(f"{form_key}_date", default_day),
            min_value=date.today(),
            key=f"{form_key}_date",
        )
        st.markdown(f"**Request a slot on {booking_date.strftime('%B %d, %Y')}**")
        request_time = st.time_input(
            "Start time",
            value=st.session_state.get(f"{form_key}_time", time(18, 0)),
            key=f"{form_key}_time",
        )
        duration_minutes = st.number_input(
            "Duration (minutes)",
            min_value=15,
            max_value=240,
            value=st.session_state.get(f"{form_key}_duration", 60),
            step=15,
            key=f"{form_key}_duration",
        )
        speaker_name = st.text_input("Speaker name", key=f"{form_key}_speaker")
        email = st.text_input(
            "Email (private — used for organizer contact only)",
            key=f"{form_key}_email",
        )
        talk_title = st.text_input("Talk title", key=f"{form_key}_title")
        talk_summary = st.text_area("Talk summary (optional)", key=f"{form_key}_summary")
        preferred_format = st.selectbox(
            "Preferred format",
            ["Online", "In-person", "Hybrid"],
            key=f"{form_key}_format",
            index=["Online", "In-person", "Hybrid"].index(
                st.session_state.get(f"{form_key}_format", "Online")
            ),
        )
        availability_notes = st.text_area(
            "Availability notes (optional)",
            key=f"{form_key}_notes",
        )
        submit_booking = st.form_submit_button("Submit booking request")

    if submit_booking:
        if not speaker_name.strip() or not email.strip() or not talk_title.strip():
            st.warning("Speaker name, email, and talk title are required.")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
            st.warning("Please enter a valid email address.")
        else:
            requested_dt = pd.Timestamp.combine(booking_date, request_time)
            conflict = first_booking_conflict(bookings, requested_dt, int(duration_minutes))
            if conflict is not None:
                st.error(format_booking_conflict_message(conflict))
            else:
                event_end = requested_dt + pd.Timedelta(minutes=int(duration_minutes))
                event_row = first_event_conflict_row(
                    events_df,
                    requested_dt,
                    event_end,
                    existing_duration_minutes=DEFAULT_EVENT_DURATION_MINUTES,
                )
                if event_row is not None:
                    st.error(format_event_conflict_message(event_row))
                else:
                    save_event_booking(
                        EVENT_BOOKINGS_PATH,
                        {
                            "requested_datetime": requested_dt.isoformat(),
                            "duration_minutes": int(duration_minutes),
                            "speaker_name": speaker_name.strip(),
                            "email": email.strip(),
                            "talk_title": talk_title.strip(),
                            "talk_summary": str(talk_summary).strip(),
                            "preferred_format": preferred_format,
                            "availability_notes": str(availability_notes).strip(),
                            "status": "Requested",
                            "submitted_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    _clear_booking_modal()
                    st.session_state["booking_request_saved"] = True
                    st.session_state[reset_flag] = True
                    if hasattr(st, "experimental_set_query_params"):
                        st.experimental_set_query_params()
                    st.rerun()


def _clear_booking_modal():
    if "cal_modal_date" in st.session_state:
        st.session_state.pop("cal_modal_date", None)
    if hasattr(st, "experimental_set_query_params"):
        st.experimental_set_query_params()


def _render_booking_modal(events_df, default_date):
    unique_form_key = f"booking_popup_form_{default_date.isoformat()}"
    if hasattr(st, "modal"):
        with st.modal("Request a booking slot", key=f"booking_modal_{default_date.isoformat()}"):
            render_booking_request_form(events_df, default_date=default_date, form_key=unique_form_key)
            if st.button("Cancel", key=f"booking_modal_cancel_{default_date.isoformat()}"):
                _clear_booking_modal()
                st.rerun()
    else:
        st.warning("Your browser does not support the native popup modal. The booking form is shown below.")
        render_booking_request_form(events_df, default_date=default_date, form_key=unique_form_key)


def _calendar_book_query_param():
    params = st.query_params if hasattr(st, "query_params") else st.experimental_get_query_params()
    raw = params.get("book")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return str(raw).strip() if raw else None


def _calendar_day_meta(events, feedback_by_event, state):
    if not events:
        return ""

    rating_values = []
    for event in events:
        event_id = str(event.get("Event ID", "")).strip()
        if event_id and event_id in feedback_by_event:
            rating_values.append(feedback_by_event[event_id])

    if rating_values:
        average_rating = sum(rating_values) / len(rating_values)
        return f'<span class="calendar-day-meta">⭐{average_rating:.1f}</span>'

    if len(events) > 1:
        return f'<span class="calendar-day-meta">{len(events)} events</span>'

    return '<span class="calendar-day-meta">•</span>'


def _render_calendar_day_cell(day, state, events_by_day, modal_date_raw, feedback_by_event, bookings_by_day):
    day_number = f'<span class="calendar-day-number">{day.day}</span>'
    booking_tooltip = _booking_requests_tooltip(bookings_by_day.get(day, []))
    if state == "open":
        selected = " selected-open" if modal_date_raw == day.isoformat() else ""
        href = f"?book={day.isoformat()}"
        tooltip = f"{day.strftime('%B %d, %Y')} — available for booking."
        if booking_tooltip:
            tooltip = f"{tooltip} Existing requests: {booking_tooltip}"
        return (
            f'<a class="calendar-day-btn open-slot{selected}" href="{href}" title="{html.escape(tooltip)}">'
            f"{day_number}</a>"
        )
    if state in ("event", "past-event"):
        events = events_by_day.get(day, [])
        tooltip = _event_day_tooltip(events, feedback_by_event)
        if booking_tooltip:
            tooltip = f"{tooltip} • {booking_tooltip}" if tooltip else booking_tooltip
        btn_class = "has-event" if state == "event" else "past-event"
        meta = _calendar_day_meta(events, feedback_by_event, state)
        return (
            f'<div class="calendar-day-btn {btn_class}" title="{tooltip}">{day_number}{meta}</div>'
        )
    if state == "past":
        tooltip = _past_day_tooltip(day)
        if booking_tooltip:
            tooltip = f"{tooltip} • {booking_tooltip}"
        return (
            f'<div class="calendar-day-btn past-empty" title="{tooltip}">' 
            f"{day_number}</div>"
        )
    return f'<div class="calendar-day-btn disabled">{day_number}</div>'


def render_calendar_booking_grid(
    df_events,
    events_df,
    selected_year,
    selected_month,
    feedback_df=None,
    narrow_viewport=False,
):
    import calendar

    book_param = _calendar_book_query_param()
    if book_param:
        st.session_state["cal_modal_date"] = book_param

    df = df_events.copy()
    df["Date"] = pd.to_datetime(df["Date and Time"], errors="coerce").dt.date
    events_by_day = (
        df.groupby("Date", observed=True).apply(lambda d: d.to_dict(orient="records")).to_dict()
    )
    bookings_df = load_event_bookings(EVENT_BOOKINGS_PATH)
    bookings_df = bookings_df[~bookings_df["status"].astype(str).str.strip().str.casefold().eq("cancelled")].copy()
    if not bookings_df.empty:
        bookings_df["Date"] = pd.to_datetime(bookings_df["requested_datetime"], errors="coerce").dt.date
        bookings_by_day = bookings_df.groupby("Date", observed=True).apply(lambda d: d.to_dict(orient="records")).to_dict()
    else:
        bookings_by_day = {}
    feedback_by_event = {}
    if feedback_df is not None and not feedback_df.empty:
        fb = feedback_df.copy()
        fb["rating"] = pd.to_numeric(fb["rating"], errors="coerce")
        fb = fb.dropna(subset=["rating"])
        feedback_by_event = fb.groupby("event_id", observed=True)["rating"].mean().round(1).to_dict()
    today = pd.Timestamp.now(tz=DEP_EVENT_TZ).date()
    cal = calendar.Calendar(firstweekday=6)
    month_days = list(cal.monthdatescalendar(selected_year, selected_month))
    modal_date_raw = st.session_state.get("cal_modal_date")

    table_rows = [
        '<table class="calendar-table">',
        '<thead><tr>'
        '<th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th>'
        '<th>Thu</th><th>Fri</th><th>Sat</th>'
        '</tr></thead>',
        '<tbody>',
    ]
    for week in month_days:
        row_cells = ["<tr>"]
        for day in week:
            state = _calendar_day_state(day, selected_month, events_by_day, today)
            row_cells.append(
                '<td class="calendar-day ' +
                ("current-month" if day.month == selected_month else "other-month") +
                '">' +
                _render_calendar_day_cell(day, state, events_by_day, modal_date_raw, feedback_by_event, bookings_by_day) +
                '</td>'
            )
        row_cells.append("</tr>")
        table_rows.append("".join(row_cells))
    table_rows.append("</tbody></table>")
    calendar_html = "".join(table_rows)


    st.markdown(
        """
        <style>
        .dep-calendar-layout .calendar-table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            margin-bottom: 16px;
        }
        .dep-calendar-layout .calendar-table-wrapper {
            overflow-x: auto;
            margin-bottom: 16px;
        }
        .dep-calendar-layout .calendar-table th,
        .dep-calendar-layout .calendar-table td {
            border: 1px solid #d7e2f1;
            padding: 4px;
            vertical-align: top;
        }
        .dep-calendar-layout .calendar-table th {
            background: #1d4ed8;
            color: #fff;
            font-weight: 700;
            padding: 10px 6px;
            font-size: 0.82rem;
            text-align: center;
        }
        .dep-calendar-layout .calendar-day {
            min-height: 84px;
            padding: 4px;
        }
        .dep-calendar-layout .calendar-day.current-month {
            background: #f8fbff;
        }
        .dep-calendar-layout .calendar-day.other-month {
            background: #f8fafc;
            opacity: 0.55;
        }
        .dep-calendar-layout .calendar-day-btn {
            width: 100%; min-height: 64px; border: 1px solid #d7e2f1; border-radius: 10px;
            font-weight: 700; font-size: 0.9rem; color: #0f172a; display: flex;
            flex-direction: column; align-items: center; justify-content: center;
            box-sizing: border-box; text-decoration: none; cursor: default;
            padding: 8px;
            text-align: center;
            white-space: normal;
        }
        .dep-calendar-layout .calendar-day-btn.has-event {
            background: #e0ecff; color: #1d4ed8; box-shadow: inset 0 0 0 2px #1d4ed8;
        }
        .dep-calendar-layout .calendar-day-btn.past-event {
            background: #eef2ff; color: #475569; box-shadow: inset 0 0 0 2px #94a3b8;
        }
        .dep-calendar-layout .calendar-day-btn.past-empty {
            background: #f1f5f9; color: #94a3b8;
        }
        .dep-calendar-layout .calendar-day-btn.disabled {
            background: #f8fafc; color: #94a3b8; opacity: 0.55;
        }
        .dep-calendar-layout .calendar-day-btn.open-slot {
            background: transparent;
            color: #065f46 !important;
            border-color: #10b981;
            box-shadow: inset 0 0 0 1px rgba(16, 185, 129, 0.25);
            cursor: pointer;
            text-decoration: none;
        }
        .dep-calendar-layout .calendar-day-btn.open-slot:visited {
            color: #065f46 !important;
        }
        .dep-calendar-layout .calendar-day-btn.open-slot:hover {
            background: rgba(16, 185, 129, 0.08);
        }
        .dep-calendar-layout .calendar-day-btn.selected-open {
            background: #10b981;
            color: #fff;
            box-shadow: inset 0 0 0 2px #047857;
        }
        .dep-calendar-layout .booking-modal-summary {
            margin-bottom: 12px;
            color: #475569;
        }
        .dep-calendar-layout .calendar-day-btn.open-slot .calendar-day-number {
            text-decoration: underline;
        }
        .dep-calendar-layout .calendar-day-number {
            display: block;
            font-size: 1rem;
        }
        .dep-calendar-layout .calendar-day-meta {
            display: block;
            margin-top: 4px;
            font-size: 0.72rem;
            font-weight: 600;
            opacity: 0.85;
        }
        .dep-calendar-layout .calendar-day-btn.has-event .calendar-day-meta {
            color: #1d4ed8;
        }
        .dep-calendar-layout .calendar-day-btn.past-event .calendar-day-meta {
            color: #475569;
        }
        .dep-calendar-layout .calendar-day-btn.open-slot .calendar-day-meta {
            background: #10b981;
            color: #fff;
            border-radius: 999px;
            padding: 0 6px;
            line-height: 1.4;
            margin-top: 6px;
        }
        .dep-calendar-layout .calendar-day.compact.other-month .calendar-day-btn {
            opacity: 0.55;
            background: #f8fafc;
        }
        .dep-calendar-layout .calendar-legend {
            font-size: 0.85rem; color: #475569; margin-bottom: 10px;
        }
        .dep-calendar-layout .calendar-legend span {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            margin-right: 16px;
        }
        .dep-calendar-layout .calendar-legend .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }
        .dep-calendar-layout .calendar-legend .legend-open { background: #10b981; }
        .dep-calendar-layout .calendar-legend .legend-event { background: #1d4ed8; }
        .dep-calendar-layout .calendar-legend .legend-past { background: #94a3b8; }
        .dep-calendar-layout [data-testid="stForm"],
        .dep-calendar-layout [data-testid="stForm"] > div {
            background: transparent !important; border: none !important;
            box-shadow: none !important; padding: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="dep-calendar-layout">', unsafe_allow_html=True)
    st.markdown('<div class="calendar-legend">'
                '<span><span class="legend-dot legend-open"></span>Open for booking</span>'
                '<span><span class="legend-dot legend-event"></span>Event day</span>'
                '<span><span class="legend-dot legend-past"></span>Past event</span>'
                '</div>', unsafe_allow_html=True)
    st.markdown('<div class="calendar-table-wrapper">' + calendar_html + '</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def format_event_link(title, url):
    safe_title = html.escape(sanitize_title(title))
    raw_url = str(url).strip()
    parsed = urlparse(raw_url)
    safe_url = raw_url if parsed.scheme in {"http", "https"} else "#"
    return f'<a href="{html.escape(safe_url)}" target="_blank" rel="noopener noreferrer">{safe_title}</a>'


def format_feedback_link(event_id, title):
    if not FEEDBACK_FORM_URL:
        return ""
    safe_title = sanitize_title(title)
    event_id = str(event_id).strip()
    url = (
        f"{FEEDBACK_FORM_URL}?event_id={quote_plus(event_id)}"
        f"&title={quote_plus(safe_title)}"
    )
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">Feedback</a>'


# -------------------
# API Call
# -------------------
def gql_request(query, variables):
    headers = {"Content-Type": "application/json"}
    if MEETUP_TOKEN:
        headers["Authorization"] = f"Bearer {MEETUP_TOKEN}"

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                API_URL,
                headers=headers,
                json={"query": query, "variables": variables},
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("errors"):
                st.warning(
                    f"Meetup GraphQL returned errors: {payload['errors'][0].get('message', 'Unknown error')}"
                )
            return payload
        except requests.exceptions.HTTPError as e:
            status_code = getattr(e.response, "status_code", None)
            retry_after = None
            if getattr(e, "response", None) is not None:
                retry_after = e.response.headers.get("Retry-After")
            if status_code == 503:
                st.warning(f"Meetup API unavailable (503). Retrying... ({attempt+1}/{MAX_RETRIES})")
            elif status_code == 429:
                wait_seconds = int(retry_after) if str(retry_after).isdigit() else 10
                st.warning(f"Rate limit hit (429). Waiting {wait_seconds} seconds before retry...")
                time.sleep(wait_seconds)
            else:
                st.error(f"HTTP Error {status_code}: {e}")
            if attempt < MAX_RETRIES - 1:
                backoff = RETRY_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.35)
                logger.warning(
                    "API request failed with HTTP %s. retry_in=%.2fs", status_code, backoff
                )
                time.sleep(backoff)
            else:
                raise
        except requests.exceptions.RequestException as e:
            st.warning(f"Attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                backoff = RETRY_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.35)
                logger.warning("API request exception. retry_in=%.2fs error=%s", backoff, e)
                time.sleep(backoff)
            else:
                raise


def group_node(payload):
    return payload.get("data", {}).get("groupByUrlname") or {}


# -------------------
# Pagination
# -------------------
def gql_paginated(query, urlname, page_size):
    all_edges = []
    has_next_page = True
    after = None

    while has_next_page:
        variables = {"urlname": urlname, "first": page_size}
        if after:
            variables["after"] = after
        data = gql_request(query, variables)
        events = group_node(data).get("events", {})
        edges = events.get("edges", [])
        all_edges.extend(edges)
        page_info = events.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)
        after = page_info.get("endCursor", None)
    return all_edges


# -------------------
# Data Loader
# -------------------
@st.cache_data(ttl=900, show_spinner=False)
def load_data(urlname):
    # --- Upcoming ---
    up_data = gql_request(QUERY_UPCOMING, {"urlname": urlname, "first": 20})
    up_edges = group_node(up_data).get("events", {}).get("edges", [])

    now_ts = pd.Timestamp.now(tz="utc")
    upcoming_rows = []
    past_from_upcoming = []
    for e in up_edges:
        dt_str = e["node"].get("dateTime")
        event_dt = pd.to_datetime(dt_str, errors="coerce")
        if pd.isna(event_dt):
            continue
        if event_dt.tzinfo is None:
            event_dt = event_dt.tz_localize("utc")
        else:
            event_dt = event_dt.tz_convert("utc")
        if event_dt < now_ts:
            past_from_upcoming.append(
                {
                    "Event ID": e["node"]["id"],
                    "Event Title": e["node"]["title"],
                    "Date and Time": e["node"]["dateTime"],
                    "Event URL": e["node"]["eventUrl"],
                    "Online?": e["node"].get("eventType") in ("ONLINE", "HYBRID"),
                    "No. of Attendees": (e["node"].get("rsvps") or {}).get("yesCount"),
                    "Speakers": format_speakers(e["node"].get("speakerDetails")),
                }
            )
            continue
        upcoming_rows.append(
            {
                "Event ID": e["node"]["id"],
                "Event Title": e["node"]["title"],
                "Date and Time": e["node"]["dateTime"],
                "Event URL": e["node"]["eventUrl"],
                "Online?": e["node"].get("eventType") in ("ONLINE", "HYBRID"),
                "Speakers": format_speakers(e["node"].get("speakerDetails")),
            }
        )
    df_up = pd.DataFrame(upcoming_rows) if upcoming_rows else pd.DataFrame(
        columns=["Event ID", "Event Title", "Date and Time", "Event URL", "Online?", "Speakers"]
    )

    # --- Past ---
    past_edges = gql_paginated(QUERY_PAST, urlname, page_size=100)

    df_past = (
        pd.DataFrame(
            [
                {
                    "Event ID": e["node"]["id"],
                    "Event Title": e["node"]["title"],
                    "Date and Time": e["node"]["dateTime"],
                    "Event URL": e["node"]["eventUrl"],
                    "Online?": e["node"].get("eventType") in ("ONLINE", "HYBRID"),
                    "No. of Attendees": (e["node"].get("rsvps") or {}).get("yesCount"),
                    "Speakers": format_speakers(e["node"].get("speakerDetails")),
                }
                for e in past_edges
            ]
        )
        if past_edges
        else pd.DataFrame(
            columns=[
                "Event ID",
                "Event Title",
                "Date and Time",
                "Event URL",
                "Online?",
                "No. of Attendees",
                "Speakers",
            ]
        )
    )

    if past_from_upcoming:
        df_past = pd.concat([df_past, pd.DataFrame(past_from_upcoming)], ignore_index=True)
        df_past = df_past.drop_duplicates(subset=["Event ID"], keep="first")

    return df_up, df_past


# -------------------
# Safe Metric Helper
# -------------------
def _get_s3_client():
    if not SNAPSHOT_S3_BUCKET:
        return None
    try:
        import boto3

        return boto3.client("s3")
    except Exception as e:
        logger.warning("Unable to initialize S3 client for snapshots: %s", e)
        return None


def save_snapshot(df_up, df_past, member_count):
    payload = {
        "saved_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "member_count": int(member_count or 0),
        "upcoming": df_up.to_dict(orient="records"),
        "past": df_past.to_dict(orient="records"),
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if SNAPSHOT_BACKEND == "s3":
        s3 = _get_s3_client()
        if s3 is not None:
            s3.put_object(
                Bucket=SNAPSHOT_S3_BUCKET,
                Key=SNAPSHOT_S3_KEY,
                Body=encoded,
                ContentType="application/json",
            )
            return
        logger.warning("S3 snapshot backend requested but unavailable. Falling back to file.")

    if _is_sqlite_path(SNAPSHOT_PATH):
        _save_snapshot_to_sqlite(SNAPSHOT_PATH, payload)
        return

    directory = os.path.dirname(SNAPSHOT_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{SNAPSHOT_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(encoded.decode("utf-8"))
    os.replace(tmp_path, SNAPSHOT_PATH)


def load_snapshot():
    payload = None
    if SNAPSHOT_BACKEND == "s3":
        s3 = _get_s3_client()
        if s3 is not None:
            try:
                response = s3.get_object(Bucket=SNAPSHOT_S3_BUCKET, Key=SNAPSHOT_S3_KEY)
                payload = json.loads(response["Body"].read().decode("utf-8"))
            except Exception as e:
                logger.warning("Unable to load S3 snapshot: %s", e)

    if payload is None:
        if _is_sqlite_path(SNAPSHOT_PATH):
            payload = _load_snapshot_from_sqlite(SNAPSHOT_PATH)
        if payload is None:
            if not os.path.exists(SNAPSHOT_PATH):
                return None
            with open(SNAPSHOT_PATH, encoding="utf-8") as f:
                payload = json.load(f)

    df_up = pd.DataFrame(payload.get("upcoming", []))
    df_past = pd.DataFrame(payload.get("past", []))
    return {
        "df_up": df_up,
        "df_past": df_past,
        "member_count": int(payload.get("member_count", 0)),
        "saved_at": payload.get("saved_at"),
    }


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner=False)
def get_dashboard_data(urlname):
    overrides = load_speaker_overrides(SPEAKER_OVERRIDES_PATH)
    try:
        df_up, df_past = load_data(urlname)
        df_up = normalize_speaker_column(df_up)
        df_past = normalize_speaker_column(df_past)
        df_past, override_count = apply_missing_speaker_overrides(df_past, overrides)
        member_data = gql_request(QUERY_MEMBERS, {"urlname": urlname})
        member_count = (
            group_node(member_data).get("stats", {}).get("memberCounts", {}).get("all", 0)
        )
        try:
            save_snapshot(df_up, df_past, member_count)
        except Exception as snapshot_error:
            logger.warning("Snapshot save failed: %s", snapshot_error)
        return {
            "df_up": df_up,
            "df_past": df_past,
            "member_count": int(member_count or 0),
            "source": "Live API",
            "saved_at": pd.Timestamp.utcnow().isoformat(),
            "override_count": override_count,
        }
    except Exception as e:
        snapshot = load_snapshot()
        if snapshot:
            st.warning(
                f"Live Meetup API unavailable ({type(e).__name__}). Showing last successful snapshot."
            )
            snap_up = normalize_speaker_column(snapshot["df_up"])
            snap_past = normalize_speaker_column(snapshot["df_past"])
            snap_past, override_count = apply_missing_speaker_overrides(snap_past, overrides)
            return {
                "df_up": snap_up,
                "df_past": snap_past,
                "member_count": snapshot["member_count"],
                "source": "Snapshot fallback",
                "saved_at": snapshot["saved_at"],
                "override_count": override_count,
            }
        st.error(f"Unable to load live data and no snapshot is available: {e}")
        return {
            "df_up": pd.DataFrame(
                columns=[
                    "Event ID",
                    "Event Title",
                    "Date and Time",
                    "Event URL",
                    "Online?",
                    "Speakers",
                ]
            ),
            "df_past": pd.DataFrame(
                columns=[
                    "Event ID",
                    "Event Title",
                    "Date and Time",
                    "Event URL",
                    "Online?",
                    "No. of Attendees",
                    "Speakers",
                ]
            ),
            "member_count": 0,
            "source": "Unavailable",
            "saved_at": None,
            "override_count": 0,
        }


def render_feedback_submission(feedback_df, df_up, df_past):
    event_list = df_past.copy()
    event_list = event_list.dropna(subset=["Event ID", "Event Title"]).copy()
    event_list["Event ID"] = event_list["Event ID"].astype(str).str.strip()
    event_list["Event Title"] = event_list["Event Title"].apply(sanitize_title)
    event_map = {
        row["Event ID"]: row["Event Title"]
        for _, row in event_list.drop_duplicates(subset=["Event ID"]).iterrows()
    }

    feedback_by_event = {}
    if feedback_df is not None and not feedback_df.empty:
        fb = feedback_df.copy()
        fb["rating"] = pd.to_numeric(fb["rating"], errors="coerce")
        fb = fb.dropna(subset=["rating"])
        feedback_by_event = fb.groupby("event_id", observed=True)["rating"].mean().round(1).to_dict()

    page = st.session_state.get("DEP_PAGE", "all")
    with st.expander("Submit feedback for an event", expanded=page == "feedback"):
        if feedback_by_event:
            total_ratings = len(feedback_df)
            avg_score = feedback_df["rating"].astype(float).mean()
            st.markdown(
                f"<div style='margin-bottom:8px;'>Existing feedback: {total_ratings} rating{'s' if total_ratings != 1 else ''} submitted, average score {avg_score:.1f}</div>",
                unsafe_allow_html=True,
            )
        event_options = ["-- Select event --"] + [f"{eid} — {title}" for eid, title in event_map.items()]
        star_options = [
            "⭐☆☆☆☆ (1)",
            "⭐⭐☆☆☆ (2)",
            "⭐⭐⭐☆☆ (3)",
            "⭐⭐⭐⭐☆ (4)",
            "⭐⭐⭐⭐⭐ (5)",
        ]
        with st.form("feedback_form"):
            selected_event = st.selectbox(
                "Choose event to rate",
                options=event_options,
                index=0,
                key="feedback_event_select",
            )
            selected_rating = st.selectbox(
                "Rating",
                options=star_options,
                index=4,
                key="feedback_star_rating",
            )
            rating = selected_rating.count("⭐")
            comment = st.text_area("Comments (optional)")
            submit_feedback = st.form_submit_button("Submit feedback")
            if submit_feedback:
                if selected_event == "-- Select event --":
                    st.warning("Please select an event before submitting feedback.")
                else:
                    event_id = selected_event.split(" — ", 1)[0]
                    if event_id in feedback_by_event:
                        st.warning(
                            "Feedback is already submitted for this event. "
                            "Only one response per event is allowed."
                        )
                    else:
                        save_feedback_data(
                            FEEDBACK_DATA_PATH,
                            {
                                "event_id": event_id,
                                "event_title": event_map.get(event_id, ""),
                                "rating": int(rating),
                                "comment": str(comment).strip(),
                                "submitted_at": datetime.utcnow().isoformat() + "Z",
                            },
                        )
                        st.success("Thank you! Your feedback has been recorded.")
                        st.rerun()


def render_community_calendar_section(feedback_df, df_up, df_past, narrow_viewport=False):
    st.markdown('<div id="calendar"></div>', unsafe_allow_html=True)
    st.subheader("DEP Community Calendar")

    calendar_data = pd.concat(
        [
            df_up.assign(Type="Upcoming") if not df_up.empty else df_up,
            df_past.assign(Type="Past") if not df_past.empty else df_past,
        ],
        ignore_index=True,
    )
    calendar_data["Date and Time"] = pd.to_datetime(calendar_data.get("Date and Time"), errors="coerce")
    calendar_data = calendar_data.dropna(subset=["Date and Time"]).sort_values("Date and Time")

    if calendar_data.empty:
        st.info("No events available to render the calendar yet.")
        return

    now = pd.Timestamp.now()
    selected_year, selected_month = st.columns(2)
    year = selected_year.number_input(
        "Year", min_value=2000, max_value=2100, value=int(now.year), step=1
    )
    month = selected_month.selectbox(
        "Month",
        list(range(1, 13)),
        index=int(now.month) - 1,
        format_func=lambda m: pd.Timestamp(year=int(year), month=m, day=1).strftime("%B"),
    )
    view_mode = st.radio(
        "Calendar view",
        options=["Month Grid", "List"],
        index=0 if not narrow_viewport else 1,
        horizontal=True,
    )
    st.caption(
        "Hover event days for session details. "
        "Click a green underlined date to request a speaker slot."
    )
    if view_mode == "List":
        render_monthly_calendar(
            calendar_data,
            int(year),
            int(month),
            feedback_df=feedback_df,
            view_mode="list",
        )
    else:
        render_calendar_booking_grid(
            calendar_data,
            calendar_data,
            int(year),
            int(month),
            feedback_df=feedback_df,
            narrow_viewport=narrow_viewport,
        )


def render_booking_section(events_df):
    st.subheader("Speaker Booking Requests")
    st.caption(
        "Select an available date from the calendar above. A popup booking form will appear; if your browser does not support popups, the form will render here."
    )

    modal_date_raw = st.session_state.get("cal_modal_date")
    booking_saved = st.session_state.get("booking_request_saved", False)
    if booking_saved and not modal_date_raw:
        st.session_state.pop("booking_request_saved", None)
        st.success("Booking request saved.")

    if not modal_date_raw:
        st.info("Tap the underlined date to request a speaker slot.")
        return

    try:
        modal_date = pd.to_datetime(modal_date_raw).date()
    except (TypeError, ValueError):
        modal_date = None

    if not modal_date:
        st.warning("The selected booking date is invalid. Please choose another open date.")
        return

    st.markdown(f"### Booking request for {modal_date.strftime('%B %d, %Y')}")
    _render_booking_modal(events_df, default_date=modal_date)


def render_admin_booking_page(bookings_df):
    st.subheader("Moderator dashboard")
    st.markdown(
        "Use this page to review speaker booking requests and update request status. "
        "Changes are saved back to the booking storage file."
    )

    if bookings_df is None or bookings_df.empty:
        st.info("No booking requests have been submitted yet.")
        return

    all_bookings = bookings_df.copy()
    status_filter = st.selectbox(
        "Filter by status",
        options=["All", "Requested", "Approved", "Confirmed", "Cancelled", "Tentative"],
        index=0,
    )

    visible_bookings = all_bookings
    if status_filter != "All":
        visible_bookings = all_bookings[
            all_bookings["status"].astype(str).str.strip().str.casefold()
            == status_filter.casefold()
        ].copy()

    if visible_bookings.empty:
        st.info("No booking requests match the selected filter.")
        return

    with st.form("admin_booking_form"):
        changed = False
        st.markdown(f"**{len(visible_bookings)} booking request(s)** currently visible.")

        for index, booking in visible_bookings.iterrows():
            requested_dt = booking.get("requested_datetime", "")
            duration = int(booking.get("duration_minutes") or DEFAULT_EVENT_DURATION_MINUTES)
            speaker_name = str(booking.get("speaker_name", "")).strip() or "Unknown speaker"
            talk_title = str(booking.get("talk_title", "")).strip() or "Untitled talk"
            submitted_at = booking.get("submitted_at", "")
            current_status = str(booking.get("status", "Requested")).strip() or "Requested"
            expander_label = f"{speaker_name} — {talk_title} [{current_status}]"
            with st.expander(expander_label, expanded=False):
                st.write(
                    f"**Requested:** {_display_timestamp(requested_dt, duration)}  \n"
                    f"**Email:** {booking.get('email', '')}  \n"
                    f"**Duration:** {duration} minutes  \n"
                    f"**Format:** {booking.get('preferred_format', '')}  \n"
                    f"**Submitted:** {submitted_at}  \n"
                    f"**Notes:** {booking.get('availability_notes', '') or 'None'}  \n"
                    f"**Talk summary:** {booking.get('talk_summary', '') or 'None'}"
                )
                status_key = f"admin_status_{index}"
                selected_status = st.selectbox(
                    "Status",
                    options=["Requested", "Approved", "Confirmed", "Cancelled", "Tentative"],
                    index=["Requested", "Approved", "Confirmed", "Cancelled", "Tentative"].index(current_status)
                    if current_status in ["Requested", "Approved", "Confirmed", "Cancelled", "Tentative"]
                    else 0,
                    key=status_key,
                )
                if selected_status != current_status:
                    all_bookings.loc[index, "status"] = selected_status
                    changed = True

        submit = st.form_submit_button("Save booking updates")

    if submit:
        if changed:
            save_event_bookings(EVENT_BOOKINGS_PATH, all_bookings)
            st.success("Booking request status updates saved.")
            safe_rerun()
        else:
            st.info("No status changes were made.")


# -------------------
# Main App
# -------------------
file_path = os.path.join(ROOT_DIR, "assets", "dep_logo.png")
try:
    with open(file_path, "rb") as f:
        data = f.read()
    logo = base64.b64encode(data).decode()
except OSError as e:
    logger.warning("Logo load failed from %s: %s", file_path, e)
    logo = ""
logo_html = f'<img src="data:image/png;base64,{logo}" class="header-logo">' if logo else ""

if __name__ == "__main__":
    try:
        st.sidebar.image(file_path, width=120)
    except Exception as e:
        logger.warning("Sidebar logo render failed: %s", e)

def main():
    st.set_page_config(page_title="DEP Meetup Dashboard", layout="wide")

    if __name__ == "__main__" or "DEP_PAGE" not in st.session_state:
        st.session_state["DEP_PAGE"] = "all"
        os.environ["DEP_PAGE"] = "all"

    PAGE_VIEW = (
        st.session_state.get("DEP_PAGE")
        if "DEP_PAGE" in st.session_state
        else os.getenv("DEP_PAGE", "all")
    ).strip().lower()

    render_moderator_sidebar()

    dashboard = get_dashboard_data(URLNAME)
    df_up = dashboard["df_up"]
    df_past = dashboard["df_past"]
    member_count = dashboard["member_count"]
    pulse_source = dashboard["source"]
    pulse_saved_at = dashboard["saved_at"]
    feedback_df = load_feedback_data(FEEDBACK_DATA_PATH)
    pulse = compute_pulse(member_count, df_up, df_past)
    logger.info("Dashboard data loaded. source=%s members=%s", pulse_source, member_count)

    st.markdown(
        f"""
        <style>
            :root {{
                --header-height: 128px;
            }}
            .stApp {{
                background: radial-gradient(circle at 15% 0%, #eaf2ff 0%, #f8fbff 45%, #f3f7fc 100%);
            }}
            div[data-testid="stSidebarNav"] li:first-child a {{
                position: relative;
            }}
            div[data-testid="stSidebarNav"] li:first-child a span {{
                opacity: 0;
            }}
            div[data-testid="stSidebarNav"] li:first-child a::after {{
                content: "Homepage";
                position: absolute;
                left: 0;
                top: 0;
                color: inherit;
            }}
            html {{
                scroll-padding-top: 140px;
            }}
            .header-container {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 12px;
                padding: 8px 16px 8px 16px;
                max-width: 1100px;
                margin: 0 auto;
            }}
            .header-inner {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                width: 100%;
            }}
            .header-left {{
                display: flex;
                align-items: center;
                gap: 12px;
                min-width: 320px;
            }}
            .mobile-logo {{
                display: none;
            }}
            .header-bar {{
                position: sticky;
                top: 0;
                z-index: 1200;
                background: radial-gradient(circle at 15% 0%, #eaf2ff 0%, #f8fbff 45%, #f3f7fc 100%);
                backdrop-filter: blur(2px);
                border-bottom: 1px solid #d7e2f1;
                box-shadow: 0 6px 12px rgba(15, 23, 42, 0.08);
                padding: 6px 0 6px 0;
            }}
            .header-spacer {{
                height: var(--header-height);
            }}
            .header-logo {{
                height: 3rem;
                width: auto;
                max-height: none;
                display: block;
                margin: 0;
                border-radius: 10px;
                box-shadow: 0 4px 10px rgba(0,0,0,0.08);
            }}
            .header-title-wrap {{
                display: flex;
                flex-direction: column;
                text-align: left;
                margin-left: 6px;
            }}
            .header-title {{
                font-size: 1.4rem;
                font-weight: 800;
                color: #0f172a;
                letter-spacing: 0.2px;
                margin: 0;
                line-height: 1.1;
            }}
            .header-subtitle {{
                color: #334155;
                margin-top: 2px;
                font-size: 0.8rem;
                margin: 0;
            }}
            .pulse-wrap {{
                margin: 4px 0 18px 0;
                background: linear-gradient(120deg, #0f172a 0%, #1d4ed8 55%, #0ea5e9 100%);
                color: #f8fafc;
                border-radius: 16px;
                padding: 16px 18px;
                box-shadow: 0 12px 24px rgba(15, 23, 42, 0.2);
            }}
            .pulse-title {{
                font-size: 0.85rem;
                text-transform: uppercase;
                letter-spacing: 1.1px;
                opacity: 0.9;
            }}
            .pulse-main {{
                display: flex;
                align-items: baseline;
                gap: 10px;
                margin-top: 2px;
            }}
            .pulse-score {{
                font-size: 2.5rem;
                font-weight: 800;
                line-height: 1;
            }}
            .pulse-label {{
                font-size: 1rem;
                font-weight: 600;
                opacity: 0.95;
            }}
            .pulse-meta {{
                margin-top: 10px;
                font-size: 0.88rem;
                opacity: 0.95;
            }}
            .pulse-spark {{
                margin-top: 10px;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 1.15rem;
                letter-spacing: 1.2px;
                opacity: 0.98;
            }}
            .leader-card {{
                background: #ffffff;
                border: 1px solid #d7e2f1;
                border-radius: 12px;
                padding: 12px 14px;
                box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06);
                margin-bottom: 10px;
            }}
            .leader-rank {{
                font-size: 0.8rem;
                color: #64748b;
                text-transform: uppercase;
                margin-bottom: 4px;
            }}
            .leader-name {{
                font-size: 1.03rem;
                color: #0f172a;
                font-weight: 700;
                margin-bottom: 3px;
            }}
            .leader-count {{
                font-size: 0.9rem;
                color: #1e293b;
            }}
            .table-wrap {{
                width: 100%;
                overflow-x: auto;
                border: 1px solid #d7e2f1;
                border-radius: 10px;
                background: #ffffff;
            }}
            .dep-table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: auto;
                font-size: 0.93rem;
            }}
            .dep-table th {{
                background: #f8fbff;
                color: #0f172a;
                text-align: left;
                padding: 10px 12px;
                border-bottom: 1px solid #d7e2f1;
                white-space: nowrap;
            }}
            .dep-table td {{
                padding: 10px 12px;
                border-bottom: 1px solid #eef3fa;
                vertical-align: top;
                white-space: normal;
                word-break: break-word;
            }}
            .dep-table tr:last-child td {{
                border-bottom: none;
            }}
            h2, .stSubheader {{
                font-size: 1.25rem;
                margin-top: 0.6rem;
            }}
            .footer-text {{
                color: #475569;
                font-size: 0.9rem;
                margin: 32px auto 12px auto;
                max-width: 1100px;
                text-align: center;
                opacity: 0.85;
            }}
            @media (max-width: 768px) {{
                .header-inner {{
                    align-items: center;
                    flex-wrap: wrap;
                    justify-content: center;
                }}
                .header-container {{
                    flex-direction: column;
                    align-items: center;
                    text-align: center;
                }}
                .header-title-wrap {{
                    text-align: center;
                    margin-left: 0;
                }}
                .header-title {{
                    font-size: 1.45rem;
                }}
                .header-subtitle {{
                    font-size: 0.9rem;
                }}
                .mobile-logo {{
                    display: block;
                    height: 2.6rem;
                    width: auto;
                    border-radius: 8px;
                    box-shadow: 0 3px 8px rgba(15, 23, 42, 0.15);
                }}
                .pulse-score {{
                    font-size: 2rem;
                }}
                .pulse-meta {{
                    font-size: 0.8rem;
                }}
                .pulse-spark {{
                    font-size: 0.95rem;
                    letter-spacing: 0.8px;
                }}
                .dep-table {{
                    font-size: 0.85rem;
                }}
                .dep-table th, .dep-table td {{
                    padding: 8px 9px;
                }}
            }}
        </style>

        <div class="header-bar">
            <div class="header-container">
                <div class="header-inner">
                    <div class="header-left">
                        {f'<img src="data:image/png;base64,{logo}" class="mobile-logo">' if logo else ""}
                        <div class="header-title-wrap">
                            <div class="header-title">Data Engineering Pilipinas Meetup Dashboard</div>
                            <div class="header-subtitle">Live community pulse: events, attendance, and growth</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


    viewport_width = get_viewport_width()
    is_narrow = viewport_width is not None and viewport_width < 800
    if viewport_width is not None:
        st.session_state["vw_param"] = viewport_width

    if PAGE_VIEW == "all":
        if PAGE_VIEW == "insights":
            st.markdown(
                f"""
                <div class="pulse-wrap">
                  <div class="pulse-title">Community Pulse Score</div>
                  <div class="pulse-main">
                    <div class="pulse-score">{pulse['score']}/100</div>
                    <div class="pulse-label">{pulse['label']}</div>
                  </div>
                  <div class="pulse-meta">
                    Community {pulse['community']} | Activity {pulse['activity']} | Attendance {pulse['attendance']} | Momentum {pulse['momentum']}
                  </div>
                  <div class="pulse-spark">Attendance trend: {pulse['sparkline']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.caption(f"Data source: {pulse_source} | Last refresh: {pulse_saved_at or 'n/a'}")
    if pulse_source == "Unavailable":
        st.error(
            "Live Meetup data is unavailable and no snapshot was found. "
            "Set MEETUP_TOKEN or provide a snapshot to see real data."
        )
    elif pulse_source != "Live API":
        st.warning(
            "Showing snapshot data because live Meetup API is unavailable. "
            "Some metrics may be out of date."
        )
    compact_view = st.toggle(
        "Compact mobile view",
        value=is_narrow,
        help="Use shorter tables and tighter text for small screens.",
        key="compact_view",
    )

    def render_meetup_events_section(feedback_df=None):
        st.markdown('<div id="events"></div>', unsafe_allow_html=True)
        st.subheader("Meetup Events")
        raw_controls = st.columns(3)
        raw_year = raw_controls[0]
        raw_month = raw_controls[1]
        raw_reset = raw_controls[2]

        feedback_by_event = {}
        feedback_counts = {}
        if feedback_df is not None and not feedback_df.empty:
            fb = feedback_df.copy()
            fb["rating"] = pd.to_numeric(fb["rating"], errors="coerce")
            fb = fb.dropna(subset=["rating"])
            feedback_by_event = fb.groupby("event_id", observed=True)["rating"].mean().round(1).to_dict()
            feedback_counts = fb.groupby("event_id", observed=True).size().to_dict()

        raw_all = pd.concat([df_up, df_past], ignore_index=True)
        raw_all["Date and Time"] = pd.to_datetime(raw_all.get("Date and Time"), errors="coerce")
        raw_all = raw_all.dropna(subset=["Date and Time"])
        available_years = sorted(raw_all["Date and Time"].dt.year.unique().tolist())
        past_year = None
        if not df_past.empty:
            past_years = pd.to_datetime(df_past.get("Date and Time"), errors="coerce").dt.year.dropna()
            if not past_years.empty:
                past_year = int(past_years.max())
        default_year = past_year or (available_years[-1] if available_years else pd.Timestamp.now().year)
        year_index = available_years.index(default_year) if default_year in available_years else 0
        year_value = raw_year.selectbox("Filter year", options=available_years, index=year_index)

        month_names = ["All"] + [pd.Timestamp(2000, m, 1).strftime("%B") for m in range(1, 13)]
        month_value = raw_month.selectbox("Filter month", options=month_names, index=0)
        if raw_reset.button("Clear filters"):
            year_value = default_year
            month_value = "All"

        def _month_number(name):
            if name == "All":
                return None
            for idx, label in enumerate(month_names[1:], start=1):
                if label == name:
                    return idx
            return None

        def filter_raw(df):
            if df.empty:
                return df
            out = df.copy()
            out["Date and Time"] = pd.to_datetime(out.get("Date and Time"), errors="coerce")
            out = out.dropna(subset=["Date and Time"])
            out = out[out["Date and Time"].dt.year == int(year_value)]
            month_num = _month_number(month_value)
            if month_num:
                out = out[out["Date and Time"].dt.month == month_num]
            return out

        tab1, tab2 = st.tabs(["Upcoming Events", "Past Events"])

        with tab1:
            df_up_filtered = filter_raw(df_up)
            if not df_up_filtered.empty:
                df_up_display = df_up_filtered.copy()
                date_fmt = "%a, %b %d, %Y" if compact_view else "%a, %b %d, %Y %I:%M %p"
                df_up_display["Date and Time"] = pd.to_datetime(df_up_display["Date and Time"]).dt.strftime(
                    date_fmt
                )
                df_up_display["Event Title"] = df_up_display.apply(
                    lambda row: format_event_link(row["Event Title"], row["Event URL"]),
                    axis=1,
                )
                def _event_feedback_cell(row):
                    event_id = str(row.get("Event ID", "")).strip()
                    link = format_feedback_link(event_id, row.get("Event Title", ""))
                    if event_id in feedback_by_event:
                        avg_rating = feedback_by_event[event_id]
                        count = feedback_counts.get(event_id, 0)
                        count_label = f" ({count})" if count > 1 else ""
                        return f"{link} ⭐ {avg_rating:.1f}{count_label}"
                    return link

                df_up_display["Feedback"] = df_up_display.apply(_event_feedback_cell, axis=1)
                if compact_view:
                    df_up_display = df_up_display[["Event Title", "Date and Time", "Feedback"]]
                else:
                    df_up_display = df_up_display.drop(columns=["Event URL", "Event ID"], errors="ignore")
                render_responsive_table(df_up_display, allow_html_columns=["Event Title", "Feedback"])
            else:
                st.info("No upcoming events found.")

        with tab2:
            df_past_filtered = filter_raw(df_past)
            if not df_past_filtered.empty:
                df_past_display = df_past_filtered.copy()
                df_past_display["Date and Time"] = pd.to_datetime(df_past_display["Date and Time"])
                df_past_display = df_past_display.sort_values(
                    "Date and Time", ascending=False
                ).reset_index(drop=True)
                date_fmt = "%a, %b %d, %Y" if compact_view else "%a, %b %d, %Y %I:%M %p"
                df_past_display["Date and Time"] = df_past_display["Date and Time"].dt.strftime(date_fmt)
                df_past_display["Event Title"] = df_past_display.apply(
                    lambda row: format_event_link(row["Event Title"], row["Event URL"]),
                    axis=1,
                )
                def _event_feedback_cell(row):
                    event_id = str(row.get("Event ID", "")).strip()
                    link = format_feedback_link(event_id, row.get("Event Title", ""))
                    if event_id in feedback_by_event:
                        avg_rating = feedback_by_event[event_id]
                        count = feedback_counts.get(event_id, 0)
                        count_label = f" ({count})" if count > 1 else ""
                        return f"{link} ⭐ {avg_rating:.1f}{count_label}"
                    return link

                df_past_display["Feedback"] = df_past_display.apply(_event_feedback_cell, axis=1)
                if compact_view:
                    df_past_display = df_past_display[
                        ["Event Title", "Date and Time", "No. of Attendees", "Feedback"]
                    ]
                else:
                    df_past_display = df_past_display.drop(
                        columns=["Event URL", "Event ID"], errors="ignore"
                    )
                render_responsive_table(df_past_display, allow_html_columns=["Event Title", "Feedback"])
            else:
                st.info("No past events found.")

        render_feedback_submission(feedback_df, df_up, df_past)

    def render_feedback_page(feedback_df=None):
        st.markdown('<div id="feedback"></div>', unsafe_allow_html=True)
        st.subheader("Community Feedback")
        if feedback_df is None or feedback_df.empty:
            st.info("No feedback has been submitted yet.")
            return

        fb = feedback_df.copy()
        fb["rating"] = pd.to_numeric(fb["rating"], errors="coerce")
        fb["submitted_at"] = pd.to_datetime(fb["submitted_at"], errors="coerce")
        fb = fb.sort_values("submitted_at", ascending=False)

        average_rating = fb["rating"].mean()
        total_feedback = len(fb)
        unique_events = fb["event_id"].nunique()

        st.markdown(
            f"<div style='margin-bottom:10px;'>"
            f"<strong>{total_feedback}</strong> feedback entries received across "
            f"<strong>{unique_events}</strong> events — average score <strong>{average_rating:.1f}</strong>."
            f"</div>",
            unsafe_allow_html=True,
        )

        display_df = fb["event_id event_title rating comment submitted_at".split()].copy()
        display_df["submitted_at"] = display_df["submitted_at"].dt.strftime("%Y-%m-%d %H:%M UTC").fillna("")
        render_responsive_table(display_df)


    # --- Insights / Story ---
    if PAGE_VIEW in ("all", "insights"):
        st.markdown('<div id="insights"></div>', unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class="pulse-wrap">
              <div class="pulse-title">Community Pulse Score</div>
              <div class="pulse-main">
                <div class="pulse-score">{pulse['score']}/100</div>
                <div class="pulse-label">{pulse['label']}</div>
              </div>
              <div class="pulse-meta">
                Community {pulse['community']} | Activity {pulse['activity']} | Attendance {pulse['attendance']} | Momentum {pulse['momentum']}
              </div>
              <div class="pulse-spark">Attendance trend: {pulse['sparkline']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- Meetup Events ---
    if PAGE_VIEW == "events":
        render_meetup_events_section(feedback_df=feedback_df)

    if PAGE_VIEW == "feedback":
        render_feedback_page(feedback_df=feedback_df)
        render_feedback_submission(feedback_df, df_up, df_past)

    if PAGE_VIEW == "calendar":
        calendar_events = pd.concat([df_up, df_past], ignore_index=True)
        render_community_calendar_section(
            feedback_df, df_up, df_past, narrow_viewport=is_narrow
        )
        render_booking_section(calendar_events)

    if PAGE_VIEW == "admin":
        if not is_moderator_authenticated():
            st.warning(
                "Moderator access is required to view this page. "
                "Use the sidebar to sign in with the configured moderator password."
            )
        else:
            bookings_df = load_event_bookings(EVENT_BOOKINGS_PATH)
            render_admin_booking_page(bookings_df)

    if PAGE_VIEW == "all":
        with st.expander("How Community Pulse Score works"):
            st.markdown("""
    The **Community Pulse Score** is a 0-100 health signal for this meetup community.

    **Formula**
    - Community size (25%): member count normalized to a 5,000-member benchmark.
    - Activity (25%): upcoming events normalized to a 6-event benchmark.
    - Attendance quality (30%): average past attendance normalized to an 80-attendee benchmark.
    - Momentum (20%): trend signal from recent 3 events vs previous 3 events.

    **Why it matters**
    - It gives stakeholders a fast snapshot of community health.
    - It combines both leading indicators (upcoming activity) and lagging indicators (attendance history).
    - It helps prioritize action quickly: schedule more events, improve promotion, or optimize topics/speakers.

    **Important note**
    - This score supports decisions, but it should be read with the detailed KPIs and charts below.
    """)
            
        st.subheader("Community Insights")

        if df_past.empty and df_up.empty:
            st.info("No event data available yet. Check back later!")
        else:
            total_past = len(df_past)
            total_upcoming = len(df_up)
            attendance_series = df_past.get("No. of Attendees")
            avg_attendance = safe_metric(attendance_series, agg="mean")
            max_attendance = safe_metric(attendance_series, agg="max")
            min_attendance = safe_metric(attendance_series, agg="min")

            story = f"""
            The **Data Engineering Pilipinas (DEP)** community now has over **{member_count:,} members**.  

            We have hosted **{total_past} past events** so far.  
            On average, each session gathered around **{avg_attendance} attendees**, 
            with participation ranging between **{min_attendance} and {max_attendance}**.

            Currently, there is/are **{total_upcoming} upcoming event/events** planned.  
            The community continues to grow, showing consistent engagement from members and speakers.
            """
            st.markdown(story)

            if not df_up.empty:
                next_event = df_up.copy()
                next_event["Date and Time"] = pd.to_datetime(
                    next_event["Date and Time"], errors="coerce"
                )
                next_event = (
                    next_event.dropna(subset=["Date and Time"])
                    .sort_values("Date and Time")
                    .head(1)
                )
                next_event = next_event.iloc[0] if not next_event.empty else df_up.iloc[0]
                next_dt = pd.to_datetime(next_event.get("Date and Time"), errors="coerce")
                next_dt_str = (
                    next_dt.strftime("%a, %b %d, %Y %I:%M %p") if pd.notna(next_dt) else "TBA"
                )
                speaker_text = ""
                speaker_name = str(next_event.get("Speakers", "")).strip()
                if speaker_name:
                    speaker_text = f" | Speaker: **{speaker_name}**"
                online_flag = next_event.get("Online?")
                if online_flag is True:
                    mode_text = "Online"
                elif online_flag is False:
                    mode_text = "In-person"
                else:
                    mode_text = "TBA"
                st.success(
                    f"Next event: **[{next_event['Event Title']}]({next_event['Event URL']})** "
                    f"on **{next_dt_str}** | **{mode_text}**{speaker_text}"
                )


    # --- Metrics ---
    if PAGE_VIEW in ("all", "kpi"):
        st.markdown('<div id="kpi"></div>', unsafe_allow_html=True)
        st.subheader("KPI Overview")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Members", member_count)
        c2.metric("Upcoming Events", len(df_up) if not df_up.empty else 0)
        c3.metric("Past Events", len(df_past) if not df_past.empty else 0)
        c4.metric("Avg Past Attendance", safe_metric(df_past.get("No. of Attendees"), agg="mean"))

    # --- Charts ---
    if PAGE_VIEW in ("all", "analytics"):
        st.markdown('<div id="analytics"></div>', unsafe_allow_html=True)
        st.subheader("Event Analytics")

        if not df_past.empty:
            df_past["Date and Time"] = pd.to_datetime(df_past["Date and Time"])
            df_past_sorted = df_past.sort_values("Date and Time").reset_index(drop=True)
            df_past_sorted.index += 1

            fig_attendance = px.line(
                df_past_sorted,
                x="Date and Time",
                y="No. of Attendees",
                title="Attendance Trend Over Time",
                markers=True,
                hover_data={"Event Title": True, "Date and Time": True, "No. of Attendees": True},
            )
            fig_attendance.update_layout(
                template="plotly_white",
                margin=dict(l=8, r=8, t=55, b=8),
                title_x=0.01,
                title_font_size=20,
                plot_bgcolor="#ffffff",
                paper_bgcolor="#ffffff",
            )
            fig_attendance.update_traces(line=dict(color="#1d4ed8", width=3), marker=dict(size=7))
            st.plotly_chart(fig_attendance, use_container_width=True)

            monthly = df_past_sorted.copy()
            monthly["Year"] = monthly["Date and Time"].dt.year.astype(str)
            monthly["Month"] = monthly["Date and Time"].dt.strftime("%b")
            month_order = [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ]
            monthly["Month"] = pd.Categorical(monthly["Month"], categories=month_order, ordered=True)
            monthly_rollup = (
                monthly.groupby(["Year", "Month"], observed=True)["No. of Attendees"]
                .mean()
                .reset_index(name="Avg Attendance")
            )
            monthly_rollup = monthly_rollup.sort_values(["Year", "Month"])

            heatmap_data = monthly_rollup.pivot(index="Year", columns="Month", values="Avg Attendance")
            heatmap_data = heatmap_data.reindex(columns=month_order)
            heatmap_data.index = pd.to_numeric(heatmap_data.index, errors="coerce")
            heatmap_data = heatmap_data.sort_index(ascending=False)
            heatmap_data.index = heatmap_data.index.astype("Int64").astype(str)

            fig_heatmap = px.imshow(
                heatmap_data,
                color_continuous_scale="Blues",
                aspect="auto",
                title="Monthly Attendance Heatmap",
                labels={"x": "Month", "y": "Year", "color": "Avg Attendance"},
            )
            fig_heatmap.update_layout(
                template="plotly_white",
                margin=dict(l=8, r=8, t=55, b=8),
                title_x=0.01,
                title_font_size=20,
                plot_bgcolor="#ffffff",
                paper_bgcolor="#ffffff",
            )
            fig_heatmap.update_traces(
                hovertemplate="Year: %{y}<br>Month: %{x}<br>Avg Attendance: %{z:.1f}<extra></extra>"
            )
            st.plotly_chart(fig_heatmap, use_container_width=True)
        else:
            st.info("No past events available yet for trend and heatmap analytics.")

    if PAGE_VIEW in ("all", "speakers"):
        st.markdown('<div id="speakers"></div>', unsafe_allow_html=True)
        st.subheader("Speaker Leaderboard")
        speaker_board = build_speaker_leaderboard(df_past)
        if speaker_board.empty:
            st.info("No speaker data available yet.")
        else:
            leader_cols = st.columns(3)
            top_rows = speaker_board.head(3).to_dict(orient="records")
            for idx, row in enumerate(top_rows):
                with leader_cols[idx]:
                    st.markdown(
                        f"""
                        <div class="leader-card">
                            <div class="leader-rank">Rank #{idx + 1}</div>
                            <div class="leader-name">{sanitize_title(row["Speaker"])}</div>
                            <div class="leader-count">{int(row["Sessions"])} sessions | avg attendance {int(row["Avg Attendance"])}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            if PAGE_VIEW == "speakers":
                render_responsive_table(speaker_board)

    if PAGE_VIEW == "all":
        render_meetup_events_section()

    st.markdown(
        '<div class="footer-text">Copyright © 2026 Katherine Bulac for Data Engineering Pilipinas Community.</div>',
        unsafe_allow_html=True,
    )

if __name__ == "__main__":
    main()
