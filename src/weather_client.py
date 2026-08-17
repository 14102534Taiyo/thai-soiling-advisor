import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

HISTORICAL_WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FORECAST_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

WEATHER_HOURLY_VARS = [
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "precipitation",
    "temperature_2m",
    "wind_speed_10m",
]
WEATHER_COLUMN_RENAME = {
    "shortwave_radiation": "ghi",
    "direct_radiation": "dni",
    "diffuse_radiation": "dhi",
    "precipitation": "rainfall_mm",
    "temperature_2m": "temp_air",
    "wind_speed_10m": "wind_speed",
}

AQ_HOURLY_VARS = ["pm2_5", "pm10"]


def _cache_path(endpoint: str, params: dict) -> Path:
    key_params = {k: params[k] for k in sorted(params)}
    digest = hashlib.sha256(json.dumps(key_params, sort_keys=True).encode()).hexdigest()[:24]
    endpoint_name = endpoint.rstrip("/").rsplit("/", 1)[-1]
    return CACHE_DIR / f"{endpoint_name}_{digest}.json"


def _fetch_json(endpoint: str, params: dict, daily_expiry: bool = False) -> dict:
    """daily_expiry=True treats a cache file written on an earlier calendar
    day as stale and refetches. Needed for the forecast endpoints: their
    params (lat/lon/forecast_days) carry no date, so "16 days from now"
    would otherwise hash to the same cache file forever, silently freezing
    the first-ever fetch -- confirmed live 2026-08-16, a Bangkok forecast
    cached 2026-08-08 was still being served 8 days later (today's PSH
    6.2h from the stale forecast vs 3.88h from a real refetch -- nearly 2x
    off). Historical endpoints don't need this: start_date/end_date are
    already part of the cache key, so a stale file there just means
    "already-settled data that hasn't changed," not a freshness bug."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(endpoint, params)
    if cache_file.exists():
        is_stale = daily_expiry and date.fromtimestamp(cache_file.stat().st_mtime) < date.today()
        if not is_stale:
            return json.loads(cache_file.read_text(encoding="utf-8"))

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _hourly_to_dataframe(payload: dict, rename_map: dict | None = None) -> pd.DataFrame:
    hourly = payload.get("hourly", {})
    df = pd.DataFrame(hourly)
    if df.empty:
        columns = list(rename_map.values()) if rename_map else []
        return pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], name="time"))
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    if rename_map:
        df = df.rename(columns=rename_map)
        df = df[list(rename_map.values())]
    return df


def _round_coord(value: float) -> float:
    return round(float(value), 4)


def get_historical_weather(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    params = {
        "latitude": _round_coord(lat),
        "longitude": _round_coord(lon),
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(WEATHER_HOURLY_VARS),
        "timezone": "auto",
    }
    payload = _fetch_json(HISTORICAL_WEATHER_URL, params)
    return _hourly_to_dataframe(payload, WEATHER_COLUMN_RENAME)


def get_forecast_weather(lat: float, lon: float, forecast_days: int = 16) -> pd.DataFrame:
    params = {
        "latitude": _round_coord(lat),
        "longitude": _round_coord(lon),
        "forecast_days": forecast_days,
        "hourly": ",".join(WEATHER_HOURLY_VARS),
        "timezone": "auto",
    }
    payload = _fetch_json(FORECAST_WEATHER_URL, params, daily_expiry=True)
    return _hourly_to_dataframe(payload, WEATHER_COLUMN_RENAME)


def get_historical_air_quality(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    params = {
        "latitude": _round_coord(lat),
        "longitude": _round_coord(lon),
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(AQ_HOURLY_VARS),
        "timezone": "auto",
    }
    payload = _fetch_json(HISTORICAL_AQ_URL, params)
    return _hourly_to_dataframe(payload, {v: v for v in AQ_HOURLY_VARS})


def get_forecast_air_quality(lat: float, lon: float, forecast_days: int = 5) -> pd.DataFrame:
    params = {
        "latitude": _round_coord(lat),
        "longitude": _round_coord(lon),
        "forecast_days": forecast_days,
        "hourly": ",".join(AQ_HOURLY_VARS),
        "timezone": "auto",
    }
    payload = _fetch_json(FORECAST_AQ_URL, params, daily_expiry=True)
    return _hourly_to_dataframe(payload, {v: v for v in AQ_HOURLY_VARS})


def compute_monthly_pm_climatology(lat: float, lon: float, year_start: int, year_end: int) -> dict:
    """Mean PM2.5/PM10 per calendar month from Open-Meteo's own CAMS-based
    historical AQ archive, averaged across whatever years in
    [year_start, year_end] actually have data.

    Real (non-null) coverage only starts ~August 2022 for the CAMS Global
    Forecast product Open-Meteo archives outside Europe (verified live
    2026-08-14 -- see docs/data_source_research.md) -- years before that are
    silently skipped, same as get_historical_pm25's "not yet published"
    handling elsewhere in this project.

    Unlike src/representative_year.py's Air4Thai-based representative-year
    selection, this needs no ground station and is available at any lat/lon
    Open-Meteo covers -- verified live 2026-08-14 for all 5 of app.py's site
    presets. It trades year-to-year ground-truth accuracy (and day-to-day
    variability within a month) for universal coverage: intended as the
    fallback for sites/months where Air4Thai's per-station historical
    coverage isn't deep enough to trust (representative_year.py found this
    is true for 4 of the 5 site presets), while still restoring PM's real
    seasonal shape -- dry season runs ~3x higher than rainy season, see
    soiling_pv_model.py's LITERATURE_CALIBRATED_DEPO_VELOC comment -- that a
    single flat constant across the whole projection would lose entirely.

    Returns {month: {"pm2_5": float | None, "pm10": float | None}}, None
    for a month with no data in range.
    """
    frames = []
    for year in range(year_start, year_end + 1):
        aq = get_historical_air_quality(lat, lon, f"{year}-01-01", f"{year}-12-31")
        if not aq.empty:
            frames.append(aq)

    if not frames:
        return {month: {"pm2_5": None, "pm10": None} for month in range(1, 13)}

    combined = pd.concat(frames)
    monthly = combined.groupby(combined.index.month)[["pm2_5", "pm10"]].mean()
    return {
        month: {
            "pm2_5": float(monthly.loc[month, "pm2_5"]) if month in monthly.index and pd.notna(monthly.loc[month, "pm2_5"]) else None,
            "pm10": float(monthly.loc[month, "pm10"]) if month in monthly.index and pd.notna(monthly.loc[month, "pm10"]) else None,
        }
        for month in range(1, 13)
    }


def _shift_index_by_years(index: pd.DatetimeIndex, years: int) -> pd.DatetimeIndex:
    return index + pd.DateOffset(years=years)


def get_analog_year_weather(
    lat: float, lon: float, target_start: str, target_end: str, years_back: int = 1
) -> pd.DataFrame:
    """Replay a past year's actual weather onto the given future date range,
    for extending soiling/generation projections past the 16-day forecast
    horizon. Reuses get_historical_weather rather than any averaged
    "climatology" product -- an averaged rainfall series would mask
    whether individual days cross soiling_pv_model's cleaning_threshold_mm,
    since HSU's rain-cleaning is a binary threshold, not proportional."""
    src_start = (pd.Timestamp(target_start) - pd.DateOffset(years=years_back)).date().isoformat()
    src_end = (pd.Timestamp(target_end) - pd.DateOffset(years=years_back)).date().isoformat()
    df = get_historical_weather(lat, lon, src_start, src_end).copy()
    df.index = _shift_index_by_years(df.index, years_back)
    return df


def get_analog_year_air_quality(
    lat: float, lon: float, target_start: str, target_end: str, years_back: int = 1
) -> pd.DataFrame:
    """Same analog-year replay as get_analog_year_weather, for PM2.5/PM10.
    Averaging would be defensible here (PM feeds HSU's deposition
    continuously, no threshold), but replay is used for consistency with
    the weather side and to keep one code path."""
    src_start = (pd.Timestamp(target_start) - pd.DateOffset(years=years_back)).date().isoformat()
    src_end = (pd.Timestamp(target_end) - pd.DateOffset(years=years_back)).date().isoformat()
    df = get_historical_air_quality(lat, lon, src_start, src_end).copy()
    df.index = _shift_index_by_years(df.index, years_back)
    return df


if __name__ == "__main__":
    from datetime import date, timedelta

    bangkok_lat, bangkok_lon = 13.7563, 100.5018
    today = date.today()
    hist_start = (today - timedelta(days=10)).isoformat()
    hist_end = (today - timedelta(days=3)).isoformat()

    print("=== get_historical_weather ===")
    historical_weather = get_historical_weather(bangkok_lat, bangkok_lon, hist_start, hist_end)
    print(historical_weather.head())
    print(historical_weather.shape)

    print("\n=== get_forecast_weather ===")
    forecast_weather = get_forecast_weather(bangkok_lat, bangkok_lon, forecast_days=16)
    print(forecast_weather.head())
    print(forecast_weather.shape)

    print("\n=== get_historical_air_quality ===")
    historical_aq = get_historical_air_quality(bangkok_lat, bangkok_lon, hist_start, hist_end)
    print(historical_aq.head())
    print(historical_aq.shape)

    print("\n=== get_forecast_air_quality ===")
    forecast_aq = get_forecast_air_quality(bangkok_lat, bangkok_lon, forecast_days=5)
    print(forecast_aq.head())
    print(forecast_aq.shape)

    analog_target_start = (today + timedelta(days=20)).isoformat()
    analog_target_end = (today + timedelta(days=27)).isoformat()

    print("\n=== get_analog_year_weather ===")
    analog_weather = get_analog_year_weather(bangkok_lat, bangkok_lon, analog_target_start, analog_target_end)
    print(analog_weather.head())
    print(analog_weather.shape)
    print("index spans (should land on the requested future dates):", analog_weather.index.min(), analog_weather.index.max())

    print("\n=== get_analog_year_air_quality ===")
    analog_aq = get_analog_year_air_quality(bangkok_lat, bangkok_lon, analog_target_start, analog_target_end)
    print(analog_aq.head())
    print(analog_aq.shape)
    print("index spans (should land on the requested future dates):", analog_aq.index.min(), analog_aq.index.max())
