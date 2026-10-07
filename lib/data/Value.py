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


from datetime import datetime
from typing import Dict, Optional

from rfc3339 import rfc3339

from lib.data.Units import Units, VARIABLES


def to_rfc3339(dt: datetime) -> str:
    return rfc3339(dt, utc=True, use_system_timezone=False)


class Value(object):

    def __init__(self, forecasted_for: datetime, issued_at: datetime, lead_days: int,
                 values: Dict[str, Optional[float]]):
        self.forecasted_for = forecasted_for
        self.issued_at = issued_at
        self.lead_days = lead_days
        self.values = {name: values.get(name) for name in VARIABLES}

    def all_null(self) -> bool:
        return all(v is None for v in self.values.values())

    def dict(self, units: Units) -> dict:
        d = {
            "forecasted_for": to_rfc3339(self.forecasted_for),
            "issued_at": to_rfc3339(self.issued_at),
            "lead_days": self.lead_days,
        }
        d.update(self.values)
        d["units"] = units.dict()
        return d
