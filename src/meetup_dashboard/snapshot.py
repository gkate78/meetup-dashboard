#!/usr/bin/env python3
"""Fetch dashboard data and persist snapshot using the app's logic.

This script is intended to be run from CI (GitHub Actions) or a cron job.
It imports the shared `meetup` module and triggers the dashboard data fetch,
which will save a snapshot via the configured `SNAPSHOT_BACKEND`.
"""

import logging

from .app import URLNAME, get_dashboard_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fetch_snapshot")


def main():
    logger.info("Starting snapshot fetch for %s", URLNAME)
    try:
        dashboard = get_dashboard_data(URLNAME)
        logger.info(
            "Snapshot fetch complete. source=%s saved_at=%s",
            dashboard.get("source"),
            dashboard.get("saved_at"),
        )
    except Exception as e:
        logger.exception("Snapshot fetch failed: %s", e)


if __name__ == "__main__":
    main()
