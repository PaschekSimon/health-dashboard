"""cardio_fitness: a daily value kept apart from the reported band.

The field names come from the published schema rather than from a reading.
What can be pinned without live data is where each value lands: cardio fitness
keeps a reported band and a single value in separate columns, because one
number written into both ends of the band would quietly destroy it.
"""

from datetime import date
from unittest.mock import patch

import pytest

from google_api import gh_queries as gq
from health_dashboard.google import cache as db
from health_dashboard.google import handlers as google_sync


def _daily_point(field, value, day=15):
    return {field: {"date": {"year": 2026, "month": 3, "day": day}, **value}}




@pytest.fixture
def sync_cardio_fitness(tmp_db):
    def run(points):
        with patch.object(google_sync.api, "list_google_data_points", return_value=points):
            count = google_sync.sync_cardio_fitness(tmp_db, date(2026, 3, 1), date(2026, 4, 1))
        tmp_db.commit()
        return count

    return run




class TestCardioFitnessKeepsTwoDefinitionsApart:
    def test_the_single_value_lands_in_its_own_column(self, tmp_db, sync_cardio_fitness):
        sync_cardio_fitness([_daily_point("dailyVo2Max", {"vo2Max": 42.5})])
        row = gq.query_cardio_fitness(tmp_db, "2026-03-15", "2026-03-15")[0]
        assert row["vo2_max"] == 42.5

    def test_a_stored_range_is_never_overwritten_by_it(self, tmp_db, sync_cardio_fitness):
        """A single value is not a band, and the band columns are not its ends.

        Writing the one value into both ends of the band would collapse a real
        four-wide range and read as a measurement rather than as a change of
        provider - and a trend averaging the two columns steps on the day the
        imported series ends.
        """
        db.save_cardio_fitness(
            tmp_db, {"date": "2026-03-15", "vo2_max_low": 40.0, "vo2_max_high": 44.0}
        )
        tmp_db.commit()
        sync_cardio_fitness([_daily_point("dailyVo2Max", {"vo2Max": 42.0})])
        row = gq.query_cardio_fitness(tmp_db, "2026-03-15", "2026-03-15")[0]
        assert (row["vo2_max_low"], row["vo2_max_high"]) == (40.0, 44.0)
        assert row["vo2_max"] == 42.0

    @pytest.mark.parametrize(
        "qualifier",
        [{"cardioFitnessLevel": "GOOD"}, {"estimated": True}, {"vo2MaxCovariance": 1.5}],
        ids=["level", "estimated", "covariance"],
    )
    def test_a_point_carrying_no_value_stores_nothing(self, tmp_db, sync_cardio_fitness, qualifier):
        """A day with no reading is not a day of zero cardio fitness."""
        sync_cardio_fitness([_daily_point("dailyVo2Max", qualifier)])
        assert gq.query_cardio_fitness(tmp_db, "2026-03-15", "2026-03-15") == []

    def test_a_revised_value_withdraws_the_qualifiers_of_the_old_one(
        self, tmp_db, sync_cardio_fitness
    ):
        """They describe the measurement they came with, not whatever replaces it."""
        first = _daily_point(
            "dailyVo2Max",
            {
                "vo2Max": 44.5,
                "cardioFitnessLevel": "GOOD",
                "estimated": True,
                "vo2MaxCovariance": 2.0,
            },
        )
        first["dataSource"] = {"platform": "FICTIONAL"}
        sync_cardio_fitness([first])
        sync_cardio_fitness([_daily_point("dailyVo2Max", {"vo2Max": 46.0})])
        (row,) = gq.query_cardio_fitness(tmp_db, "2026-03-15", "2026-03-15")
        assert row["vo2_max"] == 46.0
        for column in ("cardio_fitness_level", "estimated", "vo2_max_covariance", "data_source"):
            assert row[column] is None, column


