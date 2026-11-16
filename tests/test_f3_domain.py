from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.blocks import BlockError, plan_block

# A fixed "now" so the tests never depend on the real clock.
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def utc(day, hour, minute=0):
    """A timezone-aware UTC datetime in November 2026."""
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def test_sc_01_eight_to_nine_gives_four_quarter_hour_slots():
    slots = plan_block(utc(3, 7), utc(3, 8), 15, NOW, [])

    assert [start.strftime("%H:%M") for start, end in slots] == ["07:00", "07:15", "07:30", "07:45"]
    assert all(end - start == timedelta(minutes=15) for start, end in slots)


def test_sc_02_leftover_minutes_are_not_a_slot():
    slots = plan_block(utc(3, 7), utc(3, 7, 50), 15, NOW, [])

    assert len(slots) == 3
    assert slots[-1][1].strftime("%H:%M") == "07:45"


def test_sc_03_slot_length_must_be_one_of_the_allowed_values():
    with pytest.raises(BlockError, match="slot length must be 10, 15, 20 or 30"):
        plan_block(utc(3, 7), utc(3, 8), 25, NOW, [])


@pytest.mark.parametrize("end", [utc(3, 6), utc(3, 7)])
def test_end_time_must_be_after_start_time(end):
    with pytest.raises(BlockError, match="end time must be after start time"):
        plan_block(utc(3, 7), end, 15, NOW, [])


def test_thirty_and_two_hundred_forty_minutes_are_accepted():
    assert len(plan_block(utc(3, 7), utc(3, 7, 30), 10, NOW, [])) == 3
    assert len(plan_block(utc(3, 7), utc(3, 11), 10, NOW, [])) == 24


@pytest.mark.parametrize("start,end", [(utc(3, 7), utc(3, 7, 20)), (utc(3, 7), utc(3, 11, 10))])
def test_block_length_must_be_between_30_and_240_minutes(start, end):
    with pytest.raises(BlockError, match="block must be between 30 and 240 minutes"):
        plan_block(start, end, 10, NOW, [])


def test_start_must_be_in_the_future():
    with pytest.raises(BlockError, match="block must start in the future"):
        plan_block(NOW, NOW + timedelta(hours=1), 15, NOW, [])


def test_a_block_may_touch_an_existing_one():
    slots = plan_block(utc(3, 8), utc(3, 9), 15, NOW, [(utc(3, 7), utc(3, 8))])

    assert len(slots) == 4


def test_a_block_may_not_overlap_an_existing_one():
    with pytest.raises(BlockError, match="block overlaps another of your blocks"):
        plan_block(utc(3, 7, 30), utc(3, 8, 30), 15, NOW, [(utc(3, 7), utc(3, 8))])


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        plan_block(datetime(2026, 11, 3, 7), utc(3, 8), 15, NOW, [])


def test_the_domain_package_never_reads_the_clock():
    domain_dir = Path(__file__).resolve().parents[1] / "app" / "domain"
    sources = list(domain_dir.rglob("*.py"))

    assert sources
    for source in sources:
        assert "datetime.now" not in source.read_text(encoding="utf-8"), source.name
