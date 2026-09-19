#!/usr/bin/env python
"""
Tests pinning down croniter's cursor/expansion-state relationship, as
described in ANALYSIS.md. Every test uses an explicit timezone (never the
system timezone) and only the public API (no private attributes).
"""
# -*- coding: utf-8 -*-

import random
from datetime import datetime

import pytest
from dateutil.tz import gettz

from croniter import CroniterBadDateError, croniter

NY = gettz("America/New_York")


def _fmt(d):
    return f"{d.isoformat()} fold={d.fold} offset={d.utcoffset()}"


def _emit(capsys, label, d):
    # Bypass pytest output capture so each step's ISO time, fold and UTC
    # offset are visible in the test output as ANALYSIS.md requires.
    with capsys.disabled():
        print(f"{label}: {_fmt(d)}")


def test_understanding_cursor_and_expansion_state(capsys):
    """Fixed seed, then next, next, prev, next.

    Proves that (a) the R expansion is decided once at construction time
    from the random seed, (b) get_next/get_prev only move the cursor and
    never re-expand, so direction switches stay on the same schedule.
    """
    random.seed(1234)
    it = croniter(
        "R 2 * * *", datetime(2026, 3, 1, 0, 0, tzinfo=NY), ret_type=datetime
    )
    _emit(capsys, "base ", it.get_current(datetime))

    n1 = it.get_next()
    _emit(capsys, "next1", n1)
    n2 = it.get_next()
    _emit(capsys, "next2", n2)
    p1 = it.get_prev()
    _emit(capsys, "prev1", p1)
    n3 = it.get_next()
    _emit(capsys, "next3", n3)

    # Same wall-clock minute every day: the expansion never changed.
    assert n1.isoformat() == "2026-03-01T02:47:00-05:00"
    assert n2.isoformat() == "2026-03-02T02:47:00-05:00"
    # prev from n2 lands back on n1's wall time; is_prev sets fold=1
    # mechanically (non-ambiguous here, so the offset is unchanged).
    assert p1.isoformat() == "2026-03-01T02:47:00-05:00"
    assert p1.fold == 1
    assert n3.isoformat() == "2026-03-02T02:47:00-05:00"
    assert n3.fold == 0
    # The cursor now sits on n3.
    assert it.get_current(datetime).isoformat() == "2026-03-02T02:47:00-05:00"

    # Re-seeding with the same seed and re-constructing reproduces the exact
    # same expansion: the schedule depends on the seed at construction, not
    # on the cursor.
    random.seed(1234)
    it2 = croniter(
        "R 2 * * *", datetime(2026, 3, 1, 0, 0, tzinfo=NY), ret_type=datetime
    )
    assert it2.get_next().isoformat() == n1.isoformat()

    # A same-seed instance started at the current cursor continues the
    # sequence identically: iterator state == (cursor, fixed expansion).
    random.seed(1234)
    it3 = croniter("R 2 * * *", n3, ret_type=datetime)
    continued = it3.get_next()
    _emit(capsys, "contd", continued)
    assert continued.isoformat() == it.get_next().isoformat() == "2026-03-03T02:47:00-05:00"

    # A different seed expands to a different minute.
    random.seed(999)
    it4 = croniter(
        "R 2 * * *", datetime(2026, 3, 1, 0, 0, tzinfo=NY), ret_type=datetime
    )
    other = it4.get_next()
    _emit(capsys, "othsd", other)
    assert other.isoformat() == "2026-03-01T02:07:00-05:00"
    assert other.minute != n1.minute


def test_understanding_failed_search_keeps_cursor(capsys):
    """A search that raises CroniterBadDateError must not advance the cursor."""
    base = datetime(2026, 3, 1, 0, 0, tzinfo=NY)
    it = croniter("0 0 30 2 *", base, ret_type=datetime)  # Feb 30 never exists
    _emit(capsys, "base ", it.get_current(datetime))
    with pytest.raises(CroniterBadDateError):
        it.get_next()
    after = it.get_current(datetime)
    _emit(capsys, "after", after)
    assert after.isoformat() == "2026-03-01T00:00:00-05:00"


def test_understanding_direction_switch_steps_back(capsys):
    """next then prev does not return to the base: prev re-matches one step
    earlier because the backward search starts 1 microsecond before the
    cursor and truncates to the minute."""
    it = croniter("*/5 * * * *", datetime(2024, 1, 25, 4, 46), ret_type=datetime)
    _emit(capsys, "base ", it.get_current(datetime))
    n1 = it.get_next()
    _emit(capsys, "next1", n1)
    p1 = it.get_prev()
    _emit(capsys, "prev1", p1)
    assert n1.isoformat() == "2024-01-25T04:50:00"
    assert p1.isoformat() == "2024-01-25T04:45:00"


def test_understanding_dst_gap_and_overlap(capsys):
    """America/New_York 2026: spring gap is skipped forward, fall overlap
    is yielded twice, distinguished by fold/utcoffset."""
    it = croniter(
        "30 2 * * *", datetime(2026, 3, 7, 12, 0, tzinfo=NY), ret_type=datetime
    )
    gap = it.get_next()
    _emit(capsys, "gap  ", gap)
    assert gap.isoformat() == "2026-03-08T03:00:00-04:00"

    it = croniter(
        "30 1 * * *", datetime(2026, 10, 31, 12, 0, tzinfo=NY), ret_type=datetime
    )
    first = it.get_next()
    _emit(capsys, "fold0", first)
    second = it.get_next()
    _emit(capsys, "fold1", second)
    third = it.get_next()
    _emit(capsys, "nextd", third)
    assert first.isoformat() == "2026-11-01T01:30:00-04:00"
    assert first.fold == 0
    assert second.isoformat() == "2026-11-01T01:30:00-05:00"
    assert second.fold == 1
    assert third.isoformat() == "2026-11-02T01:30:00-05:00"
