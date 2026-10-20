# meetup-dashboard

This is my personal project, so it overrides the team-repo workflow in my global CLAUDE.md.

## Workflow

- Still never push to `main`. Use one branch and one PR per change so CI runs before merging.
- No issue is needed before starting work; a request in chat is enough. File issues only for bugs or ideas to come back to later.
- Skip the overlap checks for open PRs and stale branches. Still pull fresh `main` before branching.
- Keep PR descriptions short: a summary, the changes, and what was verified.
- Screenshots or video are only needed for substantial UI changes.
- Do not keep a contribution log for this project.

## Checks (match CI)

Run `ruff check .`, `black --check .`, `mypy` and `pytest -q`. CI formats with **black**, not `ruff format`, which reformats unrelated lines.

## Development notes

After finishing a feature or a bug fix that took real effort, add or update an entry in
`docs/development-notes.md` (problem, what I did, takeaway). Skip trivial tweaks.
