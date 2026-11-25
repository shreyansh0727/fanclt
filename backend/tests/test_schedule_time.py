from datetime import datetime, timezone

import pytest

from app.services.schedule_time import next_occurrence


def test_same_day_future_time():
    # Monday 2024-01-01 00:00 UTC; 07:30 IST == 02:00 UTC the same Monday.
    after = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    result = next_occurrence("07:30", [0], "Asia/Kolkata", after)
    assert result == datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)


def test_rolls_to_next_week_when_time_passed():
    # 12:00 UTC Monday is 17:30 IST, so 07:30 has already happened.
    after = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    result = next_occurrence("07:30", [0], "Asia/Kolkata", after)
    assert result == datetime(2024, 1, 8, 2, 0, tzinfo=timezone.utc)


def test_picks_next_selected_weekday():
    after = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)  # Monday
    result = next_occurrence("06:00", [2], "UTC", after)  # Wednesday
    assert result == datetime(2024, 1, 3, 6, 0, tzinfo=timezone.utc)


def test_timezone_moves_utc_boundary():
    after = datetime(2024, 1, 1, 20, 0, tzinfo=timezone.utc)  # Tue 01:30 IST
    result = next_occurrence("01:00", [1], "Asia/Kolkata", after)
    # Tue 01:00 IST is 30 minutes in the past -> the following Tuesday.
    assert result == datetime(2024, 1, 8, 19, 30, tzinfo=timezone.utc)

    result = next_occurrence("02:00", [1], "Asia/Kolkata", after)
    # Tue 02:00 IST == 2024-01-01 20:30 UTC, 30 minutes ahead.
    assert result == datetime(2024, 1, 1, 20, 30, tzinfo=timezone.utc)


def test_result_is_strictly_in_the_future():
    after = datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)  # exactly 07:30 IST
    result = next_occurrence("07:30", [0], "Asia/Kolkata", after)
    assert result > after
    assert result == datetime(2024, 1, 8, 2, 0, tzinfo=timezone.utc)


def test_all_days_covers_tomorrow():
    after = datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc)
    result = next_occurrence("00:30", list(range(7)), "UTC", after)
    assert result == datetime(2024, 1, 2, 0, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad_time", ["7:30", "24:00", "07:60", "0730"])
def test_time_format_is_validated(bad_time):
    from pydantic import ValidationError

    from app.models import ScheduleAction, ScheduleCreate

    with pytest.raises(ValidationError):
        ScheduleCreate(
            device_id="dev_x",
            time=bad_time,
            days=[0],
            timezone="UTC",
            action=ScheduleAction(type="set_all", on=True),
        )
