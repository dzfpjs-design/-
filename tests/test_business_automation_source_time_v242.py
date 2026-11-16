"""DB datetime and saved-wire timestamp boundaries; no database substitutes.

The real PG source fixture separately passes actual aware DB datetimes into
new_units/select_unit. These tests prevent normalization from guessing a zone
or accepting a falsey invalid not_before boundary.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import business_automation_sources as source


UTC = datetime(2026, 10, 28, 1, 2, 3, 123456, timezone.utc)


@pytest.mark.parametrize('value', [UTC, UTC.astimezone(timezone(timedelta(hours=8))),
    '2026-10-28T01:02:03.123456Z', '2026-10-28T09:02:03.123456+08:00',
    '2026-10-27T18:02:03.123456-07:00'])
def test_real_aware_db_clocks_and_saved_offset_strings_share_one_utc_instant(value):
    result = source.instant(value)
    assert result == UTC and result.tzinfo == timezone.utc


@pytest.mark.parametrize('value', [datetime(2026,10,28,1,2,3), date(2026,10,28),
    '2026-10-28T01:02:03', '2026-10-28', '', None, True, False, 0, 1.0, {}, []])
def test_naive_or_non_timestamp_values_are_rejected_instead_of_using_host_timezone(value):
    with pytest.raises(HTTPException) as error: source.instant(value)
    assert error.value.status_code == 422


@pytest.mark.parametrize('value', [False, 0, '', datetime(2026,10,28)])
def test_optional_source_lower_bound_validates_present_falsey_values_before_query(value):
    class NoDatabase:
        def execute(self, *args): pytest.fail('Invalid timestamp must be rejected before source query')
    with pytest.raises(HTTPException) as error:
        source.select_unit(NoDatabase(), NS(workspace_id='not-read'), {}, {}, at=UTC, not_before=value)
    assert error.value.status_code == 422
