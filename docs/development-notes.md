# Development notes

What I built, the problems I ran into, and what I learned. Written to double as talking points.

> Lines marked **TODO (Kata)** need details only I know, such as numbers, motivation or outcomes. Fill them in so the stories hold up when someone asks follow-up questions.

---

## The project in 30 seconds

A Streamlit dashboard for the **Data Engineering Pilipinas** Meetup community. It pulls events from the Meetup GraphQL API and gives organizers one place to see attendance trends, KPIs, a speaker leaderboard, community feedback, and a booking calendar where speakers can request a slot.

- **Stack:** Python 3.11, Streamlit, pandas, Plotly, SQLite, Docker, deployed on Dokploy. Optional S3 for snapshots and backups.
- **Quality:** GitHub Actions CI runs ruff, black, mypy and pytest on every PR.
- **Timeline:** started 2026-03-03 and still active.
- **Users:** **TODO (Kata):** who uses it and how often (organizers, speakers, members), plus community size.

---

## Talking points by theme

Use these when a question calls for a specific kind of story.

| If asked about… | Story to tell |
|---|---|
| Messy real-world data | [Speaker names and identity](#1-speaker-names-are-messy-real-world-data) |
| Reliability and resilience | [Snapshot fallback](#2-the-meetup-api-isnt-always-available) and [Backups and recovery](#6-backups-are-only-real-if-you-can-restore-them) |
| A bug you tracked down | [Admin status updates](#4-admin-status-changes-that-didnt-stick) or [Calendar booking dates](#5-the-calendar-that-hid-booking-dates-on-mobile) |
| Deployment and DevOps | [Container and port configuration](#3-getting-the-container-to-run-on-dokploy) |
| Refactoring and code quality | [From one script to a package](#7-from-one-big-script-to-a-tested-package) |
| Security and data handling | [Runtime data out of Git](#8-keeping-runtime-data-out-of-git) |
| A product or design trade-off | [Pinning the light theme](#9-choosing-consistency-over-dark-mode) |

---

## Milestones

| When | Milestone |
|---|---|
| Mar 2026 | First version: event analytics, KPIs, speaker leaderboard. Added lint, format and security guidelines. |
| Mar 2026 | Month-grid community calendar with speaker, time, online/in-person and feedback links, plus a responsive list mode. Dockerfile. |
| May 2026 | In-app feedback page. Booking calendar with validated speaker requests. SQLite persistence for bookings, feedback and speaker overrides. |
| May 2026 | Restructured into a `src/` package with Makefile, docker-compose and `.env.example`. Admin booking moderation page. |
| May–Jun 2026 | Deployed on Dokploy; fixed container port and config issues. |
| Jul 2026 | Pinned a light theme for consistent rendering across devices. |
| Aug 2026 | Canonical speaker alias resolution. Backup and restore tooling with a recovery runbook. |
| Oct 2026 | Small UX polish (for example, Yes/No instead of True/False in the speakers table). |

---

## Challenges and how I solved them

### 1. Speaker names are messy real-world data

**Problem:** The leaderboard counts sessions per speaker, but names come from free text in Meetup events. One person showed up in several forms (`Maria Santos`, `M. Santos`, with or without a credential such as `MSDS`). Co-speakers were joined with commas, `and` or `&`, and some events had placeholders like `nan`, `-` or `none`. The result was split counts, credentials counted as separate "people", and junk rows in the ranking.

**What I did:**
- Wrote a parser that splits co-speakers but keeps credentials (MSDS, PhD, MBA and others) attached to the name, and drops placeholder values.
- Normalized harmless differences such as case, accents, punctuation and honorifics before comparing names.
- Added **reviewed aliases** in SQLite: a `speakers` table holds a stable `speaker_id` and a canonical name, and a `speaker_aliases` table maps each approved variation to it.
- Kept the raw value alongside the cleaned one for auditing.

**Key decision:** **No fuzzy auto-merging.** Two different people can have similar names, and a wrong merge silently corrupts someone's record. Only variations a person has reviewed are merged, and everything else stays separate.

**What I tried first:** An earlier version (Aug 2026, archived as the tag `archive/speaker-normalization`) matched speakers automatically by **last name**, ignoring honorifics. It fixed the obvious cases, such as "Kristine Joy Cristobal" vs "Kristine Cristobal" and "Mr Sunga" vs "Macky Sunga". When I compared it side by side with the alias approach, though, it also merged **different people who share a surname**: "Maria Santos" and "Jose Santos" became one speaker with both people's sessions. I dropped it before merging and used reviewed aliases instead, which handle all the real variants without false merges.

**Takeaway:** For identity data, a small manual review step is worth more than a clever matching algorithm. Being correct matters more than being automatic. Test a matching rule against cases that *should not* match, not only the ones that should.

**TODO (Kata):** how many duplicates or variants did this fix? Did any speaker's rank change noticeably?

### 2. The Meetup API isn't always available

**Problem:** The dashboard depends on an external API that can return 503s or rate-limit with 429s.

**What I did:** Retries with exponential backoff and jitter that honor `Retry-After` on 429. Every successful load saves a snapshot. If the live API fails, the app shows the last good snapshot with a visible warning instead of an error page.

**Takeaway:** Make it clear when data is stale rather than failing outright. Users would rather see yesterday's numbers labeled as such than nothing.

### 3. Getting the container to run on Dokploy

**Problem:** The app ran locally but not when deployed. The Dockerfile hard-coded port 8501, so the platform's `PORT` was ignored. docker-compose also bind-mounted the source folder over the image and parameterized the port mapping in a way the platform didn't route to.

**What I did:** Started Streamlit through `sh -c` so `${PORT:-8501}` is expanded at runtime, set it to run headless, removed the source bind mount, and simplified the compose port mapping. I documented the persistent `/app/data` volume that runtime data needs.

**Takeaway:** "Works locally" and "works on the platform" are different tests. Exec-form `CMD` doesn't expand environment variables, and dev conveniences like bind mounts can break a production container.

**TODO (Kata):** what did the failure actually look like (container restarts, 502, health check)? How did you diagnose it?

### 4. Admin status changes that didn't stick

**Problem:** On the admin booking page, changing a booking's status sometimes didn't work. Statuses stored with different capitalization, or values outside the known list, didn't match the dropdown, which then fell back to "Requested". Sign-in and sign-out also cleared a Streamlit widget's state in a way that caused trouble.

**What I did:** Matched statuses case-insensitively, kept any unexpected stored value as a selectable option instead of overwriting it, moved the update logic into a tested function (`update_event_booking_status`), and stopped manually clearing widget-owned session state.

**Takeaway:** A UI can quietly rewrite data when stored values don't match what the UI expects. Test the edge cases, not only the happy path.

### 5. The calendar that hid booking dates on mobile

**Problem:** Two bugs. On narrow screens the calendar defaulted to list view, but bookable dates exist only in the grid, so mobile users couldn't book. Separately, the calendar's scoped CSS didn't apply because Streamlit renders each `st.markdown` call in its own container. My opening `<div>` and closing `</div>` ended up in different containers.

**What I did:** Kept the grid as the default everywhere and made it scroll horizontally on small screens. I build the wrapper, legend and table as one HTML string and render it in a single call.

**Takeaway:** Know how your framework actually renders things. Streamlit isn't a template engine, and HTML split across calls doesn't nest.

### 6. Backups are only real if you can restore them

**Problem:** Feedback, bookings and speaker aliases live in SQLite files on a mounted volume. Losing that volume would lose community data that can't be fetched again from Meetup.

**What I did:** Wrote `backup_runtime_data.py` with `create` and `restore` commands. It copies SQLite files using SQLite's backup API, which is safe while the app is running, archives them with SHA-256 checksums, and can optionally upload to S3. I wrote a recovery runbook covering daily backups, restore steps, health checks and a monthly restore drill.

**Takeaway:** Copying a live SQLite file can capture it mid-write, so use the backup API. A backup you've never restored is only a hope.

### 7. From one big script to a tested package

**Problem:** Most of the app lived in one `meetup.py` file of more than 2,000 lines. It was hard to test and hard to change safely.

**What I did:** Restructured into `src/meetup_dashboard/` (app, metrics, bookings, snapshot, recovery), moved tests into `tests/unit`, and added a Makefile, docker-compose and `.env.example`. CI enforces ruff, black, mypy and pytest.

**Takeaway:** Pure functions such as speaker parsing, metrics and booking validation are easy to unit-test. Pulling them out of the UI code is what made the later bug fixes safe.

### 8. Keeping runtime data out of Git

**Problem:** Early on, files like `speaker_overrides.csv` and snapshot JSON were committed. They contain community data and change at runtime.

**What I did:** Removed them from Git and the Docker build context, documented them as runtime data on a mounted volume, and kept secrets such as the Meetup token and admin password in environment variables.

**Takeaway:** Separate code, config and data early. It simplifies deployment, backups and privacy.

### 9. Choosing consistency over dark mode

**Problem:** Custom HTML, CSS and charts looked inconsistent or unreadable depending on the device's light or dark setting.

**What I did:** Pinned a light theme in `.streamlit/config.toml` and tuned the colors to match.

**Takeaway:** Supporting both themes properly costs ongoing effort for every custom component. For a small community tool, one theme that is readable everywhere was the better trade-off.

**TODO (Kata):** the branch was named `ui-theme-adaptive`. Did you try an adaptive theme first? If so, what broke? That makes the story stronger.

---

## Things I'd do differently

- **TODO (Kata):** for example, write commit messages that say *why*, not only what. Or split the package earlier, or start with SQLite instead of CSV.

---

## How to keep this updated

Add an entry when a feature is finished or a bug takes real effort to solve. Skip small tweaks. Write the problem, what you did and the takeaway while it's still fresh.
