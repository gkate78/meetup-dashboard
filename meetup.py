import pandas as pd
import streamlit as st
import plotly.express as px
import base64
import requests
import re
import time
import os
import json
import html
import random
import logging
from urllib.parse import urlparse
from streamlit.errors import StreamlitSecretNotFoundError
from meetup_metrics import safe_metric, build_speaker_leaderboard, compute_pulse

def sanitize_title(title):
    # Remove emojis
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002700-\U000027BF"  # Dingbats
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    clean = emoji_pattern.sub(r'', str(title))
    clean = clean.replace('\t', ' ').replace('\n', ' ').strip()
    clean = clean.replace('[', '\\[').replace(']', '\\]').replace('|', '\\|').replace('`', '\\`')
    return clean

# -------------------
# Config
# -------------------
st.set_page_config(page_title="DEP Meetup Dashboard", layout="wide")

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

logger = logging.getLogger("dep_meetup")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MEETUP_TOKEN = os.getenv("MEETUP_TOKEN", "").strip()
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
    return dict(zip(clean["event_id"], clean["canonical_speakers"]))


def apply_missing_speaker_overrides(df, overrides):
    if df is None or df.empty or not overrides:
        return df, 0
    if "Event ID" not in df.columns or "Speakers" not in df.columns:
        return df, 0

    out = df.copy()
    out["Event ID"] = out["Event ID"].astype(str).str.strip()
    missing_mask = out["Speakers"].fillna("").astype(str).str.strip().str.casefold().isin(
        {"", "-", "nan", "none", "null", "na", "n/a"}
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


def format_event_link(title, url):
    safe_title = html.escape(sanitize_title(title))
    raw_url = str(url).strip()
    parsed = urlparse(raw_url)
    safe_url = raw_url if parsed.scheme in {"http", "https"} else "#"
    return f'<a href="{html.escape(safe_url)}" target="_blank" rel="noopener noreferrer">{safe_title}</a>'


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
                st.warning(f"Meetup GraphQL returned errors: {payload['errors'][0].get('message', 'Unknown error')}")
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
                backoff = RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.35)
                logger.warning("API request failed with HTTP %s. retry_in=%.2fs", status_code, backoff)
                time.sleep(backoff)
            else:
                raise
        except requests.exceptions.RequestException as e:
            st.warning(f"Attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                backoff = RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.35)
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

    df_up = pd.DataFrame([{
            "Event ID": e["node"]["id"],
            "Event Title": e["node"]["title"],
            "Date and Time": e["node"]["dateTime"],
            "Event URL": e["node"]["eventUrl"],
            "Online?": e["node"].get("eventType") in ("ONLINE", "HYBRID"),
            "Speakers": format_speakers(e["node"].get("speakerDetails"))
        } for e in up_edges]) if up_edges else pd.DataFrame(columns=[
            "Event ID", "Event Title", "Date and Time", "Event URL", "Online?", "Speakers"
        ])

    # --- Past ---
    past_edges = gql_paginated(QUERY_PAST, urlname, page_size=100)

    df_past = pd.DataFrame([{
            "Event ID": e["node"]["id"],
            "Event Title": e["node"]["title"],
            "Date and Time": e["node"]["dateTime"],
            "Event URL": e["node"]["eventUrl"],
            "Online?": e["node"].get("eventType") in ("ONLINE", "HYBRID"),
            "No. of Attendees": (e["node"].get("rsvps") or {}).get("yesCount"),
            "Speakers": format_speakers(e["node"].get("speakerDetails"))
        } for e in past_edges]) if past_edges else pd.DataFrame(columns=[
            "Event ID", "Event Title", "Date and Time", "Event URL", "Online?", "No. of Attendees", "Speakers"
        ])

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
            s3.put_object(Bucket=SNAPSHOT_S3_BUCKET, Key=SNAPSHOT_S3_KEY, Body=encoded, ContentType="application/json")
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
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)

    df_up = pd.DataFrame(payload.get("upcoming", []))
    df_past = pd.DataFrame(payload.get("past", []))
    return {
        "df_up": df_up,
        "df_past": df_past,
        "member_count": int(payload.get("member_count", 0)),
        "saved_at": payload.get("saved_at"),
    }


def get_dashboard_data(urlname):
    overrides = load_speaker_overrides(SPEAKER_OVERRIDES_PATH)
    try:
        df_up, df_past = load_data(urlname)
        df_up = normalize_speaker_column(df_up)
        df_past = normalize_speaker_column(df_past)
        df_past, override_count = apply_missing_speaker_overrides(df_past, overrides)
        member_data = gql_request(QUERY_MEMBERS, {"urlname": urlname})
        member_count = group_node(member_data).get("stats", {}).get("memberCounts", {}).get("all", 0)
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
            "df_up": pd.DataFrame(columns=["Event ID", "Event Title", "Date and Time", "Event URL", "Online?", "Speakers"]),
            "df_past": pd.DataFrame(columns=["Event ID", "Event Title", "Date and Time", "Event URL", "Online?", "No. of Attendees", "Speakers"]),
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

st.markdown(
    f"""
    <style>
        .stApp {{
            background: radial-gradient(circle at 15% 0%, #eaf2ff 0%, #f8fbff 45%, #f3f7fc 100%);
        }}
        .header-container {{
            text-align: center;
            padding: 24px 12px 8px 12px;
        }}
        .sticky-header {{
            position: fixed;
            top: 2.6rem;
            left: 0;
            right: 0;
            z-index: 1000;
            background: radial-gradient(circle at 15% 0%, #eaf2ff 0%, #f8fbff 45%, #f3f7fc 100%);
            backdrop-filter: blur(2px);
            border-bottom: 1px solid #d7e2f1;
            overflow: visible;
        }}
        .header-spacer {{
            height: 220px;
        }}
        .header-logo {{
            width: 130px;
            height: auto;
            max-height: none;
            display: block;
            margin-left: auto;
            margin-right: auto;
            margin-bottom: 8px;
        }}
        .header-title {{
            font-size: 2.15rem;
            font-weight: bold;
            color: #0f172a;
            letter-spacing: 0.2px;
        }}
        .header-subtitle {{
            color: #334155;
            margin-top: 4px;
            font-size: 1rem;
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
        @media (max-width: 768px) {{
            .header-spacer {{
                height: 190px;
            }}
            .header-title {{
                font-size: 1.6rem;
            }}
            .header-subtitle {{
                font-size: 0.9rem;
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

    <div class="sticky-header">
        <div class="header-container">
            {logo_html}
            <div class="header-title">Data Engineering Pilipinas Meetup Dashboard</div>
            <div class="header-subtitle">Live community pulse: events, attendance, and growth</div>
        </div>
    </div>
    <div class="header-spacer"></div>
    """,
    unsafe_allow_html=True
)

# --- Load Data ---
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
compact_view = st.toggle("Compact mobile view", value=False, help="Use shorter tables and tighter text for small screens.")

with st.expander("How Community Pulse Score works"):
    st.markdown(
        """
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
"""
    )

# --- Metrics ---
st.subheader("KPI Overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Members", member_count)
c2.metric("Upcoming Events", len(df_up) if not df_up.empty else 0)
c3.metric("Past Events", len(df_past) if not df_past.empty else 0)
c4.metric("Avg Past Attendance", safe_metric(df_past.get("No. of Attendees"), agg="mean"))

# --- Insights / Story ---
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
        next_event = df_up.iloc[0]
        st.success(
            f"Next event: **[{next_event['Event Title']}]({next_event['Event URL']})** "
            f"on **{next_event['Date and Time']}**"
        )

# --- Charts ---
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
        hover_data={"Event Title": True, "Date and Time": True, "No. of Attendees": True}
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
    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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

# --- Raw Data ---
st.subheader("Raw Event Data")
tab1, tab2 = st.tabs(["Upcoming Events", "Past Events"])

with tab1:
    if not df_up.empty:
        df_up_display = df_up.copy()
        date_fmt = "%b %d, %Y" if compact_view else "%b %d, %Y %I:%M %p"
        df_up_display["Date and Time"] = pd.to_datetime(df_up_display["Date and Time"]).dt.strftime(date_fmt)
        df_up_display["Event Title"] = df_up_display.apply(
            lambda row: format_event_link(row["Event Title"], row["Event URL"]),
            axis=1,
        )
        if compact_view:
            df_up_display = df_up_display[["Event Title", "Date and Time"]]
        else:
            df_up_display = df_up_display.drop(columns=["Event URL", "Event ID"], errors="ignore")
        render_responsive_table(df_up_display, allow_html_columns=["Event Title"])
    else:
        st.info("No upcoming events found.")

with tab2:
    if not df_past.empty:
        df_past_display = df_past.copy()
        df_past_display["Date and Time"] = pd.to_datetime(df_past_display["Date and Time"])
        df_past_display = df_past_display.sort_values("Date and Time", ascending=False).reset_index(drop=True)
        date_fmt = "%b %d, %Y" if compact_view else "%b %d, %Y %I:%M %p"
        df_past_display["Date and Time"] = df_past_display["Date and Time"].dt.strftime(date_fmt)
        df_past_display["Event Title"] = df_past_display.apply(
            lambda row: format_event_link(row["Event Title"], row["Event URL"]),
            axis=1,
        )
        if compact_view:
            df_past_display = df_past_display[["Event Title", "Date and Time", "No. of Attendees"]]
        else:
            df_past_display = df_past_display.drop(columns=["Event URL", "Event ID"], errors="ignore")
        render_responsive_table(df_past_display, allow_html_columns=["Event Title"])
    else:
        st.info("No past events found.")
