import pandas as pd

from meetup_dashboard.metrics import (
    build_sparkline,
    build_speaker_leaderboard,
    compute_pulse,
    safe_metric,
    split_speaker_names,
)


def test_safe_metric_mean_handles_nan():
    series = pd.Series([10, None, 20, 30])
    assert safe_metric(series, "mean") == 20


def test_build_sparkline_returns_na_with_insufficient_data():
    assert build_sparkline([None, 5]) == "n/a"


def test_build_speaker_leaderboard_sorts_by_sessions_then_attendance():
    df = pd.DataFrame(
        [
            {
                "Event Title": "A",
                "Date and Time": "2025-01-01",
                "No. of Attendees": 60,
                "Speakers": "Ana, Ben",
            },
            {
                "Event Title": "B",
                "Date and Time": "2025-02-01",
                "No. of Attendees": 40,
                "Speakers": "Ana",
            },
            {
                "Event Title": "C",
                "Date and Time": "2025-03-01",
                "No. of Attendees": 80,
                "Speakers": "Ben",
            },
        ]
    )
    board = build_speaker_leaderboard(df)
    assert board.iloc[0]["Speaker"] == "Ben"
    assert int(board.iloc[0]["Sessions"]) == 2


def test_build_speaker_leaderboard_excludes_missing_and_nan_speakers():
    df = pd.DataFrame(
        [
            {
                "Event Title": "A",
                "Date and Time": "2025-01-01",
                "No. of Attendees": 60,
                "Speakers": "Ana",
            },
            {
                "Event Title": "B",
                "Date and Time": "2025-02-01",
                "No. of Attendees": 40,
                "Speakers": "nan",
            },
            {
                "Event Title": "C",
                "Date and Time": "2025-03-01",
                "No. of Attendees": 80,
                "Speakers": None,
            },
            {
                "Event Title": "D",
                "Date and Time": "2025-04-01",
                "No. of Attendees": 50,
                "Speakers": "-",
            },
            {
                "Event Title": "E",
                "Date and Time": "2025-05-01",
                "No. of Attendees": 55,
                "Speakers": "",
            },
        ]
    )
    board = build_speaker_leaderboard(df)
    assert board["Speaker"].tolist() == ["Ana"]


def test_split_speaker_names_handles_joiners_and_credentials():
    assert split_speaker_names("Jessie Dimanlig and Jake Robert Mongaya") == [
        "Jessie Dimanlig",
        "Jake Robert Mongaya",
    ]
    assert split_speaker_names("Nina Comia & Ian James Maceres") == [
        "Nina Comia",
        "Ian James Maceres",
    ]
    assert split_speaker_names("Engr. Bob Mathew D. Sunga, MSDS") == [
        "Engr. Bob Mathew D. Sunga, MSDS"
    ]


def test_build_speaker_leaderboard_splits_joiners_without_counting_credentials():
    df = pd.DataFrame(
        [
            {
                "Event Title": "A",
                "Date and Time": "2025-01-01",
                "No. of Attendees": 60,
                "Speakers": "Jessie Dimanlig and Jake Robert Mongaya",
            },
            {
                "Event Title": "B",
                "Date and Time": "2025-02-01",
                "No. of Attendees": 40,
                "Speakers": "Engr. Bob Mathew D. Sunga, MSDS",
            },
            {
                "Event Title": "C",
                "Date and Time": "2025-03-01",
                "No. of Attendees": 80,
                "Speakers": "jessie dimanlig",
            },
        ]
    )

    board = build_speaker_leaderboard(df)

    assert "MSDS" not in board["Speaker"].tolist()
    assert "Jake Robert Mongaya" in board["Speaker"].tolist()
    assert board.loc[board["Speaker"] == "Jessie Dimanlig", "Sessions"].iloc[0] == 2


def test_compute_pulse_structure_and_bounds():
    df_up = pd.DataFrame([{"Event Title": "up-1"}, {"Event Title": "up-2"}])
    df_past = pd.DataFrame(
        [
            {"Date and Time": "2025-01-01", "No. of Attendees": 20},
            {"Date and Time": "2025-02-01", "No. of Attendees": 40},
            {"Date and Time": "2025-03-01", "No. of Attendees": 60},
            {"Date and Time": "2025-04-01", "No. of Attendees": 50},
            {"Date and Time": "2025-05-01", "No. of Attendees": 80},
            {"Date and Time": "2025-06-01", "No. of Attendees": 100},
        ]
    )
    pulse = compute_pulse(1000, df_up, df_past)
    assert set(pulse.keys()) == {
        "score",
        "label",
        "community",
        "activity",
        "attendance",
        "momentum",
        "sparkline",
    }
    assert 0 <= pulse["score"] <= 100
