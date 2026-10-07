# Copyright 2026 InfAI (CC SES)
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.


import sched
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import requests

from lib import OpenMeteoPreviousRunsImport as imp
from lib.data.Units import Units, VARIABLES
from lib.data.Value import Value


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def value(h: datetime, n: int, null=False) -> Value:
    return Value(h, h - timedelta(days=n), n, {v: (None if null else 1.0) for v in VARIABLES})


class FakeLib:
    def __init__(self, config=None, last=(None, None)):
        self.config = config or {}
        self.last = last
        self.puts = []

    def get_config(self, key, default):
        return self.config.get(key, default)

    def get_last_published_datetime(self):
        return self.last

    def put(self, t, v):
        self.puts.append((t, v))


def make(lib, now, fake_data, monkeypatch):
    '''fake_data(first, last, lead_days) -> list of Value; builds the import with a frozen clock'''
    calls = []

    def get_data(lat, long, first, last, lead_days, model, apikey):
        calls.append((first, last, list(lead_days), model, apikey))
        return Units({}), fake_data(first, last, lead_days)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(imp, 'get_data', get_data)
    monkeypatch.setattr(imp, 'datetime', Clock)
    sch = sched.scheduler()
    sch.enterabs = mock.Mock()
    return imp.OpenMeteoPreviousRunsImport(lib, sch), calls, sch


def grid(first, last, lead_days):
    '''every hour of the date range, every lead day, non-null'''
    d = datetime.strptime(first, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end = datetime.strptime(last, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1)
    out = []
    while d < end:
        out += [value(d, n) for n in lead_days]
        d += timedelta(hours=1)
    return out


# --- lead_days validation

@pytest.mark.parametrize('raw,expected', [([2, 1], [1, 2]), ([1, 1, 3], [1, 3]), (3, [3]), ('1, 2', [1, 2]),
                                          ([7], [7]), ([1.0], [1])])
def test_lead_days_valid(raw, expected):
    assert imp.parse_lead_days(raw) == expected


@pytest.mark.parametrize('raw', [[0], [8], [-1], [], '', 'a', [1.5], [True], None, [None], {'a': 1}])
def test_lead_days_invalid(raw):
    with pytest.raises(ValueError):
        imp.parse_lead_days(raw)


def test_invalid_config_raises(monkeypatch):
    with pytest.raises(ValueError):
        make(FakeLib({'lead_days': [0]}), utc(2026, 1, 10, 12, 3), grid, monkeypatch)
    with pytest.raises(ValueError):
        make(FakeLib({'model': 'a,b'}), utc(2026, 1, 10, 12, 3), grid, monkeypatch)


# --- windows and date ranges

def test_issue_windows_are_contiguous_and_capped():
    lo, hi = utc(2026, 1, 1), utc(2026, 4, 1)
    w = imp.issue_windows(lo, hi)
    assert w[0][0] == lo and w[-1][1] == hi
    assert all(b - a <= imp.CHUNK for a, b in w)
    assert all(w[i][1] == w[i + 1][0] for i in range(len(w) - 1))
    assert imp.issue_windows(hi, hi) == []


def test_valid_date_range_covers_all_leads():
    assert imp.valid_date_range(utc(2026, 1, 1), utc(2026, 1, 2), [1, 2]) == ('2026-01-02', '2026-01-03')
    assert imp.valid_date_range(utc(2026, 1, 1), utc(2026, 2, 1), [2]) == ('2026-01-03', '2026-02-02')


# --- select: resume filter, ordering, nulls

def test_select_only_after_watermark():
    vs = grid('2026-01-01', '2026-01-04', [1])
    a, b = utc(2026, 1, 1), utc(2026, 1, 5)
    out = imp.select(vs, a, b, (utc(2026, 1, 2, 10), datetime.max.replace(tzinfo=timezone.utc)), utc(2026, 2, 1))
    assert out[0].issued_at == utc(2026, 1, 2, 11)
    assert all(v.issued_at > utc(2026, 1, 2, 10) for v in out)


def test_select_tie_uses_forecasted_for():
    # issued_at 2026-01-02 10:00 has lead 1 (valid 01-03) and lead 2 (valid 01-04); the former is already out
    vs = [value(utc(2026, 1, 3, 10), 1), value(utc(2026, 1, 4, 10), 2)]
    out = imp.select(vs, utc(2026, 1, 1), utc(2026, 1, 5), (utc(2026, 1, 2, 10), utc(2026, 1, 3, 10)), utc(2026, 2, 1))
    assert [(v.lead_days, v.forecasted_for) for v in out] == [(2, utc(2026, 1, 4, 10))]


def test_select_window_bounds():
    vs = grid('2026-01-01', '2026-01-03', [1])
    out = imp.select(vs, utc(2026, 1, 1, 12), utc(2026, 1, 2, 12), None, utc(2026, 2, 1))
    assert out[0].issued_at == utc(2026, 1, 1, 12) and out[-1].issued_at == utc(2026, 1, 2, 11)


def test_select_orders_by_issued_at_then_forecasted_for():
    vs = [value(utc(2026, 1, 4, 10), 2), value(utc(2026, 1, 3, 10), 1), value(utc(2026, 1, 3, 9), 1),
          value(utc(2026, 1, 3, 11), 2)]
    out = imp.select(vs, utc(2026, 1, 1), utc(2026, 1, 9), None, utc(2026, 2, 1))
    keys = [(v.issued_at, v.forecasted_for) for v in out]
    assert keys == sorted(keys)
    assert out[0].issued_at == utc(2026, 1, 1, 11)  # 01-03 11:00 minus 2 days
    assert out[-1].forecasted_for == utc(2026, 1, 4, 10)  # issued 01-02 10:00 for lead 2 comes before 01-03 09:00


def test_select_skips_old_all_null_and_keeps_partial_nulls():
    old_null = value(utc(2026, 1, 3, 10), 1, null=True)
    partial = value(utc(2026, 1, 3, 11), 1)
    partial.values['precipitation'] = None
    out = imp.select([old_null, partial], utc(2026, 1, 1), utc(2026, 1, 9), None, utc(2026, 2, 1))
    assert out == [partial] and out[0].values['precipitation'] is None


def test_select_stops_at_young_all_null():
    now = utc(2026, 1, 10, 12, 3)
    ok = value(utc(2026, 1, 10, 9), 1)  # issued 01-09 09:00
    young_null = value(utc(2026, 1, 11, 11), 1, null=True)  # issued 01-10 11:00, within grace
    after = value(utc(2026, 1, 11, 12), 1)
    out = imp.select([ok, young_null, after], utc(2026, 1, 1), utc(2026, 1, 11), None, now)
    assert out == [ok]


# --- the import end to end

def test_first_run_starts_at_start_and_publishes_issue_time(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-08', 'lead_days': [1, 2]})
    _, calls, _ = make(lib, now, grid, monkeypatch)
    issued = [t for t, _ in lib.puts]
    assert issued[0] == utc(2026, 1, 8)
    assert issued[-1] == utc(2026, 1, 10, 12)  # now floored to the hour, inclusive
    assert issued == sorted(issued)
    t, v = lib.puts[0]
    assert v['issued_at'] == '2026-01-08T00:00:00Z' and t == utc(2026, 1, 8)
    assert v['forecasted_for'] == '2026-01-09T00:00:00Z' and v['lead_days'] == 1
    # one request, valid dates reach max(lead_days) days past now
    assert calls == [('2026-01-09', '2026-01-12', [1, 2], 'best_match', None)]
    # each (issued_at, lead) exactly once
    keys = [(v['issued_at'], v['lead_days']) for _, v in lib.puts]
    assert len(keys) == len(set(keys))


def test_default_start_is_one_year_back(monkeypatch):
    now = utc(2026, 10, 7, 6, 10)
    # the request starts 365 days back plus the smallest lead
    _, calls, _ = make(FakeLib(), now, lambda *a: [], monkeypatch)
    assert calls[0][0] == '2025-10-08'


def test_resume_does_not_republish(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    last = datetime(2026, 1, 9, 10)  # naive UTC, as import-lib returns it
    msg = {'forecasted_for': '2026-01-10T10:00:00Z', 'lead_days': 1}
    lib = FakeLib({'start': '2026-01-01', 'lead_days': [1]}, last=(last, msg))
    _, calls, _ = make(lib, now, grid, monkeypatch)
    assert lib.puts[0][0] == utc(2026, 1, 9, 11)
    assert all(t > utc(2026, 1, 9, 10) for t, _ in lib.puts)
    assert calls[0][0] == '2026-01-10'  # window starts at the watermark's issue hour


def test_resume_without_forecasted_for_treats_whole_issue_time_as_published(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-01', 'lead_days': [1, 2]}, last=(datetime(2026, 1, 9, 10), {}))
    make(lib, now, grid, monkeypatch)
    assert all(t > utc(2026, 1, 9, 10) for t, _ in lib.puts)


def test_nothing_to_do_when_up_to_date(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    last = datetime(2026, 1, 10, 12)
    msg = {'forecasted_for': '2026-01-11T12:00:00Z'}
    lib = FakeLib({'lead_days': [1]}, last=(last, msg))
    make(lib, now, grid, monkeypatch)
    assert lib.puts == []


def test_does_not_publish_future_issue_times(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-09', 'lead_days': [1, 2]})
    make(lib, now, grid, monkeypatch)  # grid returns hours whose issue time lies after now too
    assert max(t for t, _ in lib.puts) <= now


def test_multiple_chunks_stay_ascending_without_duplicates(monkeypatch):
    now = utc(2026, 3, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-01', 'lead_days': [1, 2, 3]})
    _, calls, _ = make(lib, now, grid, monkeypatch)
    assert len(calls) > 2
    keys = [(v['issued_at'], v['forecasted_for']) for _, v in lib.puts]
    assert keys == sorted(keys) and len(keys) == len(set(keys))


def test_config_passthrough(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-10', 'lead_days': [3], 'model': 'icon_d2', 'apikey': 'k', 'lat': 1, 'long': 2})
    _, calls, _ = make(lib, now, lambda *a: [], monkeypatch)
    assert calls[0][2:] == ([3], 'icon_d2', 'k')


def test_blank_apikey_uses_free_api(monkeypatch):
    now = utc(2026, 1, 10, 12, 3)
    lib = FakeLib({'start': '2026-01-10', 'apikey': ' '})
    _, calls, _ = make(lib, now, lambda *a: [], monkeypatch)
    assert calls[0][4] is None


# --- retry and scheduling

def build_failing(monkeypatch, exc, ok_after=None):
    state = {'n': 0}

    def get_data(*a):
        state['n'] += 1
        if ok_after is None or state['n'] <= ok_after:
            raise exc
        return Units({}), []

    monkeypatch.setattr(imp, 'get_data', get_data)
    monkeypatch.setattr(imp.time, 'sleep', lambda s: None)
    sch = sched.scheduler()
    sch.enterabs = mock.Mock()
    return imp.OpenMeteoPreviousRunsImport(FakeLib({'start': str(datetime.now(timezone.utc).date())}), sch), state, sch


def test_retries_connection_errors_and_still_schedules(monkeypatch):
    _, state, sch = build_failing(monkeypatch, requests.exceptions.ConnectionError('x'))
    assert state['n'] == 5
    assert sch.enterabs.call_count >= 1


def test_http_error_not_retried_but_next_run_scheduled(monkeypatch):
    _, state, sch = build_failing(monkeypatch, requests.exceptions.HTTPError('400'))
    assert state['n'] == 1
    assert sch.enterabs.call_count >= 1


def test_recovers_after_transient_failure(monkeypatch):
    _, state, _ = build_failing(monkeypatch, requests.exceptions.Timeout('t'), ok_after=2)
    assert state['n'] == 3


def test_next_run_is_a_few_minutes_past_the_hour(monkeypatch):
    _, _, sch = build_failing(monkeypatch, requests.exceptions.HTTPError('400'))
    ts = sch.enterabs.call_args[0][0]
    t = datetime.fromtimestamp(ts, timezone.utc)
    assert t.minute == imp.RUN_MINUTE and t.second == 0
    assert t > datetime.now(timezone.utc)
