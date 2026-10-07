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
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Any

import requests

from import_lib.import_lib import get_logger, ImportLib

from lib.data.OpenMeteoPreviousRunsDataFetcher import get_data, DEFAULT_MODEL, MAX_LEAD_DAYS
from lib.data.Units import Units
from lib.data.Value import Value

logger = get_logger(__name__)

CHUNK = timedelta(days=31)  # width of the issue-time window fetched per request
GRACE = timedelta(hours=3)  # younger all-null forecasts may still be filled in by Open-Meteo, so they are retried
RUN_MINUTE = 5  # next run at this minute past the hour

# (issued_at, forecasted_for): messages are published in ascending order of this key
Key = Tuple[datetime, datetime]


def parse_lead_days(raw: Any) -> List[int]:
    '''Accepts a list of ints, a single int or a comma separated string. Returns the sorted, unique days (1..7).'''
    if isinstance(raw, str):
        raw = [p for p in raw.replace(' ', '').split(',') if p != '']
    elif isinstance(raw, int) and not isinstance(raw, bool):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise ValueError("lead_days must be a non-empty list of integers between 1 and %d" % MAX_LEAD_DAYS)
    days = []
    for d in raw:
        try:
            if isinstance(d, bool) or (isinstance(d, float) and d != int(d)):
                raise ValueError
            d = int(d)
        except (ValueError, TypeError):
            raise ValueError("lead_days must contain integers only, got %r" % (d,))
        if d < 1 or d > MAX_LEAD_DAYS:
            raise ValueError("lead_days must be between 1 and %d, got %d" % (MAX_LEAD_DAYS, d))
        days.append(d)
    return sorted(set(days))


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def issue_windows(lo: datetime, hi: datetime) -> List[Tuple[datetime, datetime]]:
    '''Splits the issue-time range [lo, hi) into consecutive windows of at most CHUNK.'''
    windows = []
    a = lo
    while a < hi:
        b = min(a + CHUNK, hi)
        windows.append((a, b))
        a = b
    return windows


def valid_date_range(a: datetime, b: datetime, lead_days: List[int]) -> Tuple[str, str]:
    '''UTC dates (inclusive) that hold every valid hour whose issue time lies in [a, b).'''
    first = a + timedelta(days=min(lead_days))
    last = b - timedelta(hours=1) + timedelta(days=max(lead_days))
    return str(first.date()), str(last.date())


def select(values: List[Value], a: datetime, b: datetime, after: Optional[Key], now: datetime) -> List[Value]:
    '''
    Picks what is published from a fetched window: issue time in [a, b), strictly after the watermark, ordered by
    (issued_at, forecasted_for). All-null forecasts are dropped. A young all-null forecast ends the selection
    instead, so that the next run retries it rather than skipping it for good.
    '''
    candidates = [v for v in values if a <= v.issued_at < b and (after is None or (v.issued_at, v.forecasted_for) > after)]
    candidates.sort(key=lambda v: (v.issued_at, v.forecasted_for))
    result = []
    for v in candidates:
        if v.all_null():
            if v.issued_at > now - GRACE:
                break
            continue
        result.append(v)
    return result


class OpenMeteoPreviousRunsImport:
    def __init__(self, lib: ImportLib, scheduler: sched.scheduler):
        self.__lib = lib
        self.__scheduler = scheduler
        self.__lat = float(self.__lib.get_config("lat", 51.34))
        self.__long = float(self.__lib.get_config("long", 12.38))
        start = self.__lib.get_config('start', str(datetime.now(timezone.utc).date() - timedelta(days=365)))
        self.__start = datetime.strptime(str(start)[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        self.__lead_days = parse_lead_days(self.__lib.get_config("lead_days", [1, 2]))
        self.__model = str(self.__lib.get_config("model", DEFAULT_MODEL))
        if self.__model == '' or ',' in self.__model:
            raise ValueError("model must name exactly one model")
        self.__apikey = self.__lib.get_config("apikey", None) or None
        self.__watermark: Optional[Key] = self.__read_watermark()
        self.import_current_with_retry()

    def __read_watermark(self) -> Optional[Key]:
        last, message = self.__lib.get_last_published_datetime()
        if last is None:
            return None
        issued_at = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
        # Within one issue time the messages are ordered by forecasted_for, so the last message tells how many of
        # them are already out. Without it the whole issue time counts as published.
        forecasted_for = datetime.max.replace(tzinfo=timezone.utc)
        try:
            forecasted_for = parse_utc(message['forecasted_for'])
        except (KeyError, TypeError, ValueError):
            pass
        return issued_at, forecasted_for

    def import_current_with_retry(self, max_attempts: int = 5, base_sleep: float = 2.0) -> bool:
        try:
            return self.__import_with_retry(max_attempts, base_sleep)
        finally:
            self.__schedule_next()

    def __import_with_retry(self, max_attempts: int, base_sleep: float) -> bool:
        for attempt in range(1, max_attempts + 1):
            try:
                self.import_current()
                return True

            except requests.exceptions.ConnectionError as e:
                logger.info(f"Open-Meteo-Verbindungsfehler, Versuch {attempt}/{max_attempts}: {e}")

            except requests.exceptions.Timeout as e:
                logger.info(f"Open-Meteo-Timeout, Versuch {attempt}/{max_attempts}: {e}")

            except requests.exceptions.HTTPError as e:
                logger.info(f"Open-Meteo-HTTP-Fehler, kein weiterer Retry: {e}")
                return False

            if attempt < max_attempts:
                time.sleep(base_sleep * attempt)

        logger.info("Open-Meteo-Import nach mehreren Versuchen fehlgeschlagen. App läuft weiter.")
        return False

    def __schedule_next(self):
        now = datetime.now(timezone.utc)
        next_run = floor_hour(now) + timedelta(hours=1, minutes=RUN_MINUTE)
        logger.info("Scheduling next run for " + str(next_run))
        self.__scheduler.enterabs(next_run.timestamp(), 1, self.import_current_with_retry)

    def import_current(self):
        logger.info("Open-Meteo previous runs import_current started")
        now = datetime.now(timezone.utc)
        hi = floor_hour(now) + timedelta(hours=1)  # exclusive: an issue time is available from its full hour on
        lo = self.__start if self.__watermark is None else max(self.__start, floor_hour(self.__watermark[0]))
        total = 0
        for a, b in issue_windows(lo, hi):
            first, last = valid_date_range(a, b, self.__lead_days)
            logger.info(f"Fetching forecasts issued from {a} to {b} (valid {first} to {last})")
            units, values = get_data(self.__lat, self.__long, first, last, self.__lead_days, self.__model,
                                     self.__apikey)
            selected = select(values, a, b, self.__watermark, now)
            self.__publish(selected, units)
            total += len(selected)
        logger.info("Imported " + str(total) + " values")

    def __publish(self, values: List[Value], units: Units):
        for v in values:
            # The envelope time is the issue time, not the valid time: an export with the default TimePath `time`
            # then stores each forecast at the moment it became available, and a reader bounded at time t sees only
            # forecasts issued before t. The valid time travels in the payload as forecasted_for.
            self.__lib.put(v.issued_at, v.dict(units))
            self.__watermark = (v.issued_at, v.forecasted_for)
            logger.debug(json.dumps(v.dict(units)))
