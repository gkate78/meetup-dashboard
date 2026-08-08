"""Create and restore verified backups of the dashboard's runtime data."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

RUNTIME_FILES = {
    "feedback": "FEEDBACK_DATA_PATH",
    "speaker_overrides": "SPEAKER_OVERRIDES_PATH",
    "event_bookings": "EVENT_BOOKINGS_PATH",
    "snapshot": "SNAPSHOT_PATH",
}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
ARCHIVE_PREFIX = "meetup-dashboard-"
MANIFEST_NAME = "manifest.json"


def configured_runtime_files(root_dir: Path) -> dict[str, Path]:
    """Return runtime paths using the same defaults as the dashboard."""
    defaults = {
        "feedback": "data/feedback.db",
        "speaker_overrides": "data/speaker_overrides.db",
        "event_bookings": "data/event_bookings.db",
        "snapshot": "cache/meetup_snapshot.db",
    }
    files = {}
    for name, env_name in RUNTIME_FILES.items():
        raw_path = Path(os.getenv(env_name, defaults[name])).expanduser()
        files[name] = raw_path if raw_path.is_absolute() else root_dir / raw_path
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_consistent(source: Path, destination: Path) -> None:
    """Copy a file, using SQLite's backup API to avoid a torn database copy."""
    if source.suffix.lower() not in SQLITE_EXTENSIONS:
        shutil.copy2(source, destination)
        return

    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)


def create_backup(runtime_files: dict[str, Path], backup_dir: Path, retain: int = 14) -> Path:
    """Create a checksummed archive of all existing runtime files.

    Missing optional files are omitted. A successful archive is written
    atomically and old archives are pruned only after that succeeds.
    """
    if retain < 1:
        raise ValueError("retain must be at least 1")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = backup_dir / f"{ARCHIVE_PREFIX}{timestamp}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="meetup-backup-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        files = []
        for name, source in runtime_files.items():
            if not source.exists():
                continue
            copied = temp_dir / f"{name}{source.suffix.lower()}"
            _copy_consistent(source, copied)
            files.append({"name": copied.name, "sha256": _sha256(copied)})

        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
        }
        (temp_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary_archive = backup_dir / f".{archive.name}.tmp"
        with tarfile.open(temporary_archive, "w:gz") as tar:
            tar.add(temp_dir / MANIFEST_NAME, arcname=MANIFEST_NAME)
            for item in files:
                tar.add(temp_dir / item["name"], arcname=item["name"])
        os.replace(temporary_archive, archive)

    archives = sorted(backup_dir.glob(f"{ARCHIVE_PREFIX}*.tar.gz"), reverse=True)
    for old_archive in archives[retain:]:
        old_archive.unlink()
    return archive


def upload_backup_to_s3(
    archive: Path, bucket: str, prefix: str = "meetup-dashboard/backups", s3_client=None
) -> str:
    """Upload a verified archive to S3 with server-side encryption enabled."""
    if not archive.is_file():
        raise FileNotFoundError(archive)
    bucket = bucket.strip()
    if not bucket:
        raise ValueError("an S3 bucket is required")
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")
    key = f"{prefix.strip('/')}/{archive.name}" if prefix.strip("/") else archive.name
    s3_client.upload_file(
        str(archive),
        bucket,
        key,
        ExtraArgs={"ServerSideEncryption": "AES256"},
    )
    return key


def restore_backup(archive: Path, runtime_files: dict[str, Path]) -> list[Path]:
    """Verify and atomically restore an archive. Stop the app before calling it."""
    if not archive.is_file():
        raise FileNotFoundError(archive)

    with tempfile.TemporaryDirectory(prefix="meetup-restore-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            allowed_names = {MANIFEST_NAME} | {
                f"{name}{path.suffix.lower()}" for name, path in runtime_files.items()
            }
            if any(not member.isfile() or member.name not in allowed_names for member in members):
                raise ValueError("backup archive contains an unexpected file")
            # The strict member allow-list above makes extraction safe. The
            # filter argument is unavailable on Python 3.11.
            try:
                tar.extractall(temp_dir, members, filter="data")
            except TypeError:
                tar.extractall(temp_dir, members)

        manifest_path = temp_dir / MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError("backup archive has no manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("backup archive has an invalid manifest")

        restored = []
        for item in manifest["files"]:
            name = item.get("name")
            source = temp_dir / str(name)
            key = next((key for key in runtime_files if str(name).startswith(f"{key}.")), None)
            if key is None or not source.is_file() or _sha256(source) != item.get("sha256"):
                raise ValueError("backup archive failed integrity verification")
            destination = runtime_files[key]
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_destination = destination.with_name(f".{destination.name}.restore")
            shutil.copy2(source, temporary_destination)
            os.replace(temporary_destination, destination)
            restored.append(destination)
    return restored
