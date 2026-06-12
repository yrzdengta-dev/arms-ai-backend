from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a naive UTC timestamp for the existing database columns."""
    return datetime.now(UTC).replace(tzinfo=None)
