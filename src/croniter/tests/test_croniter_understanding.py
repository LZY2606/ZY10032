"""Understanding test for cursor/expansion coupling and direction switching.

See ``ANALYSIS.md`` at the repository root for the source walkthrough that this
test pins down. It deliberately uses only the public API (constructor,
``get_next``/``get_prev``, ``get_current`` and the documented ``expanded``
attribute) and an explicit UTC zone, so it neither reads private fields nor
depends on the host timezone.
"""

import datetime
import random
import warnings

from croniter import croniter
from croniter.tests import base

UTC = datetime.timezone.utc
SEED = 20260920
BASE = datetime.datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
EXPRESSION = "R R * * *"


def describe(dt):
    return f"{dt.isoformat()} fold={dt.fold} utcoffset={dt.utcoffset()}"


class CroniterUnderstandingCursorExpansionTest(base.TestCase):
    def test_understanding_next_next_prev_next_cursor_and_expansion(self):
        """Fix the PRNG seed, then run next, next, prev, next.

        Random expansion is drawn once while constructing the object, not while
        searching. Iteration therefore moves only the floating point cursor;
        the expanded field set stays frozen and the direction switch returns
        to the already-emitted slot instead of drawing a new schedule.
        """
        random.seed(SEED)
        cron = croniter(EXPRESSION, BASE, ret_type=datetime.datetime)
        expanded_at_construction = cron.expanded
        self.assertEqual(expanded_at_construction, [[6], [12], ["*"], ["*"], ["*"]])

        timeline = ["base: " + describe(BASE)]
        expected = [
            "next: 2026-01-01T12:06:00+00:00 fold=0 utcoffset=0:00:00",
            "next: 2026-01-02T12:06:00+00:00 fold=0 utcoffset=0:00:00",
            "prev: 2026-01-01T12:06:00+00:00 fold=1 utcoffset=0:00:00",
            "next: 2026-01-02T12:06:00+00:00 fold=0 utcoffset=0:00:00",
        ]
        actual = []
        for step, method in (
            ("next", cron.get_next),
            ("next", cron.get_next),
            ("prev", cron.get_prev),
            ("next", cron.get_next),
        ):
            result = method(datetime.datetime)
            line = f"{step}: {describe(result)}"
            actual.append(line)
            timeline.append(line)
            # The cursor is committed to every returned match.
            self.assertEqual(cron.get_current(datetime.datetime), result)

        self.assertEqual(actual, expected)
        # The expansion state never changes once the constructor finished, even
        # after switching search direction.
        self.assertEqual(cron.expanded, expanded_at_construction)

        # The draw happens at construction time: reseeding and building a second
        # object reproduces the same frozen schedule, independently of the first
        # object's cursor position.
        random.seed(SEED)
        sibling = croniter(EXPRESSION, BASE, ret_type=datetime.datetime)
        self.assertEqual(sibling.expanded, expanded_at_construction)
        self.assertEqual(
            sibling.get_next(datetime.datetime), datetime.datetime(2026, 1, 1, 12, 6, tzinfo=UTC)
        )

        warnings.warn(
            "understanding state timeline\n" + "\n".join(timeline),
            stacklevel=1,
        )
