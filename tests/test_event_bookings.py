import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from meetup import (
    booking_conflict_mask,
    first_event_conflict_row,
    first_booking_conflict,
    format_booking_conflict_message,
    format_event_conflict_message,
    load_event_bookings,
    load_feedback_data,
    load_speaker_overrides,
    save_event_booking,
    save_feedback_data,
    save_snapshot,
    load_snapshot,
    update_event_booking_status,
    slot_conflict_mask,
)


def test_event_booking_roundtrip(tmp_path):
    path = tmp_path / "event_bookings.csv"
    record = {
        "requested_datetime": "2026-05-21T12:00:00Z",
        "duration_minutes": 60,
        "speaker_name": "Ana Cruz",
        "email": "ana@example.com",
        "talk_title": "Building Reliable Pipelines",
        "talk_summary": "A practical talk on data pipeline reliability.",
        "preferred_format": "Hybrid",
        "availability_notes": "Evenings, UTC+8",
        "status": "Requested",
        "submitted_at": "2026-05-21T00:00:00Z",
    }

    save_event_booking(str(path), record)
    bookings = load_event_bookings(str(path))

    assert len(bookings) == 1
    assert str(bookings.iloc[0]["requested_datetime"])[:10] == "2026-05-21"
    assert int(bookings.iloc[0]["duration_minutes"]) == 60
    assert bookings.iloc[0]["speaker_name"] == "Ana Cruz"
    assert bookings.iloc[0]["talk_title"] == "Building Reliable Pipelines"


def test_event_booking_roundtrip_sqlite(tmp_path):
    path = tmp_path / "event_bookings.db"
    record = {
        "requested_datetime": "2026-05-21T12:00:00Z",
        "duration_minutes": 60,
        "speaker_name": "Ana Cruz",
        "email": "ana@example.com",
        "talk_title": "Building Reliable Pipelines",
        "talk_summary": "A practical talk on data pipeline reliability.",
        "preferred_format": "Hybrid",
        "availability_notes": "Evenings, UTC+8",
        "status": "Requested",
        "submitted_at": "2026-05-21T00:00:00Z",
    }

    save_event_booking(str(path), record)
    bookings = load_event_bookings(str(path))

    assert len(bookings) == 1
    assert str(bookings.iloc[0]["requested_datetime"])[:10] == "2026-05-21"
    assert int(bookings.iloc[0]["duration_minutes"]) == 60
    assert bookings.iloc[0]["speaker_name"] == "Ana Cruz"
    assert bookings.iloc[0]["talk_title"] == "Building Reliable Pipelines"


def test_feedback_storage_roundtrip_sqlite(tmp_path):
    path = tmp_path / "feedback.db"
    record = {
        "event_id": "evt-1",
        "event_title": "DEP Meetup",
        "rating": 4.5,
        "comment": "Great session",
        "submitted_at": "2026-05-21T00:00:00Z",
    }

    save_feedback_data(str(path), record)
    feedback = load_feedback_data(str(path))

    assert len(feedback) == 1
    assert feedback.iloc[0]["event_id"] == "evt-1"
    assert float(feedback.iloc[0]["rating"]) == 4.5
    assert feedback.iloc[0]["event_title"] == "DEP Meetup"
    assert feedback.iloc[0]["comment"] == "Great session"
    assert feedback.iloc[0]["submitted_at"] == "2026-05-21T00:00:00Z"


def test_snapshot_storage_roundtrip_sqlite(tmp_path):
    path = tmp_path / "meetup_snapshot.db"
    import pandas as pd
    import meetup

    meetup.SNAPSHOT_PATH = str(path)

    df_up = pd.DataFrame(
        [
            {"id": "evt-1", "title": "Future Meetup", "date": "2026-06-01"}
        ]
    )
    df_past = pd.DataFrame(
        [
            {"id": "evt-0", "title": "Past Meetup", "date": "2026-04-01"}
        ]
    )

    save_snapshot(df_up, df_past, member_count=1200)
    snapshot = load_snapshot()

    assert snapshot is not None
    assert snapshot["member_count"] == 1200
    assert snapshot["saved_at"] is not None
    assert len(snapshot["df_up"]) == 1
    assert len(snapshot["df_past"]) == 1
    assert snapshot["df_up"].iloc[0]["title"] == "Future Meetup"
    assert snapshot["df_past"].iloc[0]["title"] == "Past Meetup"


def test_load_speaker_overrides_sqlite(tmp_path):
    path = tmp_path / "speaker_overrides.db"
    import sqlite3
    import pandas as pd

    records = [
        {"event_id": "evt-1", "canonical_speakers": "Jane Doe"},
        {"event_id": "evt-2", "canonical_speakers": "John Smith"},
    ]
    df = pd.DataFrame(records)
    with sqlite3.connect(str(path)) as conn:
        df.to_sql("speaker_overrides", conn, if_exists="replace", index=False)

    overrides = load_speaker_overrides(str(path))
    assert overrides["evt-1"] == "Jane Doe"
    assert overrides["evt-2"] == "John Smith"


def test_load_event_bookings_recovers_mixed_schema(tmp_path):
    path = tmp_path / "event_bookings.csv"
    path.write_text(
        "\n".join(
            [
                "requested_datetime,speaker_name,email,talk_title,talk_summary,preferred_format,availability_notes,status,submitted_at",
                "2026-05-23 19:00:00,KATHERINE G BULAC,GKATE78@YAHOO.COM,agentic ai,hjhgjhg,Online,khjgjhg,Confirmed,2026-05-21 08:59:29.864210+00:00",
                "2026-05-24T18:00:00,KATHERINE G BULAC,gkate1178@gmail.com,Agentic,gasdf,Online,asdfas,Requested,60,2026-05-21T18:31:02.990353",
                "2026-05-25T18:00:00,KATHERINE G BULAC,GKATE78@YAHOO.COM,Agentic,gasdfas,Online,asdfsaddf,Requested,60,2026-05-21T18:36:44.914762",
            ]
        ),
        encoding="utf-8",
    )

    bookings = load_event_bookings(str(path))

    assert len(bookings) == 3
    assert int(bookings.iloc[0]["duration_minutes"]) == 60
    assert int(bookings.iloc[1]["duration_minutes"]) == 60
    assert int(bookings.iloc[2]["duration_minutes"]) == 60


def test_booking_conflict_mask_flags_overlapping_window():
    import pandas as pd

    bookings = pd.DataFrame(
        [
            {
                "requested_datetime": "2026-05-21T12:00:00Z",
                "duration_minutes": 60,
                "speaker_name": "Ana Cruz",
                "email": "ana@example.com",
            },
            {
                "requested_datetime": "2026-05-21T14:00:00Z",
                "duration_minutes": 30,
                "speaker_name": "Ben Cruz",
                "email": "ben@example.com",
            },
        ]
    )

    mask = booking_conflict_mask(
        bookings,
        pd.Timestamp("2026-05-21T12:30:00Z"),
        30,
    )

    assert mask.tolist() == [True, False]


def test_booking_conflict_mask_blocks_same_booked_slot():
    import pandas as pd

    bookings = pd.DataFrame(
        [
            {
                "requested_datetime": "2026-05-21T18:00:00+08:00",
                "duration_minutes": 60,
                "speaker_name": "Ana Cruz",
                "email": "ana@example.com",
            }
        ]
    )

    mask = booking_conflict_mask(
        bookings,
        pd.Timestamp("2026-05-21T18:00:00"),
        60,
    )

    assert mask.tolist() == [True]


def test_first_booking_conflict_returns_matching_row():
    import pandas as pd

    bookings = pd.DataFrame(
        [
            {
                "requested_datetime": "2026-05-21T18:00:00+08:00",
                "duration_minutes": 60,
                "speaker_name": "Ana Cruz",
                "talk_title": "Reliable Pipelines",
                "email": "ana@example.com",
            },
            {
                "requested_datetime": "2026-05-21T20:00:00+08:00",
                "duration_minutes": 60,
                "speaker_name": "Ben Cruz",
                "talk_title": "Data Quality",
                "email": "ben@example.com",
            },
        ]
    )

    conflict = first_booking_conflict(
        bookings,
        pd.Timestamp("2026-05-21T18:30:00+08:00"),
        60,
    )

    assert conflict is not None
    assert conflict["speaker_name"] == "Ana Cruz"


def test_format_booking_conflict_message_includes_details():
    import pandas as pd

    row = pd.Series(
        {
            "requested_datetime": pd.Timestamp("2026-05-21T18:00:00+08:00"),
            "duration_minutes": 60,
            "speaker_name": "Ana Cruz",
            "talk_title": "Reliable Pipelines",
        }
    )

    message = format_booking_conflict_message(row)

    assert "That slot is already booked" in message
    assert "Ana Cruz" in message
    assert "Reliable Pipelines" in message
    assert "2026-05-21 18:00" in message
    assert "2026-05-21 19:00" in message


def test_slot_conflict_mask_flags_existing_event_time():
    import pandas as pd

    existing = pd.Series(
        [pd.Timestamp("2026-05-21T12:00:00Z"), pd.Timestamp("2026-05-21T14:00:00Z")]
    )

    mask = slot_conflict_mask(
        existing, pd.Timestamp("2026-05-21T12:00:00Z"), pd.Timestamp("2026-05-21T12:30:00Z")
    )

    assert mask.tolist() == [True, False]


def test_slot_conflict_mask_blocks_late_overlap_with_event_window():
    import pandas as pd

    existing = pd.Series([pd.Timestamp("2026-05-21T12:00:00Z")])

    mask = slot_conflict_mask(
        existing,
        pd.Timestamp("2026-05-21T13:30:00Z"),
        pd.Timestamp("2026-05-21T14:30:00Z"),
        existing_duration_minutes=120,
    )

    assert mask.tolist() == [True]


def test_first_event_conflict_row_returns_matching_event():
    import pandas as pd

    events = pd.DataFrame(
        [
            {"Date and Time": pd.Timestamp("2026-05-21T12:00:00Z"), "Event Title": "DEP Meetup"},
            {"Date and Time": pd.Timestamp("2026-05-21T15:00:00Z"), "Event Title": "AI Study"},
        ]
    )

    event = first_event_conflict_row(
        events,
        pd.Timestamp("2026-05-21T13:30:00Z"),
        pd.Timestamp("2026-05-21T14:30:00Z"),
        existing_duration_minutes=120,
    )

    assert event is not None
    assert event["Event Title"] == "DEP Meetup"


def test_format_event_conflict_message_includes_title_and_time():
    import pandas as pd

    row = pd.Series(
        {
            "Date and Time": pd.Timestamp("2026-05-21T12:00:00+08:00"),
            "Event Title": "DEP Meetup",
        }
    )

    message = format_event_conflict_message(row)

    assert "That slot overlaps with" in message
    assert "DEP Meetup" in message
    assert "2026-05-21 12:00" in message
    assert "Please choose a different time" in message


def test_slot_conflict_mask_handles_timezone_mixed_inputs():
    import pandas as pd

    existing = pd.Series([pd.Timestamp("2026-05-21T12:00:00+08:00")])

    mask = slot_conflict_mask(
        existing,
        pd.Timestamp("2026-05-21T12:00:00"),
        pd.Timestamp("2026-05-21T13:00:00"),
    )

    assert mask.tolist() == [True]


def test_update_event_booking_status_changes_status():
    import pandas as pd

    bookings = pd.DataFrame(
        [
            {
                "requested_datetime": "2026-05-21T12:00:00Z",
                "duration_minutes": 60,
                "speaker_name": "Ana Cruz",
                "email": "ana@example.com",
                "talk_title": "Building Reliable Pipelines",
                "status": "Requested",
            }
        ]
    )

    updated, changed = update_event_booking_status(bookings, 0, "Cancelled")

    assert changed is True
    assert updated.loc[0, "status"] == "Cancelled"
