# Recovery runbook

Use this runbook when the dashboard is unhealthy, data is corrupted, or a deployment needs to be rolled back.

## Routine backup

Run daily from an environment where the database paths in `FEEDBACK_DATA_PATH`, `SPEAKER_OVERRIDES_PATH`, `EVENT_BOOKINGS_PATH` and `SNAPSHOT_PATH` point at the production files. In production these are single files bind-mounted under `/data/sqlite/meetup/` (see the README's Dokploy section). Only those `.db` files are persistent in the container, so write the archive to a directory outside the container, or copy it off before the next deploy:

```bash
python backup_runtime_data.py create --backup-dir /safe-backup-location
```

For production, configure `BACKUP_S3_BUCKET` and AWS credentials scoped to a backup prefix. The same command then uploads an encrypted off-site copy:

```bash
BACKUP_S3_BUCKET=your-backup-bucket \
BACKUP_S3_PREFIX=meetup-dashboard/production \
python backup_runtime_data.py create --backup-dir /safe-backup-location
```

Schedule this from the production host/platform—not GitHub Actions—because the job must access the real mounted data volume. A cron entry can run it daily at 02:15 local server time:

```cron
15 2 * * * cd /app && /usr/local/bin/python backup_runtime_data.py create --backup-dir /safe-backup-location >> /var/log/meetup-backup.log 2>&1
```

The backup contains feedback, speaker overrides/aliases, bookings, and the snapshot. SQLite databases are copied through SQLite's backup API, then archived with SHA-256 checksums. Keep the archive outside the application volume; configure encrypted off-site storage for production.

## Restore a known-good state

1. Stop the app to prevent writes during restore.
2. Deploy the last known-good Git revision or container image.
3. Restore the selected backup against the same mounted data paths:

   ```bash
   python backup_runtime_data.py restore --archive /safe-backup-location/meetup-dashboard-YYYYMMDDTHHMMSSZ.tar.gz
   ```

   **Production (single-file bind mounts):** `restore` writes a temporary file and renames it over each database. Docker does not allow renaming over a file that is itself a bind mount, so expect this to fail inside the production container with `Device or resource busy`. Until restore supports this layout, restore on the host instead: with the app stopped, extract the archive, compare `sha256sum *.db` with the checksums in `manifest.json`, then copy each `.db` file over the matching file in `/data/sqlite/meetup/` (for example `cp speaker_overrides.db /data/sqlite/meetup/speaker_overrides.db`). `cp` overwrites the file in place, so the mount stays intact.

4. Start the app and check `/_stcore/health`.
5. Confirm the dashboard loads, the snapshot fallback is available, a booking can be read, and the speaker leaderboard appears.
6. Record the incident, archive used, and data-loss window.

## Restore test

At least monthly, restore a recent backup into a non-production environment and perform the verification in step 5. A backup that has not been restored successfully is not yet a reliable recovery mechanism.
