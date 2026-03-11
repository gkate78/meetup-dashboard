import pandas as pd


def safe_metric(series, agg="count"):
    if series is None or series.dropna().empty:
        return 0
    if agg == "count":
        return int(series.dropna().count())
    if agg == "mean":
        return int(series.dropna().mean())
    if agg == "max":
        return int(series.dropna().max())
    if agg == "min":
        return int(series.dropna().min())
    return 0


def clamp(value, lower=0, upper=100):
    return max(lower, min(upper, value))


def build_sparkline(values):
    bars = "▁▂▃▄▅▆▇█"
    clean = [v for v in values if pd.notna(v)]
    if len(clean) < 2:
        return "n/a"
    low = min(clean)
    high = max(clean)
    if high == low:
        return bars[3] * len(clean)
    out = []
    for v in clean:
        idx = int(((v - low) / (high - low)) * (len(bars) - 1))
        out.append(bars[idx])
    return "".join(out)


def build_speaker_leaderboard(df):
    if df is None or df.empty or "Speakers" not in df.columns:
        return pd.DataFrame(columns=["Speaker", "Sessions", "Avg Attendance", "Last Session"])

    base = df.copy()
    base["No. of Attendees"] = pd.to_numeric(base.get("No. of Attendees"), errors="coerce")
    base["Date and Time"] = pd.to_datetime(base.get("Date and Time"), errors="coerce")
    invalid_speakers = {"", "-", "nan", "none", "null", "na", "n/a"}
    base["Speaker"] = base["Speakers"].fillna("").astype(str).str.split(",")
    expanded = base.explode("Speaker")
    expanded["Speaker"] = expanded["Speaker"].str.strip()
    expanded = expanded[~expanded["Speaker"].str.casefold().isin(invalid_speakers)]
    if expanded.empty:
        return pd.DataFrame(columns=["Speaker", "Sessions", "Avg Attendance", "Last Session"])

    grouped = expanded.groupby("Speaker", as_index=False).agg(
        Sessions=("Event Title", "count"),
        Avg_Attendance=("No. of Attendees", "mean"),
        Last_Session=("Date and Time", "max"),
    )

    grouped["Avg_Attendance"] = grouped["Avg_Attendance"].fillna(0)
    grouped = grouped.sort_values(
        by=["Sessions", "Avg_Attendance", "Last_Session", "Speaker"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    grouped["Avg Attendance"] = grouped["Avg_Attendance"].round().astype(int)
    grouped["Last Session"] = grouped["Last_Session"].dt.strftime("%b %d, %Y").fillna("-")
    return grouped[["Speaker", "Sessions", "Avg Attendance", "Last Session"]]


def compute_pulse(member_count, df_up, df_past):
    upcoming_count = len(df_up) if df_up is not None else 0
    base_df = df_past if df_past is not None else pd.DataFrame()
    attendance = pd.to_numeric(base_df.get("No. of Attendees"), errors="coerce")
    avg_attendance = safe_metric(attendance, agg="mean")

    community_score = clamp((member_count / 5000) * 100)
    activity_score = clamp((upcoming_count / 6) * 100)
    attendance_score = clamp((avg_attendance / 80) * 100)

    momentum_score = 50
    sparkline = "n/a"
    if df_past is not None and not df_past.empty and "Date and Time" in df_past.columns:
        momentum_df = df_past[["Date and Time", "No. of Attendees"]].copy()
        momentum_df["Date and Time"] = pd.to_datetime(momentum_df["Date and Time"], errors="coerce")
        momentum_df["No. of Attendees"] = pd.to_numeric(
            momentum_df["No. of Attendees"], errors="coerce"
        )
        momentum_df = momentum_df.dropna().sort_values("Date and Time")
        sparkline = build_sparkline(momentum_df.tail(10)["No. of Attendees"].tolist())
        if len(momentum_df) >= 6:
            recent = momentum_df.tail(3)["No. of Attendees"].mean()
            prior = momentum_df.tail(6).head(3)["No. of Attendees"].mean()
            delta = recent - prior
            momentum_score = clamp(50 + (delta * 1.5))

    pulse_score = round(
        (0.25 * community_score)
        + (0.25 * activity_score)
        + (0.30 * attendance_score)
        + (0.20 * momentum_score)
    )

    if pulse_score >= 80:
        pulse_label = "High Momentum"
    elif pulse_score >= 60:
        pulse_label = "Healthy Growth"
    elif pulse_score >= 40:
        pulse_label = "Steady"
    else:
        pulse_label = "Needs Activation"

    return {
        "score": int(pulse_score),
        "label": pulse_label,
        "community": int(round(community_score)),
        "activity": int(round(activity_score)),
        "attendance": int(round(attendance_score)),
        "momentum": int(round(momentum_score)),
        "sparkline": sparkline,
    }
