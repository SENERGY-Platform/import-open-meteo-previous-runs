# import-open-meteo-previous-runs

Imports archived weather forecasts, as they were issued at the time, from the
[Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api). The purpose is training
a day-ahead forecast (for example PV generation) on a year of forecasts instead of on observations.

For every valid hour `H` and every configured lead `N` (days) one message is published: the value the model
predicted `N * 24 h` before `H` (`<variable>_previous_dayN`).

## Time semantics

The envelope `time` of a message is the **issue time** `issued_at = H - N days` (UTC), not the valid time. An
export with the default TimePath `time` therefore stores each forecast at the moment it was available, and a
reader bounded at time `t` sees only forecasts issued before `t`. The valid time is in the payload as
`forecasted_for`.

`issued_at` is nominal. The underlying model run may have been initialised up to its run interval earlier and
published some hours later, so `lead_days = 1` can be slightly optimistic for a day-ahead forecast.
`lead_days >= 2` is safe.

## Outputs
* forecasted_for (string): valid time, RFC 3339 UTC
* issued_at (string): nominal issue time, RFC 3339 UTC, equal to the envelope `time`
* lead_days (int)
* shortwave_radiation (float, W/m²)
* direct_radiation (float, W/m²)
* diffuse_radiation (float, W/m²)
* direct_normal_irradiance (float, W/m²)
* cloud_cover (float, %)
* temperature_2m (float, °C)
* relative_humidity_2m (float, %)
* precipitation (float, mm)
* wind_speed_10m (float, km/h)
* units (structure): unit string per variable above

A variable that Open-Meteo has no value for is `null`. A forecast whose variables are all `null` is not published.

Example (`lead_days = 1`):

```json
{
  "import_id": "urn:infai:ses:import:a50aa583-282e-56c2-b101-7aaf68ebd2b9",
  "time": "2026-10-05T06:00:00Z",
  "value": {
    "forecasted_for": "2026-10-06T06:00:00Z",
    "issued_at": "2026-10-05T06:00:00Z",
    "lead_days": 1,
    "shortwave_radiation": 4.0,
    "direct_radiation": 0.0,
    "diffuse_radiation": 4.0,
    "direct_normal_irradiance": 0.0,
    "cloud_cover": 100,
    "temperature_2m": 8.9,
    "relative_humidity_2m": 100,
    "precipitation": 0.0,
    "wind_speed_10m": 3.3,
    "units": {
      "shortwave_radiation": "W/m²", "direct_radiation": "W/m²", "diffuse_radiation": "W/m²",
      "direct_normal_irradiance": "W/m²", "cloud_cover": "%", "temperature_2m": "°C",
      "relative_humidity_2m": "%", "precipitation": "mm", "wind_speed_10m": "km/h"
    }
  }
}
```

## Configs
* lat (float): latitude. Default: 51.34
* long (float): longitude. Default: 12.38
* start (string): first issue date, YYYY-MM-DD. Default: today minus 365 days. Most models are available from January 2024.
* lead_days (list of int): leads in days, each 1 to 7. Default: [1, 2]
* model (string): one Open-Meteo model. Default: `best_match`; passed as `models` only when different.
* apikey (string, optional): commercial Open-Meteo key. When set to a non-blank value, `customer-previous-runs-api.open-meteo.com` is used and the key is sent as `apikey`.

## Behaviour

On start the import reads the last published message and publishes every forecast whose issue time lies after it
and not after now, beginning at `start` when nothing was published yet. This backfills and continues live in one
loop, without duplicates across restarts. Messages go out in ascending order of `issued_at`, then
`forecasted_for`. The import then runs hourly, a few minutes past the hour. Requests are split into issue-time
windows of 31 days.

A forecast issued within the last three hours whose variables are all `null` is not skipped but retried on the
next run, because Open-Meteo may still fill it in.

Two things the resume point does not cover. A lead added to `lead_days` later is published only from the
resume point on; its earlier issue times stay missing, so recreate the import (new topic) to backfill it. And
when the topic has lost every message to retention, for instance after the import stood still for more than
about a week, the import starts again at `start` and republishes everything; the export then holds those rows
twice, which readers that drop exact duplicate rows tolerate.

## Export recommendation

Import topics are small (retention of a few days), so training on a year needs an export. Create the export
**before the first start** of the import, because the initial backfill is larger than the topic retention.

* TimePath: the default `time`.
* Columns: `value.forecasted_for`, `value.lead_days`, `value.issued_at` (optional) and the variables you need.

Registering this import type in import-repository is a separate, manual step and is not part of this repository.

## Tests

```
python3 -m venv .venv
.venv/bin/pip install -r pip-requirements.txt -r requirements-test.txt
.venv/bin/python -m pytest -q
```

## Licences and attribution

Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution is required.

The free API is for non-commercial use only. Commercial use needs an Open-Meteo subscription and the `apikey`
config.

Dependencies: [import-lib](https://github.com/SENERGY-Platform/import-lib) (Apache-2.0), requests (Apache-2.0),
rfc3339 (ISC); tests: pytest (MIT). This project is Apache-2.0, see `LICENSE.txt`.
