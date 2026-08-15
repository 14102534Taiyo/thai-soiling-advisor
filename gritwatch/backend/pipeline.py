"""Orchestration layer for the GritWatch dashboard backend:
weather_client/air4thai_client/tmd_client -> soiling_pv_model -> economics.

This is a GritWatch-local copy of the same wiring app.py's `run_pipeline`
does (see that file's Streamlit sidebar section) -- kept here instead of in
src/ or app.py so this whole dashboard rebuild stays self-contained under
gritwatch/ and never edits files the existing Streamlit app (or anyone else
working in this repo) owns. It only ever *reads* from src/ -- imports the
existing, calibrated functions in src/soiling_pv_model.py, src/economics.py,
src/weather_client.py, src/air4thai_client.py, src/tmd_client.py, and
src/representative_year.py, and does not modify any of them.

Framework-agnostic on purpose -- no `streamlit` import here.
"""

from datetime import date, timedelta

import pandas as pd

from src.air4thai_client import (
    MAX_CORRECTION_STATION_DISTANCE_DEG,
    compute_monthly_pm_correction,
    find_nearest_station,
    get_historical_station_codes,
    get_realtime_stations,
)
from src.economics import compute_acr_curve, recommend_cleaning
from src.representative_year import (
    compute_monthly_rain_stats,
    fill_aq_with_representative_years,
    fill_weather_with_representative_years,
    select_pm_representative_years,
    select_representative_weather_years,
)
from src.soiling_pv_model import (
    compute_expected_generation,
    compute_expected_generation_hourly,
    compute_soiling_ratio,
)
from src.tmd_client import get_realtime_rain_today, get_rain_corrections_for_site, save_rain_correction
from src.weather_client import (
    get_forecast_air_quality,
    get_forecast_weather,
    get_historical_air_quality,
    get_historical_weather,
)

SITE_PRESETS = {
    "กรุงเทพฯ": (13.7563, 100.5018),
    "เชียงใหม่": (18.7883, 98.9853),
    "ขอนแก่น": (16.4419, 102.8360),
    "ระยอง": (12.6814, 101.2816),
    "หาดใหญ่": (7.0086, 100.4747),
    "กำหนดพิกัดเอง": None,
}

HIST_DAYS = 90
FORECAST_DAYS = 16
AQ_FORECAST_DAYS = 5
REPRESENTATIVE_YEAR_WINDOW = 10
RESET_RATIO_THRESHOLD = 0.999


def _concat_hourly(hist: pd.DataFrame, fcst: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([hist, fcst])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def get_beyond_horizon_fill_params(lat: float, lon: float):
    year_end = date.today().year - 1
    year_start = year_end - REPRESENTATIVE_YEAR_WINDOW + 1
    rain_stats = compute_monthly_rain_stats(lat, lon, year_start=year_start, year_end=year_end)
    representative_weather_years = select_representative_weather_years(rain_stats)
    pm_representative_years = select_pm_representative_years(lat, lon, year_start=year_start, year_end=year_end)
    return representative_weather_years, pm_representative_years


def fetch_pipeline_inputs(lat: float, lon: float, horizon_days: int):
    today = date.today()
    hist_start = (today - timedelta(days=HIST_DAYS)).isoformat()
    hist_end = (today - timedelta(days=1)).isoformat()

    weather_hourly = _concat_hourly(
        get_historical_weather(lat, lon, hist_start, hist_end),
        get_forecast_weather(lat, lon, forecast_days=FORECAST_DAYS),
    )
    aq_hourly = _concat_hourly(
        get_historical_air_quality(lat, lon, hist_start, hist_end),
        get_forecast_air_quality(lat, lon, forecast_days=AQ_FORECAST_DAYS),
    )

    horizon_end = today + timedelta(days=horizon_days)
    weather_forecast_end = weather_hourly.index.max().date()
    aq_forecast_end = aq_hourly.index.max().date()

    if weather_forecast_end < horizon_end or aq_forecast_end < horizon_end:
        representative_weather_years, pm_representative_years = get_beyond_horizon_fill_params(lat, lon)

    if weather_forecast_end < horizon_end:
        gap_start = weather_forecast_end + timedelta(days=1)
        fill_weather = fill_weather_with_representative_years(
            lat, lon, representative_weather_years, gap_start.isoformat(), horizon_end.isoformat()
        )
        weather_hourly = _concat_hourly(weather_hourly, fill_weather)

    if aq_forecast_end < horizon_end:
        gap_start = aq_forecast_end + timedelta(days=1)
        fill_aq = fill_aq_with_representative_years(
            lat, lon, pm_representative_years, gap_start.isoformat(), horizon_end.isoformat()
        )
        aq_hourly = _concat_hourly(aq_hourly, fill_aq)

    return weather_hourly, aq_hourly, weather_forecast_end, aq_forecast_end, horizon_end


def get_pm_correction_factors(lat: float, lon: float):
    reference_year = date.today().year - 1
    return compute_monthly_pm_correction(lat, lon, reference_year)


def get_realtime_pm_today(lat: float, lon: float):
    year_now = date.today().year
    hist_codes = get_historical_station_codes(year_now - 1)
    if not hist_codes:
        return None
    stations = get_realtime_stations()
    nearest = find_nearest_station(lat, lon, stations, valid_codes=hist_codes)
    distance_deg = ((nearest["lat"] - lat) ** 2 + (nearest["lon"] - lon) ** 2) ** 0.5
    if distance_deg > MAX_CORRECTION_STATION_DISTANCE_DEG or nearest["pm2_5"] is None:
        return None
    return float(nearest["pm2_5"])


def get_realtime_rain_status(lat: float, lon: float):
    return get_realtime_rain_today(lat, lon)


def run_pipeline(lat, lon, kwp, dc_ac_ratio, tilt, azimuth, cleaning_threshold_mm,
                  price_per_kwh, cleaning_cost_per_kwp, horizon_days, system_efficiency=1.0,
                  gamma_pdc=-0.0040):
    today = date.today()
    weather_hourly, aq_hourly, weather_fcst_end, aq_fcst_end, horizon_end = fetch_pipeline_inputs(
        lat, lon, horizon_days
    )

    pm_correction = get_pm_correction_factors(lat, lon)
    if pm_correction is not None:
        month_factor = pd.Series(aq_hourly.index.month, index=aq_hourly.index).map(pm_correction).fillna(1.0)
        aq_hourly = aq_hourly.copy()
        aq_hourly["pm2_5"] = aq_hourly["pm2_5"] * month_factor
        aq_hourly["pm10"] = aq_hourly["pm10"] * month_factor

    realtime_pm25_today = get_realtime_pm_today(lat, lon)
    if realtime_pm25_today is not None:
        today_mask = aq_hourly.index.date == today
        aq_hourly = aq_hourly.copy()
        aq_hourly.loc[today_mask, "pm2_5"] = realtime_pm25_today

    weather_hourly = weather_hourly.copy()
    persisted_corrections = get_rain_corrections_for_site(lat, lon)
    for date_str, corrected_mm in persisted_corrections.items():
        corrected_date = date.fromisoformat(date_str)
        weather_hourly.loc[weather_hourly.index.date == corrected_date, "rainfall_mm"] = corrected_mm

    yesterday = today - timedelta(days=1)
    rain_status = get_realtime_rain_status(lat, lon)
    rain_verified_yesterday = rain_status is not None or yesterday.isoformat() in persisted_corrections
    rain_correction_applied = False
    if rain_status is not None and yesterday.isoformat() not in persisted_corrections:
        yesterday_mask = weather_hourly.index.date == yesterday
        yesterday_rain = weather_hourly.loc[yesterday_mask, "rainfall_mm"]
        forecast_peak_hourly_yesterday = yesterday_rain.max() if len(yesterday_rain) else 0.0
        if rain_status["rainfall_mm_today"] < 0.5 and forecast_peak_hourly_yesterday >= cleaning_threshold_mm:
            weather_hourly.loc[yesterday_mask, "rainfall_mm"] = 0.0
            rain_correction_applied = True
            save_rain_correction(lat, lon, yesterday.isoformat(), 0.0)
    elif yesterday.isoformat() in persisted_corrections:
        rain_correction_applied = True

    weather_daily = weather_hourly.resample("D").agg(
        {"rainfall_mm": "sum", "ghi": "mean", "dni": "mean", "dhi": "mean",
         "temp_air": "mean", "wind_speed": "mean"}
    )
    aq_daily = aq_hourly.resample("D").mean()

    soiling_ratio = compute_soiling_ratio(
        weather_hourly, aq_hourly, cleaning_threshold_mm=cleaning_threshold_mm, tilt=tilt,
    )
    expected_generation = compute_expected_generation(
        weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
        system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
    )

    today_ts = pd.Timestamp(today)
    past_resets = soiling_ratio.index[(soiling_ratio.index <= today_ts) & (soiling_ratio >= RESET_RATIO_THRESHOLD)]
    last_clean_date = past_resets.max() if len(past_resets) else soiling_ratio.index.min()
    assumed_reset = len(past_resets) == 0
    if last_clean_date == today_ts:
        last_clean_date_unconfirmed = True
    elif last_clean_date == today_ts - pd.Timedelta(days=1):
        last_clean_date_unconfirmed = not rain_verified_yesterday
    else:
        last_clean_date_unconfirmed = False

    projection_index = soiling_ratio.index[soiling_ratio.index > last_clean_date]
    future_resets = projection_index[soiling_ratio.loc[projection_index] >= RESET_RATIO_THRESHOLD]
    next_natural_reset_date = future_resets.min() if len(future_resets) else None
    days_until_natural = (
        (next_natural_reset_date - last_clean_date).days if next_natural_reset_date is not None else None
    )

    stretch_index = (
        projection_index[projection_index < next_natural_reset_date]
        if next_natural_reset_date is not None
        else projection_index
    )
    sr_stretch = soiling_ratio.loc[stretch_index]
    gen_stretch = expected_generation.reindex(stretch_index)

    avg_daily_loss_until_natural = (
        float(((1 - sr_stretch) * gen_stretch.fillna(0) * price_per_kwh).mean())
        if len(sr_stretch) else None
    )

    acr_curve = None
    result = None
    hits_edge = None
    if len(sr_stretch) >= 2:
        acr_curve = compute_acr_curve(
            sr_stretch, gen_stretch, price_per_kwh, cleaning_cost_per_kwp, kwp, max_days=len(sr_stretch),
        )
        min_T = max(1, (today_ts - last_clean_date).days)
        result = recommend_cleaning(acr_curve, last_clean_date=last_clean_date, min_T=min_T)
        hits_edge = result["optimal_T"] >= len(sr_stretch)

    soiling_today = float(soiling_ratio.reindex([today_ts]).iloc[0]) if today_ts in soiling_ratio.index else None
    pm25_today = float(aq_daily["pm2_5"].reindex([today_ts]).iloc[0]) if today_ts in aq_daily.index else None
    gen_today = float(expected_generation.reindex([today_ts]).iloc[0]) if today_ts in expected_generation.index else None
    rain_today = float(weather_daily["rainfall_mm"].reindex([today_ts]).iloc[0]) if today_ts in weather_daily.index else None
    today_hourly_rain = weather_hourly["rainfall_mm"][weather_hourly.index.date == today_ts.date()]
    rain_today_max_hourly = float(today_hourly_rain.max()) if len(today_hourly_rain) else None
    rain_since_clean = float(weather_daily.loc[last_clean_date:today_ts, "rainfall_mm"].sum())
    days_since_clean = (today_ts - last_clean_date).days

    today_hourly_weather = weather_hourly[weather_hourly.index.date == today_ts.date()]
    if len(today_hourly_weather) and soiling_today is not None:
        expected_power_kw_today = compute_expected_generation_hourly(
            today_hourly_weather, lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
            system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
        )
        actual_power_kw_today = expected_power_kw_today * soiling_today
    else:
        expected_power_kw_today = None
        actual_power_kw_today = None

    loss_period_index = soiling_ratio.index[
        (soiling_ratio.index >= last_clean_date) & (soiling_ratio.index < today_ts)
    ]
    loss_baht_since_clean = float((
        (1 - soiling_ratio.loc[loss_period_index])
        * expected_generation.reindex(loss_period_index).fillna(0)
        * price_per_kwh
    ).sum())
    loss_baht_today = (
        (1 - soiling_today) * gen_today * price_per_kwh
        if soiling_today is not None and gen_today is not None else None
    )

    return {
        "soiling_ratio": soiling_ratio,
        "expected_generation": expected_generation,
        "weather_daily": weather_daily,
        "aq_daily": aq_daily,
        "weather_hourly": weather_hourly,
        "aq_hourly": aq_hourly,
        "last_clean_date": last_clean_date,
        "assumed_reset": assumed_reset,
        "last_clean_date_unconfirmed": last_clean_date_unconfirmed,
        "rain_status": rain_status,
        "rain_verified_yesterday": rain_verified_yesterday,
        "rain_correction_applied": rain_correction_applied,
        "next_natural_reset_date": next_natural_reset_date,
        "days_until_natural": days_until_natural,
        "avg_daily_loss_until_natural": avg_daily_loss_until_natural,
        "sr_stretch": sr_stretch,
        "acr_curve": acr_curve,
        "result": result,
        "hits_edge": hits_edge,
        "weather_forecast_end": weather_fcst_end,
        "aq_forecast_end": aq_fcst_end,
        "pm_correction": pm_correction,
        "realtime_pm25_today": realtime_pm25_today,
        "horizon_end": horizon_end,
        "soiling_today": soiling_today,
        "pm25_today": pm25_today,
        "gen_today": gen_today,
        "rain_today": rain_today,
        "rain_today_max_hourly": rain_today_max_hourly,
        "rain_since_clean": rain_since_clean,
        "days_since_clean": days_since_clean,
        "loss_baht_since_clean": loss_baht_since_clean,
        "loss_baht_today": loss_baht_today,
        "expected_power_kw_today": expected_power_kw_today,
        "actual_power_kw_today": actual_power_kw_today,
    }
