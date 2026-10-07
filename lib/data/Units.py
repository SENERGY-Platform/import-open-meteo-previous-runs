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


from typing import Dict, List

# Fixed, PV-oriented variable list. Names are the Open-Meteo names without the "_previous_dayN" suffix.
# cloud_cover_low/mid/high are left out: the API accepts them but returns null for every hour (best_match, 2025-10 to 2026-10)
VARIABLES: List[str] = [
    'shortwave_radiation',
    'direct_radiation',
    'diffuse_radiation',
    'direct_normal_irradiance',
    'cloud_cover',
    'temperature_2m',
    'relative_humidity_2m',
    'precipitation',
    'wind_speed_10m',
]


class Units(object):

    def __init__(self, units: Dict[str, str]):
        self.units = {name: units.get(name) for name in VARIABLES}

    def dict(self) -> dict:
        return dict(self.units)
