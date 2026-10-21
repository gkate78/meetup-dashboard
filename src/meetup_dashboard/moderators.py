"""Moderator authorization helpers that do not depend on Streamlit."""

from __future__ import annotations

import hmac
import re
from collections.abc import Iterable, Mapping
from typing import Any

PASSWORD_MAX_ATTEMPTS = 5
PASSWORD_LOCKOUT_SECONDS = 300


def parse_moderator_emails(raw: str | Iterable[str] | None) -> frozenset[str]:
    """Parse a comma/whitespace separated allowlist into normalized emails."""
    if raw is None:
        return frozenset()
    values = re.split(r"[\s,;]+", raw) if isinstance(raw, str) else [str(v) for v in raw]
    return frozenset(v.strip().casefold() for v in values if v and v.strip())


def moderator_email(user_info: Mapping[str, Any], allowed: frozenset[str]) -> str | None:
    """Return the signed-in user's email when it is on the allowlist.

    A provider that explicitly reports ``email_verified`` as false is rejected so
    an unverified address cannot claim a moderator's identity.
    """
    if not allowed or not user_info.get("is_logged_in"):
        return None
    if user_info.get("email_verified") is False:
        return None
    email = str(user_info.get("email") or "").strip().casefold()
    return email if email and email in allowed else None


def passwords_match(candidate: str, expected: str) -> bool:
    """Compare passwords in constant time; an unset password never matches."""
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def password_lockout_remaining(failures: int, last_failure_at: float | None, now: float) -> float:
    """Seconds left before another password attempt is allowed (0 when allowed)."""
    if failures < PASSWORD_MAX_ATTEMPTS or last_failure_at is None:
        return 0.0
    return max(0.0, PASSWORD_LOCKOUT_SECONDS - (now - last_failure_at))
