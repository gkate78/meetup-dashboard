"""Speaker booking persistence and conflict detection."""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

SQLITE_BOOKING_EXTENSIONS = (".db", ".sqlite", ".sqlite3")
TABLE_NAME = "event_bookings"

BOOKING_COLUMNS = [
    "requested_datetime",
    "duration_minutes",
    "speaker_name",
    "email",
    "talk_title",
    "talk_summary",
    "preferred_format",
    "availability_notes",
    "status",
    "submitted_at",
]

DEFAULT_BOOKING_DURATION = 60
DEFAULT_EVENT_DURATION_MINUTES = int(os.getenv("DEP_EVENT_DURATION_MINUTES", "120"))
DEP_EVENT_TZ = os.getenv("DEP_EVENT_TZ", "Asia/Manila")


def _display_timestamp(value: Any, duration_minutes: int | None = None) -> str:
    ts = _coerce_timestamp(value)
    if pd.isna(ts):
        return "unknown time"
    local = ts.tz_convert(DEP_EVENT_TZ)
    if duration_minutes is None:
        return local.strftime("%Y-%m-%d %H:%M")
    end = local + pd.Timedelta(minutes=int(duration_minutes))
    return f"{local.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"


def _format_utc_storage(value: Any) -> str:
    ts = _coerce_timestamp(value)
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_sqlite_path(path: str) -> bool:
    return os.path.splitext(str(path).lower())[1] in SQLITE_BOOKING_EXTENSIONS


def _ensure_sqlite_schema(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                requested_datetime TEXT,
                duration_minutes INTEGER,
                speaker_name TEXT,
                email TEXT,
                talk_title TEXT,
                talk_summary TEXT,
                preferred_format TEXT,
                availability_notes TEXT,
                status TEXT,
                submitted_at TEXT
            )
            """
        )
        conn.commit()


def _normalize_booking_frame(bookings: pd.DataFrame) -> pd.DataFrame:
    if bookings is None or bookings.empty:
        return pd.DataFrame(columns=BOOKING_COLUMNS)
    out = bookings.copy()
    for col in BOOKING_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out = out[BOOKING_COLUMNS]
    for col in ("requested_datetime", "submitted_at"):
        out[col] = out[col].apply(_format_utc_storage)
    out["duration_minutes"] = pd.to_numeric(out["duration_minutes"], errors="coerce").fillna(
        DEFAULT_BOOKING_DURATION
    )
    out["status"] = out["status"].fillna("Requested").astype(str).str.strip()
    return out


def active_bookings(bookings: pd.DataFrame) -> pd.DataFrame:
    if bookings is None or bookings.empty:
        return bookings
    if "status" not in bookings.columns:
        return bookings.copy()
    return bookings[
        ~bookings["status"].astype(str).str.strip().str.casefold().eq("cancelled")
    ].copy()


def _coerce_timestamp(value: Any, reference: pd.Timestamp | None = None) -> pd.Timestamp:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return ts
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC")
    if reference is not None and reference.tzinfo is not None:
        return ts.tz_localize(reference.tz).tz_convert("UTC")
    return ts.tz_localize(DEP_EVENT_TZ).tz_convert("UTC")


def _interval_end(start: pd.Timestamp, duration_minutes: int) -> pd.Timestamp:
    return start + pd.Timedelta(minutes=int(duration_minutes))


def _intervals_overlap(start_a: pd.Timestamp, end_a: pd.Timestamp, start_b: pd.Timestamp, end_b: pd.Timestamp) -> bool:
    return start_a < end_b and start_b < end_a


def _parse_booking_row(fields: list[str]) -> dict[str, Any]:
    if len(fields) < 5:
        return {}

    duration = DEFAULT_BOOKING_DURATION
    submitted_at = fields[-1]
    status = fields[-2]
    if len(fields) > 9 and str(fields[-3]).strip().isdigit():
        duration = int(fields[-3])
        status = fields[-4]
        tail_start = 4
    else:
        tail_start = 2

    core_end = len(fields) - tail_start
    core = fields[:core_end]
    if len(core) < 5:
        return {}

    return {
        "requested_datetime": core[0],
        "speaker_name": core[1] if len(core) > 1 else "",
        "email": core[2] if len(core) > 2 else "",
        "talk_title": core[3] if len(core) > 3 else "",
        "talk_summary": core[4] if len(core) > 4 else "",
        "preferred_format": core[5] if len(core) > 5 else "",
        "availability_notes": core[6] if len(core) > 6 else "",
        "duration_minutes": duration,
        "status": status,
        "submitted_at": submitted_at,
    }


def _load_bookings_from_sqlite(path: str) -> pd.DataFrame:
    try:
        if not os.path.exists(path):
            return pd.DataFrame(columns=BOOKING_COLUMNS)
        _ensure_sqlite_schema(path)
        with sqlite3.connect(path) as conn:
            df = pd.read_sql_query(f"SELECT * FROM {TABLE_NAME}", conn)
        return df if df is not None else pd.DataFrame(columns=BOOKING_COLUMNS)
    except Exception as exc:
        logger.warning("Unable to load event bookings from SQLite %s: %s", path, exc)
        return pd.DataFrame(columns=BOOKING_COLUMNS)


def _load_bookings_from_csv(path: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        first_row = next(reader, None)
        if first_row is None:
            return pd.DataFrame(columns=BOOKING_COLUMNS)

        normalized_first = [str(value).strip() for value in first_row]
        header_lower = [value.casefold() for value in normalized_first]
        if not any(col.casefold() in header_lower for col in BOOKING_COLUMNS):
            parsed = _parse_booking_row(normalized_first)
            if parsed:
                rows.append(parsed)

        for fields in reader:
            if not fields or all(str(f).strip() == "" for f in fields):
                continue
            parsed = _parse_booking_row([str(f) for f in fields])
            if parsed:
                rows.append(parsed)

    return pd.DataFrame(rows, columns=BOOKING_COLUMNS) if rows else pd.DataFrame(columns=BOOKING_COLUMNS)


def load_event_bookings(path: str) -> pd.DataFrame:
    if _is_sqlite_path(path):
        return _normalize_booking_frame(_load_bookings_from_sqlite(path))

    if not os.path.exists(path):
        return pd.DataFrame(columns=BOOKING_COLUMNS)

    raw = None
    try:
        raw = pd.read_csv(path)
    except Exception:
        raw = None

    if raw is None or raw.empty:
        out = _load_bookings_from_csv(path)
        return _normalize_booking_frame(out)

    if "duration_minutes" in raw.columns and "requested_datetime" in raw.columns:
        out = raw.copy()
        for col in BOOKING_COLUMNS:
            if col not in out.columns:
                out[col] = ""
        out = out[BOOKING_COLUMNS]
    else:
        out = _load_bookings_from_csv(path)

    return _normalize_booking_frame(out)


def save_event_bookings(path: str, bookings: pd.DataFrame) -> None:
    normalized = _normalize_booking_frame(bookings)
    if _is_sqlite_path(path):
        _ensure_sqlite_schema(path)
        with sqlite3.connect(path) as conn:
            normalized.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
        return

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    normalized.to_csv(path, index=False)


def save_event_booking(path: str, record: dict[str, Any]) -> None:
    bookings = load_event_bookings(path)
    row = {col: record.get(col, "") for col in BOOKING_COLUMNS}
    if not row.get("duration_minutes"):
        row["duration_minutes"] = DEFAULT_BOOKING_DURATION
    if not row.get("status"):
        row["status"] = "Requested"
    row["requested_datetime"] = _format_utc_storage(row.get("requested_datetime"))
    row["submitted_at"] = _format_utc_storage(
        row.get("submitted_at") or datetime.now(timezone.utc)
    )
    bookings = pd.concat([bookings, pd.DataFrame([row])], ignore_index=True)
    save_event_bookings(path, bookings)


def booking_conflict_mask(
    bookings: pd.DataFrame, start: pd.Timestamp, duration_minutes: int
) -> pd.Series:
    bookings = active_bookings(bookings)
    if bookings is None or bookings.empty:
        return pd.Series(dtype=bool)

    start_utc = _coerce_timestamp(start)
    end_utc = _interval_end(start_utc, duration_minutes)
    mask = []
    for _, row in bookings.iterrows():
        ref = _coerce_timestamp(row.get("requested_datetime"))
        if pd.isna(ref):
            mask.append(False)
            continue
        booking_start = _coerce_timestamp(row.get("requested_datetime"), reference=ref)
        booking_end = _interval_end(
            booking_start, int(row.get("duration_minutes") or DEFAULT_BOOKING_DURATION)
        )
        mask.append(_intervals_overlap(start_utc, end_utc, booking_start, booking_end))
    return pd.Series(mask, index=bookings.index)


def first_booking_conflict(
    bookings: pd.DataFrame, start: pd.Timestamp, duration_minutes: int
) -> pd.Series | None:
    mask = booking_conflict_mask(bookings, start, duration_minutes)
    if mask.empty or not mask.any():
        return None
    return bookings.loc[mask[mask].index[0]]


def format_booking_conflict_message(row: pd.Series) -> str:
    speaker = str(row.get("speaker_name", "")).strip() or "another speaker"
    title = str(row.get("talk_title", "")).strip() or "a talk"
    window = _display_timestamp(
        row.get("requested_datetime"),
        int(row.get("duration_minutes") or DEFAULT_BOOKING_DURATION),
    )
    return f"That slot is already booked: {speaker} | {window} | {title}"


def slot_conflict_mask(
    existing: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    existing_duration_minutes: int = DEFAULT_EVENT_DURATION_MINUTES,
) -> pd.Series:
    start_utc = _coerce_timestamp(start)
    end_utc = _coerce_timestamp(end)
    mask = []
    for value in existing:
        ref = _coerce_timestamp(value)
        if pd.isna(ref):
            mask.append(False)
            continue
        event_start = _coerce_timestamp(value, reference=ref)
        event_end = _interval_end(event_start, existing_duration_minutes)
        mask.append(_intervals_overlap(start_utc, end_utc, event_start, event_end))
    return pd.Series(mask, index=existing.index)


def first_event_conflict_row(
    events: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    existing_duration_minutes: int = DEFAULT_EVENT_DURATION_MINUTES,
) -> pd.Series | None:
    if events is None or events.empty or "Date and Time" not in events.columns:
        return None
    mask = slot_conflict_mask(
        events["Date and Time"], start, end, existing_duration_minutes=existing_duration_minutes
    )
    if mask.empty or not mask.any():
        return None
    return events.loc[mask[mask].index[0]]


def format_event_conflict_message(row: pd.Series) -> str:
    title = str(row.get("Event Title", "")).strip() or "a scheduled event"
    label = _display_timestamp(row.get("Date and Time"))
    return f"That slot overlaps with {title} at {label}. Please choose a different time."


def update_event_booking_status(
    bookings: pd.DataFrame, index: int, status: str
) -> tuple[pd.DataFrame, bool]:
    if bookings is None or bookings.empty or index not in bookings.index:
        return bookings, False
    updated = bookings.copy()
    current = str(updated.loc[index, "status"]).strip()
    if current == status:
        return updated, False
    updated.loc[index, "status"] = status
    return updated, True
