"""Air4Thai client: real-time + historical PM2.5/PM10 from the Thai
Pollution Control Department (PCD), for bias-correcting Open-Meteo's CAMS
Global PM data against real Thai ground stations. No API key for either
endpoint (verified live, 2026-08-09):

- Real-time: https://air4thai.pcd.go.th/services/getNewAQI_JSON.php
  174 stations nationwide, 24-hour rolling average, updated hourly.
- Historical: https://pcd.gdcatalog.go.th (PCD's official CKAN open-data
  portal), one CSV/API resource per calendar year, 2554-2568 BE
  (2011-2025 CE) at time of writing. The *current* year isn't published
  here until it ends -- get_realtime_stations() is the only source for
  anything more recent than the last complete year.

  The dataset id used here (12_02_aaq_pm2-5_bkk_metro) is titled "Bangkok
  Metropolitan Region" on the catalog page, but verified live: its
  station list is NOT actually Bangkok-only -- e.g. station "36T" is
  Yupparaj Wittayalai School, Chiang Mai. Confirmed coverage (as of
  2026-08-09) for all 5 site presets in app.py (Bangkok, Chiang Mai, Khon
  Kaen, Rayong, Hat Yai), so don't trust the dataset's title over
  find_nearest_station()'s actual distance check -- it may cover more of
  Thailand than the name implies, but there's no guarantee it's
  literally nationwide either.
"""

import hashlib
import json
import re
import warnings
from pathlib import Path

import pandas as pd
import requests
import urllib3

# pcd.gdcatalog.go.th / air4thai.pcd.go.th serve an incomplete certificate
# chain (missing intermediate) that Python's default trust store can't
# verify, independent of this being a legitimate government domain --
# confirmed manually (browser + curl -k) during development. This is a
# read-only public-data endpoint, not a credentials/payment context.
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# Air4Thai/PCD's CKAN endpoints are effectively unreachable from Render's
# Oregon-based free-tier compute (confirmed live 2026-08-17 via per-stage
# timing logs on the deployed gritwatch backend: both PM-correction call
# sites hung for ~30s each -- exactly the old timeout value -- before
# giving up, costing ~60s of every dashboard load for a source this
# module's own callers already treat as optional and degrade gracefully
# without). A real but slow-to-respond connection would rarely need more
# than a few seconds for this small a JSON payload; 8s is enough headroom
# for genuine latency while cutting the all-too-common total-timeout case
# roughly 4x.
REQUEST_TIMEOUT_S = 8

REALTIME_URL = "https://air4thai.pcd.go.th/services/getNewAQI_JSON.php"
CKAN_BASE = "https://pcd.gdcatalog.go.th/api/3/action"
PM25_DATASET_ID = "12_02_aaq_pm2-5_bkk_metro"
# Verified live (2026-08-14): same structure/coverage as PM25_DATASET_ID
# (observation_date, station_code "NNT" format, measured_value, 24-hour avg),
# also spans 2554-2568 BE (2011-2025 CE) despite the different dataset id
# pattern -- "nationwide" in the name, unlike PM2.5's "bkk_metro" title, but
# both turned out broader/narrower than their titles suggest; trust the live
# station_code check, not the name, same as PM25_DATASET_ID's caveat above.
PM10_DATASET_ID = "12_03_aaq_pm10_nationwide"


def _cache_path(endpoint: str, params: dict) -> Path:
    key_params = {k: params[k] for k in sorted(params)}
    digest = hashlib.sha256(json.dumps(key_params, sort_keys=True).encode()).hexdigest()[:24]
    endpoint_name = endpoint.rstrip("/").rsplit("/", 1)[-1]
    return CACHE_DIR / f"{endpoint_name}_{digest}.json"


def _fetch_json(endpoint: str, params: dict) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(endpoint, params)
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    response = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT_S, verify=False)
    response.raise_for_status()
    payload = response.json()
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def get_realtime_stations() -> pd.DataFrame:
    """All Air4Thai stations' latest reading (24-hour rolling average).
    Columns: station_code, name_en, lat, lon, pm2_5, pm10, observed_at
    (a single combined date+time string, station-local).

    getNewAQI_JSON.php takes no real query params; `t` below is a
    harmless extra param (ignored server-side) whose only job is to
    change hourly so _fetch_json's params-hash cache refreshes at
    roughly Air4Thai's own update cadence instead of caching this
    "real-time" endpoint forever.
    """
    hour_bucket = pd.Timestamp.now().strftime("%Y%m%d%H")
    payload = _fetch_json(REALTIME_URL, {"t": hour_bucket})
    rows = []
    for s in payload["stations"]:
        aqi_last = s.get("AQILast", {})
        pm25_raw = aqi_last.get("PM25", {}).get("value")
        pm10_raw = aqi_last.get("PM10", {}).get("value")
        rows.append({
            "station_code": s["stationID"].upper(),
            "name_en": s.get("nameEN", "").strip(),
            "lat": float(s["lat"]),
            "lon": float(s["long"]),
            "pm2_5": float(pm25_raw) if pm25_raw not in (None, "-1", "") else None,
            "pm10": float(pm10_raw) if pm10_raw not in (None, "-1", "") else None,
            "observed_at": f"{aqi_last.get('date')} {aqi_last.get('time')}",
        })
    return pd.DataFrame(rows)


def find_nearest_station(
    lat: float, lon: float, stations_df: pd.DataFrame | None = None, valid_codes: set | None = None
) -> pd.Series:
    """Nearest Air4Thai station to (lat, lon) by simple squared-distance in
    degrees -- fine for "which station is closest" at Thailand's scale,
    not for precise distance-in-km.

    valid_codes restricts the candidate pool -- pass the output of
    get_historical_station_codes() when the result will be used with
    get_historical_pm25(), since the real-time station list (174 stations,
    includes newer "BKPxxxT"-coded BMA stations) and the historical CKAN
    archive (older, purely numeric "NNT" codes only) are NOT the same set;
    without this filter you can pick a real-time-nearest station that has
    zero historical records.
    """
    if stations_df is None:
        stations_df = get_realtime_stations()
    if valid_codes is not None:
        stations_df = stations_df[stations_df["station_code"].isin(valid_codes)]
    d2 = (stations_df["lat"] - lat) ** 2 + (stations_df["lon"] - lon) ** 2
    return stations_df.loc[d2.idxmin()]


def _dataset_year_resources(dataset_id: str) -> dict:
    """{buddhist_era_year: resource_id} for a pcd.gdcatalog.go.th dataset,
    discovered via package_show rather than hardcoded -- so a newly
    published year (or a differently-worded resource name) doesn't
    silently go missing.

    PM10_DATASET_ID publishes TWO resources per year -- the actual
    measurement data ("...(PM10) ปี XXXX") and a separate station-list
    metadata resource ("รายชื่อสถานี...ปี XXXX", fields are just station
    name/install-point, no readings) -- both resource names contain the
    same year number. Verified live 2026-08-14: package_show lists the
    station-list resource AFTER the data resource for every year, so a
    naive "last match wins" dict build silently keeps the metadata
    resource and drops the real data. Skip anything whose name matches the
    station-list pattern so the data resource always wins.
    """
    payload = _fetch_json(f"{CKAN_BASE}/package_show", {"id": dataset_id})
    resources = {}
    for r in payload["result"]["resources"]:
        name = r.get("name", "")
        if "รายชื่อสถานี" in name:
            continue
        match = re.search(r"(25\d\d)", name)
        if match:
            resources[int(match.group(1))] = r["id"]
    return resources


def get_historical_station_codes(year: int | None = None, dataset_id: str = PM25_DATASET_ID) -> set:
    """Distinct station_code values actually present in the historical CKAN
    archive for one calendar year (defaults to the most recent published
    year). Cheap: queries a single day's records rather than the whole
    year. Use this to filter find_nearest_station()'s candidate pool
    before calling get_historical_pm25()/get_historical_pm10() -- pass the
    matching dataset_id, since PM25_DATASET_ID and PM10_DATASET_ID are
    separate CKAN datasets and are NOT guaranteed to share the same
    station_code set."""
    year_resources = _dataset_year_resources(dataset_id)
    if year is None:
        year = max(year_resources) - 543  # most recent published BE year -> CE
    resource_id = year_resources.get(year + 543)
    if resource_id is None:
        return set()
    payload = _fetch_json(
        f"{CKAN_BASE}/datastore_search",
        {"resource_id": resource_id, "limit": 500, "filters": f'{{"observation_date": "{year}-01-01"}}'},
    )
    return {r["station_code"] for r in payload["result"]["records"]}


def _fetch_datastore_all(resource_id: str, extra_params: dict | None = None) -> list[dict]:
    """Page through a full CKAN datastore_search result (10000 rows/page)."""
    records = []
    offset = 0
    while True:
        params = {"resource_id": resource_id, "limit": 10000, "offset": offset}
        if extra_params:
            params.update(extra_params)
        payload = _fetch_json(f"{CKAN_BASE}/datastore_search", params)
        batch = payload["result"]["records"]
        records.extend(batch)
        if len(batch) < 10000:
            break
        offset += 10000
    return records


def _fetch_year_records(resource_id: str, station_code: str) -> list[dict]:
    """One station's daily records from one year's resource, as
    {"observation_date", "measured_value", "data_status"} dicts --
    normalizes over two schemas PM25_DATASET_ID/PM10_DATASET_ID actually use
    (verified live 2026-08-14, see _dataset_year_resources):

    - "long" (observation_date/station_code/measured_value/data_status
      columns), used from 2025 (2568 BE) onward -- filterable server-side.
    - "wide" (Date + one column per station_code, no data_status at all),
      used 2011-2020 for both datasets -- no server-side station filter
      exists for this shape, so every row is paged through and only this
      station's column is kept.

    2021-2023/2024 (varies by dataset) resources are listed by package_show
    but 404 on datastore_search -- not pushed to CKAN's queryable datastore
    on PCD's side. That's surfaced to the caller as an HTTPError (it
    propagates from _fetch_json here), which _get_historical_daily_series
    catches per-year so one missing year doesn't fail the whole pull.
    """
    probe = _fetch_json(f"{CKAN_BASE}/datastore_search", {"resource_id": resource_id, "limit": 1})
    fields = {f["id"] for f in probe["result"]["fields"]}

    if "station_code" in fields:
        records = _fetch_datastore_all(resource_id, {"filters": f'{{"station_code": "{station_code}"}}'})
        return [
            {
                "observation_date": r["observation_date"],
                "measured_value": r["measured_value"],
                "data_status": r.get("data_status"),
            }
            for r in records
        ]

    if "Date" in fields and station_code in fields:
        records = _fetch_datastore_all(resource_id)
        return [
            {"observation_date": r["Date"], "measured_value": r[station_code]}
            for r in records
            if r.get(station_code) not in (None, "", "-")
        ]

    # "Date" present but this station has no column in this year's wide
    # table (station didn't exist yet / wasn't in this dataset that year).
    return []


def _get_historical_daily_series(
    dataset_id: str, series_name: str, station_code: str, start_date: str, end_date: str
) -> pd.Series:
    """Shared CKAN pull for PM25_DATASET_ID/PM10_DATASET_ID, daily 24-hour
    averages -- see _fetch_year_records for the two schemas this normalizes
    over. Years with no queryable datastore resource are silently skipped
    (see _fetch_year_records), so the returned Series may have gaps even
    within [start_date, end_date]."""
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    station_code = station_code.upper()
    year_resources = _dataset_year_resources(dataset_id)

    records = []
    for year in range(start.year, end.year + 1):
        resource_id = year_resources.get(year + 543)
        if resource_id is None:
            continue
        try:
            records.extend(_fetch_year_records(resource_id, station_code))
        except requests.exceptions.HTTPError:
            continue

    if not records:
        return pd.Series(dtype="float64", name=series_name)

    df = pd.DataFrame(records)
    # Wide-format "Date" cells are occasionally a blank/malformed string
    # (verified live 2026-08-14, e.g. a lone space) rather than a real date
    # -- coerce and drop those rather than letting one bad cell crash the
    # whole multi-year pull.
    df["observation_date"] = pd.to_datetime(df["observation_date"], errors="coerce")
    df = df.dropna(subset=["observation_date"])
    df = df[(df["observation_date"] >= start) & (df["observation_date"] <= end)]
    if "data_status" in df.columns:
        # Wide-format rows never had a data_status column at all (NaN here,
        # not "invalid") -- only drop rows explicitly marked invalid by the
        # long-format schema, don't drop everything wide-format contributed.
        df = df[df["data_status"].isna() | (df["data_status"].str.lower() == "valid")]
    df["measured_value"] = pd.to_numeric(df["measured_value"], errors="coerce")
    df = df.dropna(subset=["measured_value"])
    series = df.set_index("observation_date")["measured_value"].sort_index()
    series = series[~series.index.duplicated(keep="last")]
    series.name = series_name
    return series


def get_historical_pm25(station_code: str, start_date: str, end_date: str) -> pd.Series:
    """Daily PM2.5 (ug/m3, 24-hour average) for one Air4Thai station, from
    PCD's open-data CKAN archive. Covers whatever complete calendar years
    are published (2011 onward at time of writing) -- years not yet
    published (e.g. the current, still-in-progress year) are silently
    skipped, so the returned Series may end before `end_date` if it runs
    past the last published year. Use get_realtime_stations() to fill that
    gap with today's reading.
    """
    return _get_historical_daily_series(PM25_DATASET_ID, "pm2_5", station_code, start_date, end_date)


def get_historical_pm10(station_code: str, start_date: str, end_date: str) -> pd.Series:
    """Daily PM10 (ug/m3, 24-hour average) for one Air4Thai station, from
    PCD's open-data CKAN archive (PM10_DATASET_ID -- verified live to share
    PM25_DATASET_ID's structure and 2011-2025 coverage, see the constant's
    comment above). Same "years not yet published are skipped" caveat as
    get_historical_pm25()."""
    return _get_historical_daily_series(PM10_DATASET_ID, "pm10", station_code, start_date, end_date)


# Safety net, not a "Bangkok-only" boundary: verified live that this
# dataset actually covers all 5 of app.py's site presets (Bangkok, Chiang
# Mai, Khon Kaen, Rayong, Hat Yai) despite its "Bangkok Metro" title --
# see the module docstring. This threshold (degrees) just guards against
# a genuinely unsupported custom lat/lon (a remote area with no nearby
# station at all) silently pairing with a station far enough away that
# the "correction" would be meaningless.
MAX_CORRECTION_STATION_DISTANCE_DEG = 1.0


def compute_monthly_pm_correction(lat: float, lon: float, year: int) -> dict | None:
    """Monthly PM2.5 bias-correction factors for one calendar year:
    median(Air4Thai / Open-Meteo CAMS) per month, at the Air4Thai station
    nearest (lat, lon). Multiply Open-Meteo's PM2.5 by
    result[month] to correct it toward the Air4Thai ground-truth level for
    that month.

    Returns None if (lat, lon) has no Air4Thai historical station within
    MAX_CORRECTION_STATION_DISTANCE_DEG, or too little paired data exists
    to compute a meaningful factor -- callers must handle this by falling
    back to uncorrected CAMS data, not by assuming a factor of 1.0.
    """
    from src.weather_client import get_historical_air_quality  # reuse, don't duplicate the Open-Meteo AQ fetch

    hist_codes = get_historical_station_codes(year)
    if not hist_codes:
        return None
    stations = get_realtime_stations()
    nearest = find_nearest_station(lat, lon, stations, valid_codes=hist_codes)
    distance_deg = ((nearest["lat"] - lat) ** 2 + (nearest["lon"] - lon) ** 2) ** 0.5
    if distance_deg > MAX_CORRECTION_STATION_DISTANCE_DEG:
        return None

    air4thai = get_historical_pm25(nearest["station_code"], f"{year}-01-01", f"{year}-12-31")
    cams = get_historical_air_quality(
        nearest["lat"], nearest["lon"], f"{year}-01-01", f"{year}-12-31"
    )["pm2_5"].resample("D").mean()
    air4thai.name, cams.name = "air4thai", "cams"

    paired = pd.concat([air4thai, cams], axis=1, join="inner").dropna()
    paired = paired[paired["cams"] > 0]
    if len(paired) < 30:
        return None

    ratio = paired["air4thai"] / paired["cams"]
    monthly_factor = ratio.groupby(paired.index.month).median()
    return monthly_factor.to_dict()


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    bangkok_lat, bangkok_lon = 13.7563, 100.5018

    print("=== get_realtime_stations ===")
    stations = get_realtime_stations()
    print(f"{len(stations)} stations")
    print(stations.head())

    print("\n=== get_historical_station_codes (2025) ===")
    hist_codes = get_historical_station_codes(2025)
    print(f"{len(hist_codes)} stations with historical records")

    print("\n=== find_nearest_station (restricted to historical-compatible codes) ===")
    nearest = find_nearest_station(bangkok_lat, bangkok_lon, stations, valid_codes=hist_codes)
    print(nearest)

    print("\n=== get_historical_pm25 (2025 full year, nearest station) ===")
    hist = get_historical_pm25(nearest["station_code"], "2025-01-01", "2025-12-31")
    print(f"{len(hist)} days")
    print(hist.describe())
    print(hist.head())
    print(hist.tail())

    print("\n=== compute_monthly_pm_correction (Bangkok, should succeed) ===")
    bkk_factors = compute_monthly_pm_correction(bangkok_lat, bangkok_lon, 2025)
    print(bkk_factors)

    print("\n=== compute_monthly_pm_correction (Chiang Mai -- also covered, despite dataset's Bangkok-sounding title) ===")
    cnx_factors = compute_monthly_pm_correction(18.7883, 98.9853, 2025)
    print(cnx_factors)
