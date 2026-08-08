#!/usr/bin/env python3
"""Create or restore a verified runtime-data backup."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))
recovery = importlib.import_module("meetup_dashboard.recovery")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "restore"))
    parser.add_argument("--backup-dir", type=Path, default=ROOT_DIR / "backups")
    parser.add_argument("--archive", type=Path, help="Archive to restore")
    parser.add_argument("--retain", type=int, default=14)
    parser.add_argument(
        "--s3-bucket",
        default=os.getenv("BACKUP_S3_BUCKET", ""),
        help="Optional durable S3 destination (or set BACKUP_S3_BUCKET)",
    )
    parser.add_argument(
        "--s3-prefix",
        default=os.getenv("BACKUP_S3_PREFIX", "meetup-dashboard/backups"),
        help="S3 key prefix for uploaded archives",
    )
    args = parser.parse_args()

    runtime_files = recovery.configured_runtime_files(ROOT_DIR)
    if args.action == "create":
        archive = recovery.create_backup(runtime_files, args.backup_dir, args.retain)
        print(f"Created verified backup: {archive}")
        if args.s3_bucket.strip():
            key = recovery.upload_backup_to_s3(archive, args.s3_bucket, args.s3_prefix)
            print(f"Uploaded durable backup: s3://{args.s3_bucket}/{key}")
        return
    if args.archive is None:
        parser.error("--archive is required when restoring")
    restored = recovery.restore_backup(args.archive, runtime_files)
    print("Restored: " + ", ".join(str(path) for path in restored))


if __name__ == "__main__":
    main()
