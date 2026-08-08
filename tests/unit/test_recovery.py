import sqlite3
from datetime import UTC, datetime

from meetup_dashboard.recovery import create_backup, restore_backup, upload_backup_to_s3


def test_runtime_backup_and_restore_preserves_sqlite_and_csv_data(tmp_path):
    feedback_path = tmp_path / "feedback.db"
    bookings_path = tmp_path / "bookings.db"
    aliases_path = tmp_path / "aliases.db"
    snapshot_path = tmp_path / "snapshot.json"
    runtime_files = {
        "feedback": feedback_path,
        "event_bookings": bookings_path,
        "speaker_overrides": aliases_path,
        "snapshot": snapshot_path,
    }

    with sqlite3.connect(feedback_path) as conn:
        conn.execute("CREATE TABLE feedback (comment TEXT)")
        conn.execute("INSERT INTO feedback VALUES ('original feedback')")
    with sqlite3.connect(bookings_path) as conn:
        conn.execute("CREATE TABLE event_bookings (title TEXT)")
        conn.execute("INSERT INTO event_bookings VALUES ('original booking')")
    snapshot_path.write_text('{"source": "original"}', encoding="utf-8")

    archive = create_backup(runtime_files, tmp_path / "backups", retain=2)

    with sqlite3.connect(feedback_path) as conn:
        conn.execute("UPDATE feedback SET comment = 'changed'")
    snapshot_path.write_text('{"source": "changed"}', encoding="utf-8")

    restored = restore_backup(archive, runtime_files)

    assert set(restored) == {feedback_path, bookings_path, snapshot_path}
    with sqlite3.connect(feedback_path) as conn:
        assert conn.execute("SELECT comment FROM feedback").fetchone()[0] == "original feedback"
    assert snapshot_path.read_text(encoding="utf-8") == '{"source": "original"}'


def test_backup_retention_keeps_the_requested_number_of_archives(tmp_path, monkeypatch):
    runtime_files = {"snapshot": tmp_path / "snapshot.json"}
    runtime_files["snapshot"].write_text("one", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    first = create_backup(runtime_files, backup_dir, retain=1)

    class FixedDateTime:
        @staticmethod
        def now(_tz):
            return datetime(2099, 1, 2, tzinfo=UTC)

    monkeypatch.setattr(
        "meetup_dashboard.recovery.datetime",
        FixedDateTime,
    )
    second = create_backup(runtime_files, backup_dir, retain=1)

    assert second.exists()
    assert not first.exists()


def test_backup_upload_uses_encrypted_s3_storage(tmp_path):
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"backup")

    class FakeS3:
        def __init__(self):
            self.call = None

        def upload_file(self, *args, **kwargs):
            self.call = args, kwargs

    client = FakeS3()
    key = upload_backup_to_s3(archive, "backup-bucket", "daily", client)

    assert key == "daily/backup.tar.gz"
    assert client.call == (
        (str(archive), "backup-bucket", "daily/backup.tar.gz"),
        {"ExtraArgs": {"ServerSideEncryption": "AES256"}},
    )
