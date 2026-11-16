"""Booking rules. Standard library only, and no clock here: "now" is passed in."""

from datetime import timedelta

from app.domain.constants import CANCEL_CUTOFF_MIN, TOPIC_MAX_CHARS


class BookingError(ValueError):
    """A booking request that breaks a rule. The message is shown to the user."""


def _require_aware(*values):
    """Every datetime we reason about must be timezone-aware."""
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetimes must be timezone-aware UTC")


def clean_topic(topic):
    """The topic without surrounding spaces. Empty is allowed."""
    topic = (topic or "").strip()
    if len(topic) > TOPIC_MAX_CHARS:
        raise BookingError(f"topic must be at most {TOPIC_MAX_CHARS} characters")
    return topic


def check_slot_bookable(slot_start_utc, block_status, now_utc):
    """Raise BookingError when this slot can no longer be booked."""
    _require_aware(slot_start_utc, now_utc)

    if block_status == "cancelled" or slot_start_utc <= now_utc:
        raise BookingError("This slot is no longer available")


def check_cancellable(slot_start_utc, now_utc):
    """Raise BookingError when this booking is too close to its slot to cancel."""
    _require_aware(slot_start_utc, now_utc)

    # Exactly the cutoff is still in time; anything less is not.
    if slot_start_utc - now_utc < timedelta(minutes=CANCEL_CUTOFF_MIN):
        raise BookingError("Too late to cancel: less than 120 minutes before the start")
