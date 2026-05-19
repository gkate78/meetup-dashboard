import base64
import html
import json
import logging
import os
import random
import re
import time
from urllib.parse import quote_plus, urlparse

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit.errors import StreamlitSecretNotFoundError

from meetup_metrics import build_speaker_leaderboard, compute_pulse, safe_metric


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


# -------------------
# Config
# -------------------

API_URL = "https://api.meetup.com/gql-ext"
URLNAME = "data-engineering-pilipinas"
SPEAKER_OVERRIDES_PATH = os.getenv("SPEAKER_OVERRIDES_PATH", "data/speaker_overrides.csv")
SNAPSHOT_PATH = os.getenv("SNAPSHOT_PATH", "cache/meetup_snapshot.json")
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
FEEDBACK_FORM_URL = os.getenv("FEEDBACK_FORM_URL", "https://forms.gle/your-feedback-form").strip()
FEEDBACK_DATA_PATH = os.getenv("FEEDBACK_DATA_PATH", "data/feedback.csv").strip()
if not MEETUP_TOKEN:
    try:
        MEETUP_TOKEN = st.secrets["MEETUP_TOKEN"].strip()
    except (StreamlitSecretNotFoundError, KeyError):
        MEETUP_TOKEN = ""

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


def load_feedback_data(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=["event_id", "rating", "comment"])
    try:
        fb = pd.read_csv(path)
        fb = fb.rename(columns={"event_id": "event_id", "rating": "rating", "comment": "comment"})
        if "event_id" not in fb.columns:
            return pd.DataFrame(columns=["event_id", "rating", "comment"])
        return fb
    except Exception as e:
        logger.warning("Unable to load feedback data: %s", e)
        return pd.DataFrame(columns=["event_id", "rating", "comment"])


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


def load_speaker_overrides(path):
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

    cal = calendar.Calendar(firstweekday=6)
    month_days = list(cal.monthdatescalendar(selected_year, selected_month))
    params = st.query_params if hasattr(st, "query_params") else st.experimental_get_query_params()
    raw_date = params.get("cal_date")
    if isinstance(raw_date, list):
        raw_date = raw_date[0] if raw_date else None
    selected_date = None
    if raw_date:
        try:
            selected_date = pd.to_datetime(raw_date).date()
        except (TypeError, ValueError):
            selected_date = None
    vw_param = st.session_state.get("vw_param")
    options = sorted(events_by_day.keys())
    if selected_date is None and options:
        selected_date = options[0]

    day_table = [
        "<div class='calendar-grid-wrapper compact'>",
        "<div class='calendar-month-grid compact'>",
        "<div class='calendar-row calendar-header compact'>"
        "<div>Sun</div><div>Mon</div><div>Tue</div><div>Wed</div><div>Thu</div><div>Fri</div><div>Sat</div>"
        "</div>",
    ]

    for week in month_days:
        row_html = "<div class='calendar-row compact'>"
        for d in week:
            is_current = d.month == selected_month
            cell_class = "calendar-day compact current-month" if is_current else "calendar-day compact other-month"
            events = events_by_day.get(d, []) if is_current else []
            has_events = bool(events)
            highlight = " has-event" if has_events else ""
            feedback_note = ""
            if has_events:
                event_feedbacks = []
                for event in events:
                    event_id = str(event.get("Event ID", "")).strip()
                    if event_id in feedback_by_event:
                        event_feedbacks.append(feedback_by_event[event_id])
                if event_feedbacks:
                    feedback_note = f" <span class='calendar-day-feedback'>⭐ {sum(event_feedbacks)/len(event_feedbacks):.1f}</span>"

            if is_current:
                query_parts = []
                if vw_param:
                    query_parts.append(f"vw={vw_param}")
                query_parts.append(f"cal_date={d.isoformat()}")
                href = "?" + "&".join(query_parts) + "#calendar-details"
                selected_class = " selected" if selected_date == d else ""
                day_button = (
                    f"<a class='calendar-day-btn{highlight}{selected_class}' href='{href}'>"
                    f"{d.day}{feedback_note}</a>"
                )
            else:
                day_button = f"<div class='calendar-day-btn disabled'>{d.day}</div>"

            row_html += f"<div class='{cell_class}'>{day_button}</div>"
        row_html += "</div>"
        day_table.append(row_html)

    day_table.append("</div></div>")
    st.markdown(
        """
        <style>
        .calendar-grid-wrapper.compact { border:1px solid #dfe3e9; border-radius:12px; padding: 10px; background:#fff; box-shadow: 0 6px 16px rgba(15,23,42,0.05); }
        .calendar-month-grid.compact { display: grid; gap: 6px; margin-bottom: 10px; }
        .calendar-row.compact { display: grid; grid-template-columns: repeat(7, minmax(0,1fr)); gap: 6px; }
        .calendar-header.compact div { background:#1d4ed8; color:#fff; font-weight:700; padding:6px 4px; border-radius:6px; text-align:center; font-size:0.72rem; letter-spacing:0.2px; white-space: nowrap; }
        .calendar-day.compact { border:1px solid #d7e2f1; border-radius:10px; padding:6px; background:#fff; font-size:0.82rem; display:flex; align-items:center; justify-content:center; min-height:48px; }
        .calendar-day.compact.other-month { background:#f8fafc; color:#94a3b8; }
        .calendar-day.compact.current-month { background:#ffffff; }
        .calendar-day-btn { width:100%; border:none; background:transparent; font-weight:700; font-size:0.9rem; color:#0f172a; display:flex; align-items:center; justify-content:center; gap:6px; cursor:pointer; padding:6px 0; border-radius:8px; text-decoration:none; }
        .calendar-day-btn.has-event { background:#e0ecff; color:#1d4ed8; box-shadow: inset 0 0 0 2px #1d4ed8; }
        .calendar-day-btn.selected { background:#1d4ed8; color:#ffffff; box-shadow: inset 0 0 0 2px #1d4ed8; }
        .calendar-day-btn:hover { background:#eef4ff; }
        .calendar-day-btn.disabled { cursor:default; opacity:0.5; }
        .calendar-day-feedback { font-size:0.7rem; color:#065f46; background:#ecfdf3; padding:1px 4px; border-radius:4px; }
        .calendar-details { margin-top: 12px; padding: 12px 14px; border:1px solid #d7e2f1; border-radius:12px; background:#ffffff; box-shadow: 0 6px 14px rgba(15,23,42,0.06); }
        @media (max-width: 900px) {
            .calendar-day.compact { min-height:44px; }
            .calendar-day-btn { font-size:0.85rem; }
        }
        </style>
    """,
        unsafe_allow_html=True,
    )
    st.markdown("".join(day_table), unsafe_allow_html=True)

    if options:
        picked = selected_date if selected_date in options else options[0]
        st.markdown('<div id="calendar-details"></div>', unsafe_allow_html=True)
        st.markdown("### Event Details")
        events_for_day = events_by_day.get(picked, [])
        if not events_for_day:
            st.info("No events scheduled for this date.")
        st.markdown(f"<div class='calendar-details'><strong>{picked.strftime('%B %d, %Y')}</strong></div>", unsafe_allow_html=True)
        for event in events_for_day:
            title = sanitize_title(event.get("Event Title", "Untitled"))
            url = event.get("Event URL", "#")
            typ = event.get("Type", "Past")
            online = event.get("Online?", False)
            dt = pd.to_datetime(event.get("Date and Time"), errors="coerce")
            time_label = dt.strftime("%I:%M %p") if not pd.isna(dt) else "TBD"
            date_label = dt.strftime("%b %d, %Y") if not pd.isna(dt) else picked.strftime("%b %d, %Y")
            speaker = sanitize_title(event.get("Speakers", ""))
            mode = "Online" if online else "In-person"
            event_id = str(event.get("Event ID", "")).strip()
            feedback_badge = ""
            if event_id in feedback_by_event:
                feedback_badge = f" ⭐ {feedback_by_event[event_id]:.1f}"
            st.markdown(
                f"- [{title}]({html.escape(url)}) [{typ}] — {date_label} {time_label} — {mode}"
                f"{f' • {speaker}' if speaker else ''}{feedback_badge}"
            )


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
        "saved_at": pd.Timestamp.utcnow().isoformat(),
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


# -------------------
# Main App
# -------------------
file_path = "assets/dep_logo.png"
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

    if "DEP_PAGE" not in st.session_state:
        st.session_state["DEP_PAGE"] = "all"
        os.environ["DEP_PAGE"] = "all"

    PAGE_VIEW = (
        st.session_state.get("DEP_PAGE")
        if "DEP_PAGE" in st.session_state
        else os.getenv("DEP_PAGE", "all")
    ).strip().lower()

    dashboard = get_dashboard_data(URLNAME)
    df_up = dashboard["df_up"]
    df_past = dashboard["df_past"]
    member_count = dashboard["member_count"]
    pulse_source = dashboard["source"]
    pulse_saved_at = dashboard["saved_at"]
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

    def render_meetup_events_section():
        st.markdown('<div id="events"></div>', unsafe_allow_html=True)
        st.subheader("Meetup Events")
        raw_controls = st.columns(3)
        raw_year = raw_controls[0]
        raw_month = raw_controls[1]
        raw_reset = raw_controls[2]

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
                df_up_display["Feedback"] = df_up_display.apply(
                    lambda row: format_feedback_link(row.get("Event ID", ""), row.get("Event Title", "")),
                    axis=1,
                )
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
                df_past_display["Feedback"] = df_past_display.apply(
                    lambda row: format_feedback_link(row.get("Event ID", ""), row.get("Event Title", "")),
                    axis=1,
                )
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
        render_meetup_events_section()

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

    if PAGE_VIEW == "all":
        render_meetup_events_section()

if __name__ == "__main__":
    main()
