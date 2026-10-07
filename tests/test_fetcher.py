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


from datetime import datetime, timezone
from unittest import mock

import pytest
import requests

from lib.data import OpenMeteoPreviousRunsDataFetcher as fetcher
from lib.data.Units import VARIABLES, Units
from lib.data.Value import Value


def response(times, series, units=None, status=200):
    r = mock.Mock()
    r.ok = status < 400
    r.status_code = status
    r.text = ''
    r.json.return_value = {'hourly_units': units if units is not None else {k: 'u' for k in series},
                           'hourly': dict(series, time=times)}
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(str(status))
    return r


def full_series(lead_days, times, value=1.0):
    return {f"{v}_previous_day{n}": [value] * len(times) for v in VARIABLES for n in lead_days}


def test_hourly_list_uses_suffixes():
    names = fetcher.hourly_names([1, 2]).copy()
    assert len(names) == len(VARIABLES) * 2
    assert 'shortwave_radiation_previous_day1' in names and 'wind_speed_10m_previous_day2' in names
    assert all(n.rsplit('_previous_day', 1)[1] in ('1', '2') for n in names)


def test_params_default_model_has_no_models_and_no_apikey():
    p = fetcher.build_params(51.34, 12.38, '2026-01-01', '2026-01-31', [1])
    assert p['start_date'] == '2026-01-01' and p['end_date'] == '2026-01-31'
    assert p['timezone'] == 'UTC'
    assert 'models' not in p and 'apikey' not in p
    assert p['hourly'].split(',') == fetcher.hourly_names([1])


def test_params_other_model_and_apikey():
    p = fetcher.build_params(51.34, 12.38, '2026-01-01', '2026-01-31', [1], model='ecmwf_ifs025', apikey='k')
    assert p['models'] == 'ecmwf_ifs025'
    assert p['apikey'] == 'k'


def test_get_data_uses_public_host_without_apikey():
    times = ['2026-01-01T00:00']
    with mock.patch.object(fetcher.requests, 'get', return_value=response(times, full_series([1], times))) as g:
        fetcher.get_data(51.7, 10, '2026-01-01', '2026-01-01', [1])
    assert g.call_args[0][0] == fetcher.OPEN_METEO_URL
    assert 'apikey' not in g.call_args[1]['params']


def test_get_data_uses_customer_host_with_apikey():
    times = ['2026-01-01T00:00']
    with mock.patch.object(fetcher.requests, 'get', return_value=response(times, full_series([1], times))) as g:
        fetcher.get_data(51.7, 10, '2026-01-01', '2026-01-01', [1], apikey='secret')
    assert g.call_args[0][0] == fetcher.OPEN_METEO_CUSTOMER_URL
    assert g.call_args[1]['params']['apikey'] == 'secret'


def test_get_data_builds_one_value_per_hour_and_lead():
    times = ['2026-01-02T10:00', '2026-01-02T11:00']
    series = full_series([1, 2], times)
    series['temperature_2m_previous_day2'] = [5.5, None]
    units = {k: 'x' for k in series}
    with mock.patch.object(fetcher.requests, 'get', return_value=response(times, series, units)):
        u, values = fetcher.get_data(51.7, 10, '2026-01-02', '2026-01-02', [1, 2])
    assert len(values) == 4
    v = [x for x in values if x.lead_days == 2 and x.forecasted_for.hour == 10][0]
    assert v.forecasted_for == datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
    assert v.issued_at == datetime(2025, 12, 31, 10, tzinfo=timezone.utc)
    assert v.values['temperature_2m'] == 5.5
    assert u.dict()['temperature_2m'] == 'x'
    assert set(u.dict()) == set(VARIABLES)


def test_get_data_keeps_all_null_rows_for_the_caller():
    times = ['2026-01-02T10:00']
    series = {k: [None] for k in full_series([1], times)}
    with mock.patch.object(fetcher.requests, 'get', return_value=response(times, series)):
        _, values = fetcher.get_data(51.7, 10, '2026-01-02', '2026-01-02', [1])
    assert len(values) == 1 and values[0].all_null()


def test_get_data_rejects_malformed_response():
    r = mock.Mock(ok=True)
    r.json.return_value = {'hourly': {'time': []}}
    with mock.patch.object(fetcher.requests, 'get', return_value=r):
        with pytest.raises(RuntimeError):
            fetcher.get_data(51.7, 10, '2026-01-02', '2026-01-02', [1])


def test_get_data_raises_http_error():
    with mock.patch.object(fetcher.requests, 'get', return_value=response([], {}, status=400)):
        with pytest.raises(requests.exceptions.HTTPError):
            fetcher.get_data(51.7, 10, '2026-01-02', '2026-01-02', [1])


def test_value_dict_format_and_nulls():
    t = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
    v = Value(t, datetime(2026, 1, 1, 10, tzinfo=timezone.utc), 1, {'cloud_cover': 12, 'temperature_2m': None})
    d = v.dict(Units({'cloud_cover': '%'}))
    assert d['forecasted_for'] == '2026-01-02T10:00:00Z'
    assert d['issued_at'] == '2026-01-01T10:00:00Z'
    assert d['lead_days'] == 1
    assert d['cloud_cover'] == 12 and d['temperature_2m'] is None
    assert d['units']['cloud_cover'] == '%'
    assert [k for k in d if k in VARIABLES] == VARIABLES
