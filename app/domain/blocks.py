"""Block planning rules. Standard library only, and no clock here: "now" is passed in."""

from datetime import timedelta

from app.domain.constants import ALLOWED_SLOT_LENGTHS, BLOCK_MAX_MIN, BLOCK_MIN_MIN


class BlockError(ValueError):
    """A block request that breaks a rule. The message is shown to the user."""


def _require_aware(*values):
    """Every datetime we plan with must be timezone-aware."""
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetimes must be timezone-aware UTC")


def plan_block(start_utc, end_utc, slot_length_min, now_utc, existing_blocks):
    """Return the slots of a new block as (slot_start, slot_end) pairs."""
    _require_aware(start_utc, end_utc, now_utc)

    if slot_length_min not in ALLOWED_SLOT_LENGTHS:
        raise BlockError("slot length must be 10, 15, 20 or 30")

    if end_utc <= start_utc:
        raise BlockError("end time must be after start time")

    length_min = int((end_utc - start_utc).total_seconds() // 60)
    if length_min < BLOCK_MIN_MIN or length_min > BLOCK_MAX_MIN:
        raise BlockError("block must be between 30 and 240 minutes")

    if start_utc <= now_utc:
        raise BlockError("block must start in the future")

    for old_start, old_end in existing_blocks:
        _require_aware(old_start, old_end)
        # Half-open intervals: touching (one ends where the other starts) is fine.
        if start_utc < old_end and old_start < end_utc:
            raise BlockError("block overlaps another of your blocks")

    step = timedelta(minutes=slot_length_min)
    slots = []
    slot_start = start_utc
    while slot_start + step <= end_utc:
        slots.append((slot_start, slot_start + step))
        slot_start = slot_start + step
    return slots


def check_block_cancellable(block_start_utc, block_status, now_utc):
    """Raise BlockError when this block can no longer be cancelled."""
    _require_aware(block_start_utc, now_utc)

    if block_status == "cancelled":
        raise BlockError("This block is already cancelled")

    # A block starting exactly now is no longer future.
    if block_start_utc <= now_utc:
        raise BlockError("Only future blocks can be cancelled")
