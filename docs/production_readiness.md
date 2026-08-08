# Production-readiness plan

This roadmap prioritizes a fast, safe return to a known-good state after a failure.

## Phase 1 — recovery baseline (implemented)

- Use persistent volumes for application data and snapshots.
- Create checksummed runtime-data backups with `backup_runtime_data.py`.
- Keep 14 backups by default; test a restore before each production release.
- Restart the container automatically after a process failure.
- Use the Streamlit health endpoint for deployment health checks.

## Phase 2 — reliability and operations

1. Store scheduled snapshots in S3 or another shared durable store. The current GitHub Actions file-backend snapshot is ephemeral and does not update the deployed volume.
2. Send structured logs and errors to a central monitoring service; alert on stale snapshots, failed backups, failed writes, and failed Meetup refreshes.
3. Add a release checklist: database backup, deploy, health check, smoke test, and rollback decision.
4. Add a staging environment and automated browser tests for the public booking and moderator flows.

## Phase 3 — security and scale

1. Replace the shared admin password with individual organizer identities, roles, throttling, and an audit log.
2. Add anti-spam controls to public forms.
3. Escape all dynamic values before rendering HTML.
4. Migrate runtime data to managed Postgres before running multiple app replicas or assigning multiple simultaneous operators.

## Recovery objective

Target recovery time is 15 minutes for an application or data failure: deploy the last known-good revision, restore the latest verified runtime-data backup, then confirm `/_stcore/health` and the dashboard snapshot fallback.
