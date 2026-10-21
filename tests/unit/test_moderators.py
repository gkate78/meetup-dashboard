from meetup_dashboard.moderators import (
    PASSWORD_LOCKOUT_SECONDS,
    PASSWORD_MAX_ATTEMPTS,
    moderator_email,
    parse_moderator_emails,
    password_lockout_remaining,
    passwords_match,
)

ALLOWED = parse_moderator_emails("Ana@Example.com, ben@example.com;\ncara@example.com")


def test_parse_moderator_emails_normalizes_and_splits():
    assert ALLOWED == {"ana@example.com", "ben@example.com", "cara@example.com"}
    assert parse_moderator_emails("") == frozenset()
    assert parse_moderator_emails(None) == frozenset()
    assert parse_moderator_emails(["Ana@Example.com", " "]) == {"ana@example.com"}


def test_moderator_email_accepts_allowlisted_verified_user():
    user = {"is_logged_in": True, "email": "ANA@example.com", "email_verified": True}
    assert moderator_email(user, ALLOWED) == "ana@example.com"


def test_moderator_email_rejects_unlisted_unverified_or_signed_out():
    assert moderator_email({"is_logged_in": True, "email": "eve@example.com"}, ALLOWED) is None
    unverified = {"is_logged_in": True, "email": "ana@example.com", "email_verified": False}
    assert moderator_email(unverified, ALLOWED) is None
    assert moderator_email({"is_logged_in": False, "email": "ana@example.com"}, ALLOWED) is None
    assert moderator_email({"is_logged_in": True}, ALLOWED) is None


def test_moderator_email_rejects_everyone_when_allowlist_empty():
    user = {"is_logged_in": True, "email": "ana@example.com", "email_verified": True}
    assert moderator_email(user, frozenset()) is None


def test_passwords_match_is_exact_and_rejects_unset_password():
    assert passwords_match("s3cret", "s3cret") is True
    assert passwords_match(" s3cret ", "s3cret") is False
    assert passwords_match("wrong", "s3cret") is False
    assert passwords_match("", "") is False


def test_password_lockout_after_max_attempts_then_expires():
    assert password_lockout_remaining(PASSWORD_MAX_ATTEMPTS - 1, 100.0, 101.0) == 0.0
    assert password_lockout_remaining(PASSWORD_MAX_ATTEMPTS, 100.0, 110.0) == (
        PASSWORD_LOCKOUT_SECONDS - 10
    )
    assert password_lockout_remaining(PASSWORD_MAX_ATTEMPTS, 100.0, 100.0 + 10_000) == 0.0
