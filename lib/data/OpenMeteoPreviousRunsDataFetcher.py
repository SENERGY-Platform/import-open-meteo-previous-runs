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


from datetime import datetime, timedelta, timezone
from typing import Tuple, List, Optional

import requests
from import_lib.import_lib import get_logger

from lib.data.Units import Units, VARIABLES
from lib.data.Value import Value

OPEN_METEO_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
OPEN_METEO_CUSTOMER_URL = "https://customer-previous-runs-api.open-meteo.com/v1/forecast"
DEFAULT_MODEL = "best_match"
MAX_LEAD_DAYS = 7
TIMEOUT_SECONDS = 60

logger = get_logger(__name__)


def hourly_names(lead_days: List[int]) -> List[str]:
    return [f"{var}_previous_day{n}" for var in VARIABLES for n in lead_days]


def build_params(lat: float, long: float, start_date: str, end_date: str, lead_days: List[int],
                 model: str = DEFAULT_MODEL, apikey: Optional[str] = None) -> dict:
    params = {
        'latitude': round(lat, 4),
        'longitude': round(long, 4),
        'timezone': 'UTC',
        'start_date': start_date,
        'end_date': end_date,
        'hourly': ','.join(hourly_names(lead_days)),
    }
    if model != DEFAULT_MODEL:
        params['models'] = model
    if apikey:
        params['apikey'] = apikey
    return params


def get_data(lat: float, long: float, start_date: str, end_date: str, lead_days: List[int],
             model: str = DEFAULT_MODEL, apikey: Optional[str] = None) -> Tuple[Units, List[Value]]:
    '''
    Fetches the forecasts for all valid hours from start_date to end_date (UTC dates, inclusive).
    :return: Units, one Value per (valid hour, lead day), including those whose variables are all null
    '''
    url = OPEN_METEO_CUSTOMER_URL if apikey else OPEN_METEO_URL
    r = requests.get(url, params=build_params(lat, long, start_date, end_date, lead_days, model, apikey),
                     timeout=TIMEOUT_SECONDS)
    if not r.ok:
        # The body names the reason; it never contains the api key.
        logger.info(f"Open-Meteo answered {r.status_code}: {r.text[:300]}")
    r.raise_for_status()

    j = r.json()
    if 'hourly_units' not in j or 'hourly' not in j or 'time' not in j['hourly']:
        raise RuntimeError("Error: Invalid Open-Meteo Response")
    hourly = j['hourly']
    for name, series in hourly.items():
        if len(series) != len(hourly['time']):
            raise RuntimeError('Error: Invalid Open-Meteo Response')

    api_units = j['hourly_units']
    units = {}
    for var in VARIABLES:
        for n in lead_days:
            unit = api_units.get(f"{var}_previous_day{n}")
            if unit is not None:
                units[var] = unit
                break

    values: List[Value] = []
    for i, t in enumerate(hourly['time']):
        forecasted_for = datetime.strptime(t, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
        for n in lead_days:
            v = Value(
                forecasted_for=forecasted_for,
                # Nominal issue time: the previous_dayN value is the one predicted N*24h before the valid time.
                issued_at=forecasted_for - timedelta(days=n),
                lead_days=n,
                values={var: hourly.get(f"{var}_previous_day{n}", [None] * len(hourly['time']))[i]
                        for var in VARIABLES})
            values.append(v)
    return Units(units), values
