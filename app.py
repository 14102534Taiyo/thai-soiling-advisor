"""Streamlit dashboard: economically optimal PV cleaning date advisor for
Thai sites, built on the pipeline validated in main_demo.py. See README.md
for the model's known limitations (binary rain-cleaning, uncalibrated
deposition velocity, CAMS Global PM resolution).

UI text is Thai and avoids jargon (ACR, HSU, T*, deposition_velocity) in the
main "ภาพรวม" tab -- those terms only appear in the "รายละเอียดเชิงเทคนิค"
tab, for a report/presentation audience rather than a developer audience."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

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

st.set_page_config(page_title="ระบบแนะนำวันล้างแผงโซลาร์", layout="wide")

# Visual theme layered on top of .streamlit/config.toml's palette -- targets
# Streamlit's internal data-testid hooks, which shift between versions, so
# this is written to degrade gracefully (unmatched selectors just leave the
# default Streamlit look) rather than being load-bearing for functionality.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Martian+Mono:wght@500;700&display=swap');

    [data-testid="stMetricValue"] {
        font-family: "Martian Mono", "Sarabun", monospace;
        font-variant-numeric: tabular-nums;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) {
        border-radius: 14px !important;
        box-shadow: 0 1px 2px rgba(30,26,16,.06), 0 8px 24px -12px rgba(30,26,16,.14);
    }
    section[data-testid="stSidebar"] {
        border-right: 1px solid #dbe0d2;
    }
    section[data-testid="stSidebar"] h2 {
        font-size: 0.72rem;
        letter-spacing: .06em;
        text-transform: uppercase;
        color: #5c6353;
        margin-top: 1.4rem;
        margin-bottom: 0.4rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Validated palette (see the dataviz skill) -- categorical hues assigned by
# role, never cycled: copper is the headline series, teal is the second
# series/action color, near-black is a derived/aggregate total, the rest
# are muted context/chrome. Matches the GritWatch design exploration's
# dust(copper)/rain(teal) duality -- see .streamlit/config.toml for the
# same palette applied to widget chrome.
ACCENT = "#a85f22"
ACCENT2 = "#237f78"
DARK_TOTAL = "#1b2118"
GRAY_MUTED = "#8b9482"
GRAY_BASELINE = "#c7cbba"
GRIDLINE = "#dbe0d2"
TEXT_SECONDARY = "#5c6353"
CRITICAL = "#b03d2a"  # reserved for loss/waiting-cost series, distinct from the two headline hues

CHART_LAYOUT_DEFAULTS = dict(
    plot_bgcolor="#fbfbf7",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(family="'Sarabun', system-ui, -apple-system, 'Segoe UI', sans-serif", color=TEXT_SECONDARY),
    margin=dict(t=30, b=30, l=10, r=10),
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
REPRESENTATIVE_YEAR_WINDOW = 10  # years of history behind the weather-TMY + PM representative-year fill
RESET_RATIO_THRESHOLD = 0.999


def _concat_hourly(hist: pd.DataFrame, fcst: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([hist, fcst])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


@st.cache_data(ttl=86400, show_spinner=False)
def get_beyond_horizon_fill_params(lat: float, lon: float):
    """Representative source years per calendar month -- one mapping for
    weather, one for PM -- for filling weather_hourly/aq_hourly beyond
    Open-Meteo's real forecast horizon (16 days weather, ~5 days air
    quality). Both fills REPLAY real historical hours from a chosen source
    year (shifted onto the target dates), not an average -- so day-to-day
    variation within a month is preserved, not flattened.

    Replaces the single-arbitrary-"last year" analog replay this project
    started with: weather is replayed from a real source year PER MONTH
    (chosen for that month's rain-threshold-hours typicality across
    REPRESENTATIVE_YEAR_WINDOW years -- see
    src/representative_year.py:select_representative_weather_years), and PM
    is replayed the same way but restricted to years where Open-Meteo's
    CAMS archive actually has real PM coverage (~2022 onward -- see
    select_pm_representative_years), rather than a flat monthly average
    (an earlier version of this did exactly that -- see
    src/representative_year.py's module docstring for why it was
    superseded: a flat average collapses real day-to-day PM variation to a
    constant, visible as an artificial staircase in any chart plotted
    across the fill boundary).

    A per-site, per-station Air4Thai/PCD-based version of the PM replay
    was tried first and verified live to work well for Bangkok -- but
    Air4Thai's per-station historical depth turned out to only support it
    at Bangkok among this app's 5 site presets (the others' nearest
    stations only have real PM2.5 back to 2025), and the underlying PCD
    catalog itself has real gaps/format inconsistencies (see
    src/representative_year.py's module docstring and
    src/air4thai_client.py's _fetch_year_records). Open-Meteo's own CAMS
    archive (both weather and PM) is available everywhere this app's sites
    are, so this is the one path used for all 5 presets rather than
    branching per-site.

    Cached a full day: this pulls REPRESENTATIVE_YEAR_WINDOW years of
    weather data, expensive, and doesn't change within a day.
    """
    year_end = date.today().year - 1
    year_start = year_end - REPRESENTATIVE_YEAR_WINDOW + 1
    rain_stats = compute_monthly_rain_stats(lat, lon, year_start=year_start, year_end=year_end)
    representative_weather_years = select_representative_weather_years(rain_stats)
    pm_representative_years = select_pm_representative_years(lat, lon, year_start=year_start, year_end=year_end)
    return representative_weather_years, pm_representative_years


@st.cache_data(ttl=3600, show_spinner=False)
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


@st.cache_data(ttl=86400, show_spinner=False)
def get_pm_correction_factors(lat: float, lon: float):
    """Monthly Air4Thai/CAMS correction factors for the most recent complete
    year, or None if no Air4Thai historical station is close enough (see
    air4thai_client.MAX_CORRECTION_STATION_DISTANCE_DEG). Cached for a full
    day since it pulls a whole year of two data sources -- expensive, and
    doesn't change within a day."""
    reference_year = date.today().year - 1
    return compute_monthly_pm_correction(lat, lon, reference_year)


@st.cache_data(ttl=3600, show_spinner=False)
def get_realtime_pm_today(lat: float, lon: float):
    """Today's ACTUAL PM2.5 (24h rolling avg) from the nearest Air4Thai
    station, or None if none is close enough. This is real, not a
    CAMS-derived estimate -- correcting CAMS's forecast branch with a
    reanalysis-derived monthly factor (get_pm_correction_factors) still
    understates today's true level (CAMS forecast has a bigger, different
    bias than CAMS reanalysis), so today specifically should use this
    instead of the corrected-CAMS estimate wherever it's available."""
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


@st.cache_data(ttl=3600, show_spinner=False)
def get_realtime_rain_status(lat: float, lon: float):
    """Today's rainfall-so-far (mm) from the nearest TMD station, or None
    if none is close enough. TMD reports at synoptic hours (01,04,07,10,
    13,16,19,22) as a cumulative total since local midnight -- NOT an
    hourly breakdown, so this can only sanity-check whether meaningful
    rain has happened at all today, not confirm/deny a specific hour."""
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
        # Some months can be missing from pm_correction if the nearest
        # Air4Thai station had a data gap that calendar month in the
        # reference year -- map() leaves those hours NaN, which would
        # otherwise propagate into pvlib.soiling.hsu. Fall back to no
        # correction (factor 1.0) for a missing month rather than NaN.
        month_factor = pd.Series(aq_hourly.index.month, index=aq_hourly.index).map(pm_correction).fillna(1.0)
        aq_hourly = aq_hourly.copy()
        aq_hourly["pm2_5"] = aq_hourly["pm2_5"] * month_factor
        aq_hourly["pm10"] = aq_hourly["pm10"] * month_factor

    # Today specifically: use Air4Thai's real-time reading directly rather
    # than the monthly-corrected CAMS estimate. The correction factor above
    # is derived from CAMS *reanalysis* vs Air4Thai (accurate historical
    # data), but "today" comes from CAMS *forecast*, which has its own,
    # typically larger, bias -- the monthly factor alone still understates
    # today's true PM. Real data beats a correction factor when we have it.
    realtime_pm25_today = get_realtime_pm_today(lat, lon)
    if realtime_pm25_today is not None:
        today_mask = aq_hourly.index.date == today
        aq_hourly = aq_hourly.copy()
        aq_hourly.loc[today_mask, "pm2_5"] = realtime_pm25_today

    # Rain sanity-check against TMD's real weather stations (WeatherToday
    # endpoint). This publishes ONCE per calendar day, always timestamped
    # 07:00, reporting the trailing 24 hours as of that observation --
    # confirmed empirically (repeated fetches at different times of day
    # returned the same "07:00" DateTime; it does not update through the
    # day like the separate Weather3Hours endpoint does). That 24h window
    # (~07:00 yesterday to 07:00 today) covers essentially all of
    # YESTERDAY, not today -- so this can only verify/correct YESTERDAY's
    # reset, freshly every morning, not today's (today's forecast-driven
    # determination can't be confirmed until tomorrow's 07:00 report).
    # Only corrects the false-positive direction (forecast said rain,
    # TMD's confirmed total says it barely rained) -- the opposite
    # (forecast missed real rain) isn't corrected here, since TMD gives a
    # daily total, not which hour it fell in. See
    # docs/data_source_research.md for the full investigation.
    #
    # IMPORTANT: weather_hourly is rebuilt from scratch from Open-Meteo on
    # every run, so an in-memory-only correction would be silently lost the
    # day after it's found (once the corrected date is more than 1 day old,
    # this block would no longer be looking at it). Persist confirmed
    # corrections to a local log (tmd_client.save_rain_correction) and
    # re-apply ALL of them for this site on every run, not just yesterday's.
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
    # "Today" always comes from the FORECAST branch (see fetch_pipeline_inputs:
    # hist_end = yesterday, forecast covers today onward), while everything
    # before today already came from Open-Meteo's historical archive
    # (ERA5-family reanalysis, blends real station/radar/satellite obs --
    # more reliable than a forward rain forecast). If today itself triggered
    # the most recent reset, that reset is still PROVISIONAL: no data source
    # can confirm a same-day reset yet (TMD's WeatherToday only reports
    # yesterday's closed 24h total every morning at 07:00 -- see the rain
    # sanity-check above) -- tomorrow, once today rolls into the historical
    # window AND tomorrow's TMD report covers it, this gets confirmed or
    # retracted. A reset dated yesterday is confirmed once rain_verified_
    # yesterday is True (TMD's fresh 07:00 report already checked it above);
    # anything older than that was already covered by a previous day's
    # archive-rollover + TMD check. This matters because last_clean_date
    # propagates into every downstream SR value until the next reset.
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

    # Average daily energy-loss cost over the wait-for-rain window, with NO
    # cleaning-cost term -- unlike acr_curve's "clean_cost_per_day" (which
    # assumes a PAID manual wash), letting rain do the cleaning costs
    # nothing, so this is just the plain average of the loss side alone.
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
        # Floor the search at "today" -- the ACR curve's unconstrained
        # minimum can sit at a T that's already behind us (see
        # recommend_cleaning's docstring); without this floor the
        # recommendation can come back dated in the past.
        min_T = max(1, (today_ts - last_clean_date).days)
        result = recommend_cleaning(acr_curve, last_clean_date=last_clean_date, min_T=min_T)
        hits_edge = result["optimal_T"] >= len(sr_stretch)

    # "Current status" monitor values -- today's reading of each underlying
    # input/output, not just the final recommendation.
    soiling_today = float(soiling_ratio.reindex([today_ts]).iloc[0]) if today_ts in soiling_ratio.index else None
    pm25_today = float(aq_daily["pm2_5"].reindex([today_ts]).iloc[0]) if today_ts in aq_daily.index else None
    gen_today = float(expected_generation.reindex([today_ts]).iloc[0]) if today_ts in expected_generation.index else None
    rain_today = float(weather_daily["rainfall_mm"].reindex([today_ts]).iloc[0]) if today_ts in weather_daily.index else None
    # cleaning_threshold_mm is now an HOURLY threshold (see soiling_pv_model.py) --
    # compare against today's peak hourly rain, not the daily total, so the
    # KPI caption stays consistent with what actually triggers a reset.
    today_hourly_rain = weather_hourly["rainfall_mm"][weather_hourly.index.date == today_ts.date()]
    rain_today_max_hourly = float(today_hourly_rain.max()) if len(today_hourly_rain) else None
    rain_since_clean = float(weather_daily.loc[last_clean_date:today_ts, "rainfall_mm"].sum())
    days_since_clean = (today_ts - last_clean_date).days

    # Hourly power-vs-irradiance profile for today only (not the whole
    # horizon -- this is a real re-run of the PVWatts chain at hourly
    # resolution, so it's kept cheap by restricting the input to today's
    # ~24 rows rather than recomputing it for the full projection window).
    # "actual" applies soiling_today (daily-resolution, see
    # compute_soiling_ratio) uniformly across today's hours -- the same
    # daily-value-applied-to-today convention already used by
    # loss_baht_today below, not a claim that soiling varies within the day.
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

    # Cumulative money already lost to soiling this cycle (last clean -> up to
    # YESTERDAY, not today and not projected into the future) -- turns the
    # abstract soiling-ratio % into a concrete baht figure that backs up the
    # cleaning recommendation. Today is kept separate (loss_baht_today) since
    # it's still an in-progress/forecast day, not a settled historical one.
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
        "weather_hourly": weather_hourly,
        "expected_power_kw_today": expected_power_kw_today,
        "actual_power_kw_today": actual_power_kw_today,
    }


# --- Sidebar ---
st.sidebar.header("ทำเลที่ตั้ง")
site_name = st.sidebar.selectbox("เลือกจังหวัด", list(SITE_PRESETS.keys()))
if SITE_PRESETS[site_name] is not None:
    lat, lon = SITE_PRESETS[site_name]
else:
    lat = st.sidebar.number_input("ละติจูด (Latitude)", value=13.7563, format="%.4f")
    lon = st.sidebar.number_input("ลองจิจูด (Longitude)", value=100.5018, format="%.4f")

st.sidebar.header("สเปกระบบโซลาร์")
kwp = st.sidebar.number_input("ขนาดระบบ (kWp)", min_value=1.0, value=100.0, step=1.0)
dc_ac_ratio = st.sidebar.number_input("อัตราส่วน DC/AC", min_value=1.0, value=1.2, step=0.05)
tilt = st.sidebar.slider(
    "มุมเอียงแผง (องศา)", 0, 30, 10,
    help="ตรวจสอบด้วยข้อมูลจริงทั้งปี 2025 แล้ว: มุมที่ให้พลังงานรวมทั้งปีสูงสุดสำหรับกรุงเทพฯ อยู่ที่ ~13-15° "
         "(ตรงกับมาตรฐานทั่วไปที่แนะนำ 10-15°) — แต่ตัวเลขพลังงานที่แดชบอร์ดนี้แสดง (กราฟ/สถานะปัจจุบัน) สะท้อนแค่ช่วง "
         "\"มองไปข้างหน้ากี่วัน\" ที่เลือกไว้ด้านล่างเท่านั้น ถ้า horizon สั้นและไม่ครอบคลุมหน้าหนาว (ธ.ค.-ก.พ.) ผลจะเอียงไปทาง "
         "\"มุมต่ำดีกว่า\" ทั้งที่ไม่ใช่ค่าที่เหมาะกับการติดตั้งจริงทั้งปี — ถ้าจะทดสอบความไวต่อมุมเอียงให้แม่น ควรตั้ง horizon ≥ 365 วัน",
)
azimuth = st.sidebar.slider("ทิศทางแผง (องศา, 180=ทิศใต้)", 0, 359, 180)
system_efficiency_pct = st.sidebar.slider(
    "ประสิทธิภาพระบบโดยรวม (%)", 70, 100, 88,
    help="การสูญเสียอื่น ๆ นอกเหนือจากความสกปรก เช่น สายไฟ/ขั้วต่อ, mismatch ระหว่างแผง, เงาบัง, "
         "การเสื่อมสภาพตามอายุ, downtime ของระบบ — ไม่รวมความสกปรกจากฝุ่น/ฝุ่นเกาะ เพราะโมเดลนี้คำนวณผลของความสกปรกแยกต่างหากแล้ว "
         "(จากฝุ่น+ฝนจริง ไม่ใช่ค่าคงที่) ใส่ตรงนี้ซ้ำจะนับสองรอบ ค่า default 88% อิงจากค่าเริ่มต้นของ NREL PVWatts "
         "(System Losses ~14% หักส่วนความสกปรก ~2% ออกแล้ว)",
)
system_efficiency = system_efficiency_pct / 100.0
temp_coefficient_pct = st.sidebar.slider(
    "ค่าสัมประสิทธิ์อุณหภูมิ (%/°C)", -0.50, -0.20, -0.40, step=0.01,
    help="พลังงานที่ลดลงต่อทุก 1°C ที่เซลล์ร้อนเกิน 25°C (ค่ามาตรฐาน STC) — เป็นสเปกของแผงแต่ละยี่ห้อ "
         "แผงพรีเมียมบางรุ่นลงถึง -0.29%/°C (ทนความร้อนดีกว่า) บางรุ่นสูงถึง -0.45%/°C "
         "ค่า default -0.40%/°C เป็นค่าเฉลี่ยทั่วไปของแผงซิลิคอน — มีผลชัดในภูมิอากาศร้อนแบบไทยที่เซลล์ร้อนเกิน 25°C ได้ 5-10°C",
)
gamma_pdc = temp_coefficient_pct / 100.0

st.sidebar.header("เงื่อนไขการล้างแผงจากฝน")
cleaning_threshold_mm = st.sidebar.slider(
    "ปริมาณฝนขั้นต่ำที่ล้างแผงสะอาดได้ (มม./ชั่วโมง)", 0.5, 10.0, 1.0, step=0.5,
    help="เช็คเป็นรายชั่วโมง (ไม่ใช่รายวัน) — ถ้าชั่วโมงไหนฝนถึงค่านี้ ถือว่าล้างแผงสะอาดตั้งแต่ชั่วโมงนั้น "
         "ฝนที่กระจายเบา ๆ ทั้งวันแต่ไม่มีชั่วโมงไหนถึงค่านี้เลยจะไม่ถูกนับว่าล้างแผง แม้ผลรวมทั้งวันจะเยอะก็ตาม "
         "(ค่า default 1.0 มม./ชม. อ้างอิงจากงานวิจัย Norde Santos et al. 2024, Solar RRL — เป็นแบบทั้งหมดหรือไม่เลย ไม่มีล้างบางส่วน)",
)

st.sidebar.header("ตัวเลขเศรษฐศาสตร์ (ค่าเริ่มต้น รอตัวเลขจริง)")
price_per_kwh = st.sidebar.number_input("ค่าไฟฟ้า (บาท/kWh)", min_value=0.1, value=4.0, step=0.1)
cleaning_cost_per_kwp = st.sidebar.number_input("ค่าจ้างล้างแผง (บาท/kWp ต่อครั้ง)", min_value=0.1, value=15.0, step=0.5)

st.sidebar.header("ระยะเวลาพยากรณ์")
horizon_days = st.sidebar.slider(
    "มองไปข้างหน้ากี่วัน", 30, 400, 90, step=15,
    help="ใช้ค่าเริ่มต้น 90 วันพอสำหรับการตัดสินใจเรื่องล้างแผง แต่ถ้าต้องการทดสอบว่ามุมเอียงแผงไหนดีที่สุด "
         "(ครอบคลุมทั้งฤดูร้อนและฤดูหนาว) ให้ปรับเป็น 365 วันขึ้นไป",
)

st.title("ระบบแนะนำวันล้างแผงโซลาร์เซลล์")
st.caption(
    "ใช้แบบจำลองทางฟิสิกส์สำเร็จรูป (pvlib: HSU + PVWatts) ไม่ได้เทรนโมเดลเอง "
    "ข้อมูลอากาศจาก Open-Meteo, ข้อมูลฝุ่นปรับเทียบกับสถานี Air4Thai จริงเมื่อมีสถานีใกล้พอ "
    "(ดูสถานะด้านล่าง และรายละเอียดในแท็บ \"เกี่ยวกับโมเดล\")"
)

with st.spinner("กำลังดึงข้อมูลอากาศ/ฝุ่น และคำนวณแบบจำลอง..."):
    data = run_pipeline(
        lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
        cleaning_threshold_mm, price_per_kwh, cleaning_cost_per_kwp, horizon_days,
        system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
    )

result = data["result"]
next_reset = data["next_natural_reset_date"]
days_until = data["days_until_natural"]
today_ts = pd.Timestamp(date.today())

tab_overview, tab_econ, tab_about = st.tabs(["ภาพรวม", "รายละเอียดเศรษฐศาสตร์", "เกี่ยวกับโมเดล"])

# ======================= TAB 1: ภาพรวม =======================
with tab_overview:
    with st.container(border=True):
        st.subheader("สถานะปัจจุบัน")
        current_pm_factor = data["pm_correction"].get(today_ts.month) if data["pm_correction"] else None

        if data["realtime_pm25_today"] is not None:
            pm_status_short = "✅ ฝุ่นวันนี้ใช้ค่าจริงจาก Air4Thai"
        elif data["pm_correction"] is not None:
            pm_status_short = "✅ ฝุ่นปรับเทียบกับ Air4Thai แล้ว"
        else:
            pm_status_short = "⚠️ ไม่มีสถานี Air4Thai ใกล้พอ ใช้ CAMS ดิบ"
        if data["rain_correction_applied"]:
            rain_status_short = "🔧 ฝนเมื่อวานแก้ไขแล้วด้วยสถานี TMD จริง"
        elif data["rain_verified_yesterday"]:
            rain_status_short = "✅ ฝนเมื่อวานยืนยันกับสถานี TMD แล้ว"
        else:
            rain_status_short = "⚠️ ไม่มีสถานี TMD ใกล้พอตรวจสอบฝน"
        st.caption(f"{pm_status_short} · {rain_status_short}")

        m1, m2, m3 = st.columns(3)
        m1.metric("อัตราสูญเสียจากความสกปรก (พยากรณ์)",
                  f"{(1 - data['soiling_today'])*100:.1f}%" if data["soiling_today"] is not None else "—")
        pm25_label = "ฝุ่น PM2.5 วันนี้ (ค่าจริง)" if data["realtime_pm25_today"] is not None else "ฝุ่น PM2.5 วันนี้ (พยากรณ์)"
        m2.metric(pm25_label, f"{data['pm25_today']:.0f} µg/m³" if data["pm25_today"] is not None else "—")
        m3.metric("วันหลังล้างล่าสุด", f"{data['days_since_clean']} วัน")
        m4, m5 = st.columns(2)
        m4.metric("ปริมาณฝนคาดการณ์วันนี้ (รวมทั้งวัน)",
                  f"{data['rain_today']:.1f} มม." if data["rain_today"] is not None else "—")
        if data["rain_today_max_hourly"] is not None:
            crosses = data["rain_today_max_hourly"] >= cleaning_threshold_mm
            m4.caption(f"{'≥' if crosses else '<'} เกณฑ์ {cleaning_threshold_mm:.1f} มม./ชม. → "
                       f"{'มีฝนล้างแผงวันนี้' if crosses else 'ไม่มีฝนล้างแผงวันนี้'}")
        m5.metric("พลังงานที่คาดว่าผลิตวันนี้ (ถ้าแผงสะอาด, พยากรณ์)",
                  f"{data['gen_today']:.0f} kWh" if data["gen_today"] is not None else "—")
        if data["soiling_today"] is not None:
            m5.caption(f"ยังไม่หักความสกปรก · พลังงานจริง ≈ ×{data['soiling_today']:.3f}")

        with st.expander("รายละเอียดการปรับเทียบข้อมูล"):
            if data["realtime_pm25_today"] is not None:
                st.markdown(
                    f"**ฝุ่น**: ใช้ค่าจริงจากสถานี Air4Thai โดยตรง ({data['realtime_pm25_today']:.1f} µg/m³ ณ ตอนนี้) "
                    f"ไม่ใช่ค่าประมาณจาก CAMS — ส่วนวันข้างหน้ายังเป็นค่าพยากรณ์ที่ปรับเทียบด้วยตัวคูณรายเดือน "
                    f"({f'×{current_pm_factor:.2f}' if current_pm_factor is not None else 'ไม่มีตัวคูณสำหรับเดือนนี้'})"
                )
            elif data["pm_correction"] is not None:
                st.markdown(
                    f"**ฝุ่น**: ปรับเทียบกับสถานี Air4Thai จริงแล้ว "
                    f"({f'ตัวคูณเดือนนี้ ×{current_pm_factor:.2f}' if current_pm_factor is not None else 'ไม่มีตัวคูณสำหรับเดือนนี้'} "
                    "เทียบกับ CAMS Global ดิบ) — ค่าของ \"วันนี้\" ยังเป็นค่าพยากรณ์ ไม่ใช่ค่าที่วัดจริง (ข้อมูลจริงยืนยันแล้วมีถึงแค่เมื่อวาน)"
                )
            else:
                st.markdown(
                    "**ฝุ่น**: ไม่มีสถานี Air4Thai ใกล้พิกัดนี้พอจะปรับเทียบได้ — ใช้ค่า CAMS Global ดิบ (อาจคลาดเคลื่อนจากความจริง)"
                )
            if data["rain_status"] is not None:
                rs = data["rain_status"]
                if data["rain_correction_applied"]:
                    st.markdown(
                        f"**ฝน**: สถานี TMD จริง ({rs['station_name']}, ห่าง {rs['distance_deg']:.2f}°) "
                        f"วัดฝนเมื่อวานได้แค่ {rs['rainfall_mm_today']:.1f} มม. (รายงาน 07:00 น. วันนี้) "
                        "ต่ำกว่าที่พยากรณ์ไว้มาก — ยกเลิกการรีเซตของเมื่อวานที่พยากรณ์ผิดให้อัตโนมัติ TMD รายงานวันละครั้งตอน 07:00 "
                        "ครอบคลุมฝน 24 ชม.ที่ผ่านมา (≈เมื่อวานทั้งวัน) จึงยืนยัน/แก้ไขได้แค่ของเมื่อวาน ส่วนวันนี้ต้องรอพรุ่งนี้เช้า"
                    )
                else:
                    st.markdown(
                        f"**ฝน**: สถานี TMD จริง ({rs['station_name']}, ห่าง {rs['distance_deg']:.2f}°) "
                        f"วัดฝนเมื่อวานได้ {rs['rainfall_mm_today']:.1f} มม. (รายงาน 07:00 น. วันนี้) ตรงกับพยากรณ์ "
                        "— ส่วนฝนวันนี้ยังเป็นค่าพยากรณ์ รอยืนยันพรุ่งนี้เช้า"
                    )
            else:
                st.markdown("**ฝน**: ไม่มีสถานี TMD ใกล้พิกัดนี้พอจะตรวจสอบได้")
            if data["soiling_today"] is not None:
                st.markdown(
                    f"**พลังงาน**: {data['gen_today']:.0f} kWh ยังไม่หักอัตราสูญเสียจากความสกปรก — พลังงานจริงที่คาดว่าจะได้ "
                    f"≈ ค่านี้ × {data['soiling_today']:.3f} ค่านี้เฉพาะวันนี้ ไม่ใช่ค่าเฉลี่ยทั้งปี ความไวต่อมุมเอียงแผงขึ้นกับ"
                    "ฤดูกาลของวันนี้เป็นหลัก (ดูปุ่ม ⓘ ข้าง \"มุมเอียงแผง\" ในแถบด้านซ้าย)"
                )

    with st.container(border=True):
        st.subheader("คำแนะนำ")
        loss_c1, loss_c2 = st.columns(2)
        loss_c1.metric(
            "มูลค่าไฟที่เสียไปแล้ว (ก่อนวันนี้)",
            f"{data['loss_baht_since_clean']:.0f} บาท",
        )
        loss_c2.metric(
            "มูลค่าไฟที่เสียวันนี้ (พยากรณ์)",
            f"{data['loss_baht_today']:.0f} บาท" if data["loss_baht_today"] is not None else "—",
        )
        st.caption("ซ้าย: สะสมก่อนวันนี้ ไม่รวมค่าล้างแผง · ขวา: เฉพาะวันนี้ ยังเป็นค่าพยากรณ์")

        avg_loss = data["avg_daily_loss_until_natural"]
        total_loss_until_rain = (
            avg_loss * days_until if avg_loss is not None and days_until is not None else None
        )
        avg_loss_note = (
            f" (สูญเสียค่าไฟเฉลี่ย ~{avg_loss:.0f} บาท/วัน จนกว่าฝนจะมา "
            f"รวมทั้งหมด ~{total_loss_until_rain:.0f} บาท — ไม่มีค่าจ้างล้างเพราะฝนล้างให้ฟรี)"
            if total_loss_until_rain is not None else ""
        )
        if result is None:
            if next_reset is not None:
                st.success(
                    f"**ยังไม่ต้องล้างแผง** คาดว่าฝนจะตกมากพอที่จะล้างแผงให้เองใน **อีก {days_until} วัน** "
                    f"(วันที่ {next_reset.date()}) — ยังเร็วเกินไปที่จะสกปรกจนคุ้มค่าล้าง{avg_loss_note}"
                )
            else:
                st.warning("ข้อมูลหลังล้างแผงยังน้อยเกินไปที่จะคำนวณ ลองเช็คใหม่พรุ่งนี้")
        elif next_reset is not None and data["hits_edge"]:
            st.success(
                f"**ยังไม่ต้องล้างแผง** คาดว่าฝนจะมาล้างแผงให้เองใน **อีก {days_until} วัน** "
                f"(วันที่ {next_reset.date()}) — รอฝนคุ้มกว่าจ้างล้างเอง{avg_loss_note}"
            )
        else:
            rec_date = result["recommended_date"]
            band_low_date, band_high_date = result["band_low_date"], result["band_high_date"]
            total_cost_until_clean = result["acr_at_optimal"] * result["optimal_T"]
            c1, c2, c3, c4 = st.columns(4)
            if band_low_date == band_high_date:
                c1.metric("วันที่แนะนำให้ล้างแผง", str(rec_date))
            else:
                c1.metric("ช่วงวันที่แนะนำให้ล้างแผง", f"{band_low_date} – {band_high_date}")
                c1.caption(f"ล้างวันไหนในช่วงนี้ก็ได้ ต้นทุนต่างกันไม่ถึง 5% (วันที่คุ้มที่สุด: {rec_date})")
            c2.metric("นับจากวันล้างล่าสุด", f"{result['optimal_T']} วัน")
            c3.metric("ประหยัดได้เทียบกับล้างทุกวัน", f'{result["savings_vs_daily_clean"]:.0f} บาท/วัน')
            c4.metric("ค่าใช้จ่ายรวมจนถึงวันล้าง", f"{total_cost_until_clean:.0f} บาท")
            st.caption("ค่าใช้จ่ายรวม = มูลค่าไฟที่จะเสียไปสะสม + ค่าจ้างล้างแผง")
            if next_reset is not None:
                st.info(f"ล้างแผงเองวันที่ {result['optimal_T']} คุ้มกว่ารอฝนที่คาดว่าจะมาวันที่ {days_until} "
                        f"({next_reset.date()})")
            elif data["hits_edge"]:
                st.info(
                    "วันแนะนำอยู่ที่ขอบเขตของช่วงเวลาที่มองเห็นได้ และยังไม่พบฝนที่จะมาล้างแผงเองในช่วงนี้ "
                    "จุดที่คุ้มค่าจริงอาจไกลกว่านี้ — ลองขยาย \"มองไปข้างหน้ากี่วัน\" ในแถบด้านซ้าย"
                )

        st.caption(
            f"ล้างแผงล่าสุด: {data['last_clean_date'].date()}"
            f"{' (สมมติ)' if data['assumed_reset'] else ''}"
            f"{' — ยังไม่ยืนยันด้วยข้อมูลจริง' if data['last_clean_date_unconfirmed'] else ''} · "
            f"ข้อมูลจริงถึง {data['weather_forecast_end']} (ที่เหลือของ {horizon_days} วัน ใช้ข้อมูลปีก่อนแทน)"
        )
        if data["last_clean_date_unconfirmed"]:
            with st.expander("ทำไมการล้างแผงล่าสุดยังไม่ยืนยัน"):
                if data["last_clean_date"] == today_ts:
                    st.markdown(
                        "ระบบเห็นว่าฝนวันนี้ (จากพยากรณ์) แรงพอจะล้างแผง แต่ค่านี้มาจาก**พยากรณ์ฝน** ยังไม่ใช่ข้อมูลจริงที่วัดแล้ว "
                        "พรุ่งนี้เช้า 07:00 น. เมื่อสถานี TMD รายงานยอดฝน 24 ชม.ที่ครอบคลุมวันนี้ ถ้าฝนจริงตกน้อยกว่าที่พยากรณ์ไว้ "
                        "การล้างวันนี้จะถูกยกเลิกอัตโนมัติ และวันที่ล้างล่าสุดจะขยับเป็นวันก่อนหน้าแทน (กระทบความสะอาดแผงที่คำนวณของทุกวันถัดจากนี้)"
                    )
                else:
                    st.markdown(
                        "ไม่มีสถานี TMD ใกล้พิกัดนี้พอจะตรวจสอบฝนเมื่อวานได้ ค่านี้จะยืนยันได้เองเมื่อ Open-Meteo "
                        "อัปเดตข้อมูลย้อนหลัง (archive) ให้ครอบคลุมวันนั้น"
                    )

    with st.container(border=True):
        st.subheader("ความสะอาดของแผง เทียบกับฝุ่น PM2.5 และปริมาณฝนตามช่วงเวลา (ย้อนหลัง 7 วัน)")
        st.caption(
            "แถวบน 2 แถวคือ \"ผล\" (ระดับความสะอาดแผง + อัตราสกปรกต่อวัน) "
            "ส่วนแถวล่าง 2 แถวคือ \"สาเหตุ\" (ฝุ่นสะสม + ฝนที่ล้างแผง)"
        )
        chart_start = today_ts - pd.Timedelta(days=7)
        sr_full = data["soiling_ratio"]
        sr_rate_full = -sr_full.diff()  # ค่าบวก = สกปรกเร็วขึ้น, ค่าติดลบพุ่ง = จุดที่ฝนล้างแผง (SR กระโดดขึ้น)
        sr = sr_full.loc[sr_full.index >= chart_start]
        sr_rate = sr_rate_full.reindex(sr.index)
        rain = data["weather_daily"]["rainfall_mm"].reindex(sr.index)
        pm25_trend = data["aq_daily"]["pm2_5"].reindex(sr.index)

        fig = make_subplots(rows=4, cols=1, shared_xaxes=True, row_heights=[0.28, 0.18, 0.27, 0.27], vertical_spacing=0.03)
        fig.add_trace(
            go.Scatter(x=sr.index, y=sr.values, name="ความสะอาดแผง", mode="lines",
                       line=dict(color=ACCENT, width=2), showlegend=False),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=sr_rate.index, y=sr_rate.values, name="อัตราสกปรก/วัน", mode="lines",
                       line=dict(color=ACCENT, width=1.5), showlegend=False, opacity=0.6),
            row=2, col=1,
        )
        fig.add_trace(
            go.Scatter(x=pm25_trend.index, y=pm25_trend.values, name="PM2.5", mode="lines",
                       line=dict(color=ACCENT2, width=2), showlegend=False),
            row=3, col=1,
        )
        fig.add_trace(
            go.Bar(x=rain.index, y=rain.values, name="ปริมาณฝน", marker_color=GRAY_MUTED, showlegend=False),
            row=4, col=1,
        )

        ref_lines = [(data["last_clean_date"], "ล้างล่าสุด", GRAY_BASELINE, "solid")]
        if next_reset is not None:
            ref_lines.append((next_reset, "ฝนจะล้างเอง", GRAY_MUTED, "dot"))
        if result is not None and not (next_reset is not None and data["hits_edge"]):
            ref_lines.append((pd.Timestamp(result["recommended_date"]), "แนะนำให้ล้าง", ACCENT2, "dash"))
        ref_lines.append((today_ts, "วันนี้", GRAY_BASELINE, "solid"))

        ref_lines = [r for r in ref_lines if chart_start <= r[0] <= sr.index.max()]
        ref_lines.sort(key=lambda r: r[0])
        total_span_days = max((sr.index.max() - sr.index.min()).days, 1)
        collision_gap_days = max(total_span_days * 0.03, 2)
        prev_x, stack_level = None, 0
        for x, label, color, dash in ref_lines:
            for row in (1, 2, 3, 4):
                fig.add_vline(x=x, line_color=color, line_dash=dash, line_width=1, row=row, col=1)
            stack_level = stack_level + 1 if prev_x is not None and abs((x - prev_x).days) <= collision_gap_days else 0
            fig.add_annotation(x=x, y=1.0, yref="y domain", text=label, showarrow=False,
                                yshift=12 + stack_level * 16, font=dict(size=11, color=TEXT_SECONDARY), row=1, col=1)
            prev_x = x

        fig.update_yaxes(title_text="ความสะอาดแผง (1=สะอาดเต็มที่)", range=[0.5, 1.05], gridcolor=GRIDLINE,
                          zeroline=False, row=1, col=1)
        fig.update_yaxes(title_text="อัตราสกปรก/วัน", gridcolor=GRIDLINE, zeroline=False, row=2, col=1)
        fig.update_yaxes(title_text="PM2.5 (µg/m³)", gridcolor=GRIDLINE, zeroline=False, row=3, col=1)
        fig.update_yaxes(title_text="ฝน (มม.)", gridcolor=GRIDLINE, zeroline=False, row=4, col=1)
        fig.update_xaxes(gridcolor=GRIDLINE, row=1, col=1)
        fig.update_xaxes(gridcolor=GRIDLINE, row=2, col=1)
        fig.update_xaxes(gridcolor=GRIDLINE, row=3, col=1)
        fig.update_xaxes(gridcolor=GRIDLINE, row=4, col=1)
        label_rows_needed = stack_level + 1
        fig.update_layout(
            height=680, showlegend=False,
            **{**CHART_LAYOUT_DEFAULTS, "margin": dict(t=30 + label_rows_needed * 16, b=30, l=10, r=10)},
        )
        st.plotly_chart(fig, width="stretch")

    with st.container(border=True):
        st.subheader("แนวโน้มพลังงานที่คาดว่าจะผลิตได้ 7 วันข้างหน้า (ถ้าแผงสะอาด)")
        st.caption("ยังไม่หักความสกปรก · แท่งสีส้ม = วันนี้")
        window_end = today_ts + pd.Timedelta(days=6)
        gen_trend = data["expected_generation"].reindex(sr.index).loc[today_ts:window_end]
        bar_colors = [ACCENT2 if x == today_ts else ACCENT for x in gen_trend.index]

        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            x=gen_trend.index, y=gen_trend.values, marker_color=bar_colors,
            text=[f"{v:.0f}" for v in gen_trend.values], textposition="outside", showlegend=False,
        ))
        fig3.update_yaxes(title_text="พลังงาน (kWh/วัน)", gridcolor=GRIDLINE, zeroline=False, rangemode="tozero")
        fig3.update_xaxes(gridcolor=GRIDLINE, tickformat="%d %b", dtick=86400000)
        fig3.update_layout(
            height=280, showlegend=False,
            **{**CHART_LAYOUT_DEFAULTS, "margin": dict(t=30, b=30, l=10, r=10)},
        )
        st.plotly_chart(fig3, width="stretch")

    with st.container(border=True):
        st.subheader("กำลังผลิตวันนี้ เทียบกับความเข้มแสง (Power Profile)")
        st.caption(
            "เส้นทึบ = กำลังผลิตจริง (หักความสกปรกแล้ว) · เส้นประ = ถ้าแผงสะอาดเต็มที่ · "
            "พื้นที่สี = ความเข้มแสง GHI (แกนขวา) — พลังงานจริงคำนวณจากแสงบนระนาบแผง (POA) "
            "ซึ่งต่างจาก GHI เล็กน้อย ดูแท็บ \"เกี่ยวกับโมเดล\""
        )
        today_hourly = data["weather_hourly"][data["weather_hourly"].index.date == today_ts.date()]
        expected_power_kw_today = data["expected_power_kw_today"]
        actual_power_kw_today = data["actual_power_kw_today"]

        if len(today_hourly) == 0 or expected_power_kw_today is None:
            st.info("ยังไม่มีข้อมูลรายชั่วโมงของวันนี้ให้แสดง")
        else:
            fig4 = make_subplots(specs=[[{"secondary_y": True}]])
            fig4.add_trace(
                go.Scatter(
                    x=today_hourly.index, y=today_hourly["ghi"].clip(lower=0), name="GHI (แกนขวา)",
                    mode="lines", line=dict(color=ACCENT2, width=1.5), fill="tozeroy",
                    fillcolor="rgba(35,127,120,0.12)",
                ),
                secondary_y=True,
            )
            fig4.add_trace(
                go.Scatter(
                    x=expected_power_kw_today.index, y=expected_power_kw_today.values, name="คาดการณ์ (แผงสะอาด)",
                    mode="lines", line=dict(color=GRAY_MUTED, width=1.8, dash="dash"),
                ),
                secondary_y=False,
            )
            fig4.add_trace(
                go.Scatter(
                    x=actual_power_kw_today.index, y=actual_power_kw_today.values, name="กำลังผลิตจริง",
                    mode="lines", line=dict(color=ACCENT, width=2.6),
                ),
                secondary_y=False,
            )
            fig4.update_yaxes(title_text="กำลังผลิต (kW)", gridcolor=GRIDLINE, zeroline=False,
                               rangemode="tozero", secondary_y=False)
            fig4.update_yaxes(title_text="GHI (W/m²)", gridcolor=GRIDLINE, zeroline=False,
                               rangemode="tozero", showgrid=False, secondary_y=True)
            fig4.update_xaxes(title_text="เวลา (ชั่วโมง)", gridcolor=GRIDLINE, tickformat="%H:%M")
            fig4.update_layout(
                height=340,
                legend=dict(orientation="h", y=1.15, x=0),
                **{**CHART_LAYOUT_DEFAULTS, "margin": dict(t=50, b=30, l=10, r=10)},
            )
            st.plotly_chart(fig4, width="stretch")
            psh_today = today_hourly["ghi"].clip(lower=0).sum() / 1000.0
            st.caption(f"PSH ของวันนี้ (ผลรวม GHI ÷ 1,000 W/m²) ≈ **{psh_today:.2f} ชั่วโมง**")

# ======================= TAB 2: รายละเอียดเศรษฐศาสตร์ =======================
with tab_econ:
    st.subheader("ต้นทุนเทียบกับจำนวนวันหลังล้างแผง")
    st.caption(
        "แนวคิด: ยิ่งปล่อยแผงสกปรกนาน ยิ่งเสียมูลค่าไฟมากขึ้นเรื่อย ๆ แต่ถ้าล้างถี่เกินไป ต้นทุนค่าจ้างล้างต่อวันก็สูง "
        "จุดที่คุ้มค่าที่สุดคือจุดที่ผลรวมของสองต้นทุนนี้ต่ำที่สุด"
    )
    if data["acr_curve"] is not None:
        acr_curve = data["acr_curve"]
        hits_edge = data["hits_edge"]
        # Reuse the contiguous band already computed by recommend_cleaning
        # (economics.py) around the actual optimal_T -- NOT a naive
        # global min/max over every T within tolerance of the minimum,
        # which silently breaks when the ACR curve is multi-modal (two
        # separate near-tied basins get merged into one span that
        # includes the expensive hump between them). See
        # recommend_cleaning's docstring for a real confirmed case.
        band_start, band_end = result["band_low_T"], result["band_high_T"]

        # Extend both lines past the last day we have real projected data
        # for, so the reader can see the trade-off keeps moving the same
        # direction if cleaning is delayed past the recommended window --
        # not real forecast data, so drawn dotted/lighter and labeled.
        extra_days = min(60, max(15, len(acr_curve) // 2))
        t_last = int(acr_curve["T"].iloc[-1])
        cumulative_loss = acr_curve["loss_cost_per_day"] * acr_curve["T"]
        marginal_loss_last = (
            cumulative_loss.iloc[-1] - cumulative_loss.iloc[-2] if len(acr_curve) >= 2 else cumulative_loss.iloc[-1]
        )
        cleaning_cost_total = acr_curve["clean_cost_per_day"].iloc[-1] * t_last

        extra_T = np.arange(t_last, t_last + extra_days + 1)
        extra_cumulative_loss = cumulative_loss.iloc[-1] + marginal_loss_last * (extra_T - t_last)
        extra_loss_per_day = extra_cumulative_loss / extra_T
        extra_clean_per_day = cleaning_cost_total / extra_T
        extra_acr = extra_clean_per_day + extra_loss_per_day

        fig2 = go.Figure()
        if not hits_edge:
            for x in (band_start, band_end):
                fig2.add_vline(x=x, line_color=GRAY_MUTED, line_dash="dash", line_width=1)
            fig2.add_annotation(x=(band_start + band_end) / 2, y=1.0, yref="paper", showarrow=False,
                                 text=f"ช่วงวันแนะนำ: วันที่ {band_start}–{band_end}",
                                 font=dict(size=12, color=TEXT_SECONDARY), yshift=14)

        fig2.add_trace(go.Scatter(
            x=acr_curve["T"], y=acr_curve["clean_cost_per_day"], name="ต้นทุนค่าล้างเฉลี่ย/วัน",
            mode="lines", line=dict(color=ACCENT, width=2),
        ))
        fig2.add_trace(go.Scatter(
            x=acr_curve["T"], y=acr_curve["loss_cost_per_day"], name="มูลค่าไฟที่เสียเฉลี่ย/วัน",
            mode="lines", line=dict(color=ACCENT2, width=2),
        ))
        fig2.add_trace(go.Scatter(
            x=acr_curve["T"], y=acr_curve["acr"], name="ต้นทุนรวมเฉลี่ย/วัน (ACR)",
            mode="lines", line=dict(color=DARK_TOTAL, width=3),
        ))
        fig2.add_trace(go.Scatter(
            x=extra_T, y=extra_clean_per_day, mode="lines", showlegend=False,
            line=dict(color=ACCENT, width=2, dash="dot"), opacity=0.5, hoverinfo="skip",
        ))
        fig2.add_trace(go.Scatter(
            x=extra_T, y=extra_loss_per_day, mode="lines", showlegend=False,
            line=dict(color=ACCENT2, width=2, dash="dot"), opacity=0.5, hoverinfo="skip",
        ))
        fig2.add_trace(go.Scatter(
            x=extra_T, y=extra_acr, mode="lines", showlegend=False,
            line=dict(color=DARK_TOTAL, width=3, dash="dot"), opacity=0.5, hoverinfo="skip",
        ))
        if hits_edge:
            fig2.add_trace(go.Scatter(
                x=[result["optimal_T"]], y=[result["acr_at_optimal"]], mode="markers", showlegend=False,
                marker=dict(size=10, color=GRAY_MUTED, symbol="diamond", line=dict(color="white", width=1.5)),
                hovertemplate=(
                    f"ฝนมาถึงตรงนี้ก่อน (วันที่ {result['optimal_T']})<br>"
                    "เส้นนี้ยังลดลงต่อเนื่อง ไม่ใช่จุดต่ำสุดจริง — "
                    "แปลว่าไม่มีวันไหนก่อนหน้านี้ที่จ้างล้างคุ้มกว่ารอฝน<extra></extra>"
                ),
            ))
        else:
            fig2.add_trace(go.Scatter(
                x=[result["optimal_T"]], y=[result["acr_at_optimal"]], mode="markers", showlegend=False,
                marker=dict(size=10, color=DARK_TOTAL, line=dict(color="white", width=1.5)),
                hovertemplate=f"จุดคุ้มค่าที่สุด: วันที่ {result['optimal_T']}<br>ต้นทุนรวม {result['acr_at_optimal']:.2f} บาท/วัน<extra></extra>",
            ))
        fig2.add_annotation(x=t_last, y=1.0, yref="paper", showarrow=False, text="ถ้ายังไม่ล้าง →",
                             font=dict(size=10, color=GRAY_MUTED), xanchor="left", xshift=4, yshift=-6)

        last_extra = extra_T[-1]
        fig2.add_annotation(x=last_extra, y=extra_clean_per_day[-1], text="ค่าล้าง/วัน",
                             showarrow=False, xanchor="left", xshift=8, font=dict(size=11, color=ACCENT))
        fig2.add_annotation(x=last_extra, y=extra_loss_per_day[-1], text="มูลค่าไฟเสีย/วัน (ประมาณการ)",
                             showarrow=False, xanchor="left", xshift=8, font=dict(size=11, color=ACCENT2))
        fig2.add_annotation(x=last_extra, y=extra_acr[-1], text="ต้นทุนรวม/วัน (ACR)",
                             showarrow=False, xanchor="left", xshift=8, font=dict(size=11, color=DARK_TOTAL))

        log_scale = st.checkbox(
            "แสดงเป็น log scale", value=True,
            help="เส้นเหล่านี้มีขนาดต่างกันมาก (10-100 เท่า) ถ้าใช้สเกลปกติเส้นที่ค่าน้อยจะดูแบนราบ "
                 "log scale ช่วยให้เห็นการเปลี่ยนแปลงของทุกเส้นในแกนเดียวกัน โดยไม่ต้องใช้สองแกนที่หลอกตาได้",
        )
        yaxis_kwargs = dict(title="บาท / วัน" + (" (log scale)" if log_scale else ""),
                             type="log" if log_scale else "linear", gridcolor=GRIDLINE, zeroline=False)
        if log_scale:
            positive_vals = np.concatenate([
                acr_curve["clean_cost_per_day"].to_numpy(), acr_curve["loss_cost_per_day"].to_numpy(),
                acr_curve["acr"].to_numpy(), extra_clean_per_day, extra_loss_per_day, extra_acr,
            ])
            positive_vals = positive_vals[positive_vals > 0]
            yaxis_kwargs["range"] = [np.log10(positive_vals.min()) - 0.15, np.log10(positive_vals.max()) + 0.15]
        fig2.update_layout(
            xaxis=dict(title="จำนวนวันหลังล้างแผงล่าสุด", gridcolor=GRIDLINE, zeroline=False),
            yaxis=yaxis_kwargs,
            legend=dict(orientation="h", y=1.12, x=0),
            height=380,
            **{k: v for k, v in CHART_LAYOUT_DEFAULTS.items() if k != "margin"},
            margin=dict(t=60, b=30, l=10, r=140),
        )
        st.plotly_chart(fig2, width="stretch")
        if hits_edge:
            st.caption(
                f"เส้นสีเข้ม **(ACR — ต้นทุนรวมเฉลี่ย/วัน)** ยังคง**ลดลงต่อเนื่อง**ตลอดช่วงที่มีข้อมูล (ถึงวันที่ {t_last} "
                "ซึ่งเป็นวันก่อนที่คาดว่าฝนจะมาล้างแผงให้เอง) — จุดสี่เหลี่ยมข้าวหลามตัดที่เห็น **ไม่ใช่จุดต่ำสุดจริงทางคณิตศาสตร์** "
                "เป็นแค่ขอบเขตของข้อมูลที่มองเห็นได้ก่อนฝนจะมาถึง เพราะเส้นยังไม่เลี้ยวกลับขึ้นเลยตลอดช่วงนี้ จึงสรุปได้ว่า"
                "**ไม่มีวันไหนก่อนฝนที่จ้างล้างแผงเองแล้วคุ้มกว่าการรอ** จุดต่ำสุดจริง (ถ้าสมมติไม่มีฝนเลย) อาจอยู่ไกลกว่านี้ "
                "แต่ไม่จำเป็นต้องรู้ตัวเลขนั้น เพราะฝนมาถึงก่อนอยู่ดี"
            )
        else:
            st.caption(
                f"เส้นสีเข้ม **(ACR — ต้นทุนรวมเฉลี่ย/วัน)** คือผลรวมของอีกสองเส้น เป็นตัวเลขจริงที่ระบบใช้ตัดสินใจ — "
                f"จุดต่ำสุดของเส้นนี้ (จุดวงกลม) คือวันที่ {result['optimal_T']} ซึ่งใกล้เคียงแต่**ไม่ตรงเป๊ะ**กับจุดที่เส้นน้ำเงิน/ส้มตัดกัน "
                f"(ตัดกันช่วงวันที่ {band_start}–{band_end}) เพราะจุดคุ้มค่าที่สุดคือจุดต่ำสุดของผลรวม ไม่ใช่จุดที่สองต้นทุนเท่ากันพอดี "
                f"เส้นประช่วงหลังวันที่ {t_last} เป็นการประมาณการต่อ (สมมติอัตราสูญเสียเท่าวันสุดท้ายที่มีข้อมูลจริง) "
                f"ไม่ใช่พยากรณ์จริง แค่แสดงให้เห็นว่าต้นทุนรวมจะยังเพิ่มต่อไปถ้าไม่ล้างแผง"
            )

        with st.expander("คำอธิบายศัพท์เทคนิคในกราฟนี้"):
            st.markdown(
                "- **ต้นทุนค่าล้างเฉลี่ย/วัน** (เส้นน้ำเงิน) = ค่าจ้างล้างแผงหารด้วยจำนวนวันตั้งแต่ล้างครั้งล่าสุด "
                "(ยิ่งรอนาน ค่าเฉลี่ยต่อวันยิ่งถูกลง)\n"
                "- **มูลค่าไฟที่เสียเฉลี่ย/วัน** (เส้นส้ม) = ผลรวมมูลค่าไฟที่หายไปจากความสกปรกสะสม หารด้วยจำนวนวัน\n"
                "- **ต้นทุนรวมเฉลี่ย/วัน (ACR — Average Cost Rate)** (เส้นสีเข้ม) = ผลรวมของสองเส้นข้างบน "
                "คือค่าที่ระบบหาจุดต่ำสุดเพื่อแนะนำวันล้างแผง เรียกวันที่แนะนำว่า **T\\***"
            )
    else:
        if next_reset is not None:
            st.info(
                f"ยังไม่มีกราฟให้ดู เพราะคาดว่าฝนจะมาล้างแผงเองใน {days_until} วัน — ช่วงเวลานี้สั้นเกินไปที่จะคำนวณความคุ้มค่า "
                f"ลองลดค่า \"ปริมาณฝนขั้นต่ำที่ล้างแผงสะอาดได้\" ในแถบด้านซ้ายเพื่อดูกราฟนี้ในสถานการณ์ที่แล้งนานขึ้น"
            )
        else:
            st.info("ข้อมูลยังไม่พอคำนวณ ลองเช็คใหม่พรุ่งนี้")

# ======================= TAB 3: เกี่ยวกับโมเดล =======================
with tab_about:
    st.subheader("แบบจำลองที่ใช้")
    st.markdown(
        "1. **ข้อมูลอากาศ/ฝุ่น** — Open-Meteo (ไม่ต้องขอ API key): ข้อมูลจริงย้อนหลัง + พยากรณ์ล่วงหน้า "
        "(อากาศ 16 วัน, ฝุ่น PM ~5 วัน) ส่วนที่เกินพยากรณ์จริง เติมด้วยข้อมูล**ปีตัวแทนจริงต่อเดือน** (representative-year replay) "
        "เลือกปีที่มีสถิติฝนใกล้ค่ากลางที่สุดของ 10 ปีย้อนหลัง แยกกันสำหรับอากาศและฝุ่น (ฝุ่นจำกัดเฉพาะปีที่ CAMS มีข้อมูลจริง ~2022 เป็นต้นมา) "
        "แล้วเล่นข้อมูลจริงของเดือนนั้นซ้ำ ไม่ใช่ค่าเฉลี่ย จึงยังมีความแปรผันรายชั่วโมงจริงอยู่ ดูรายละเอียดใน `src/representative_year.py`\n"
        "2. **แบบจำลองความสกปรก (Soiling)** — `pvlib.soiling.hsu` คำนวณจาก PM2.5/PM10 จริง + ปริมาณฝน "
        "ไม่ได้ตั้งอัตราสกปรกเองแบบเดา — เช็คฝนเป็น**รายชั่วโมง** (ไม่ใช่รายวัน) ตามที่งานวิจัยแนะนำ "
        "ดูหัวข้อ \"การตรวจสอบความถูกต้อง\" ด้านล่างสำหรับเหตุผล\n"
        "3. **แบบจำลองพลังงานที่ควรผลิตได้ (ถ้าแผงสะอาด)** — pvlib PVWatts chain "
        "(แปลงแสงตกกระทบมุมแผง + คำนวณอุณหภูมิแผงด้วย**ค่าสัมประสิทธิ์อุณหภูมิ**ที่ตั้งไว้ในแถบด้านซ้าย (default -0.40%/°C) "
        "+ inverter) คูณด้วย **\"ประสิทธิภาพระบบโดยรวม\"** ที่ตั้งไว้ในแถบด้านซ้าย "
        "(ค่า default 88%, อิงจาก NREL PVWatts) ซึ่งเป็นการสูญเสียอื่น ๆ ที่ไม่ใช่ความสกปรก เช่น สายไฟ/ขั้วต่อ, "
        "mismatch ระหว่างแผง, เงาบัง, การเสื่อมสภาพตามอายุ — ความสกปรกคำนวณแยกในข้อ 2 ด้านบนแล้ว ไม่ถูกนับซ้ำตรงนี้\n"
        "4. **การตัดสินใจเชิงเศรษฐศาสตร์** — หาจำนวนวันที่ทำให้ต้นทุนเฉลี่ยต่อวัน "
        "(ค่าล้าง + มูลค่าไฟที่เสีย) ต่ำที่สุด และเทียบกับวันที่ฝนน่าจะมาล้างแผงให้เองด้วย"
    )

    st.subheader("ข้อจำกัดที่ควรรู้ก่อนนำไปอ้างอิง")
    st.markdown(
        "- การล้างแผงจากฝนเป็นแบบ **ทั้งหมดหรือไม่เลย** (binary threshold ต่อชั่วโมง) ไม่มีการล้างบางส่วนแบบค่อยเป็นค่อยไป "
        "และไม่ได้จำลองผลของฝนน้อยที่อาจทำให้ฝุ่นจับเป็นคราบแย่กว่าเดิม (แม้จะเช็คเป็นรายชั่วโมงแล้ว ยังคง all-or-nothing อยู่ดี "
        "งานวิจัยเสนอ \"relative recovery of cleanness index\" ที่ละเอียดกว่านี้ แต่ยังไม่ได้ implement)\n"
        "- อัตราการเกาะของฝุ่นบนแผง (`deposition velocity`) ปรับเทียบกับ**ช่วงตัวเลขที่งานวิจัยในเอเชียตะวันออกเฉียงใต้รายงานไว้** "
        "(~0.1-0.3%/วัน ดูหัวข้อ \"การปรับเทียบโมเดล\" ด้านล่าง) ยังไม่ใช่การปรับเทียบกับข้อมูลผลผลิตจริงของไซต์นี้โดยเฉพาะ "
        "เพราะยังไม่มีข้อมูลนั้น\n"
        "- ค่าฝุ่นดิบมาจาก **CAMS Global** (ความละเอียด ~40 กม.) แต่ปรับเทียบ (bias-correct) กับสถานี Air4Thai จริงแล้ว "
        "**เฉพาะเดือน/พิกัดที่มีสถานีใกล้พอ** ด้วยตัวคูณรายเดือน (median ของ Air4Thai/CAMS ปีล่าสุดที่มีข้อมูลครบ) — "
        "ยังไม่ใช่การปรับเทียบกับข้อมูล**ผลผลิตพลังงานจริง**ของไซต์นี้ (คนละเรื่องกับ deposition velocity ด้านบน) "
        "และ correction factor คำนวณจากข้อมูลปีเดียว (2025) ยังไม่ใช่ค่าเฉลี่ยหลายปีที่เสถียรกว่านี้ — "
        "**ที่สำคัญกว่านั้น**: correction factor คำนวณจากการเทียบ Air4Thai กับ CAMS **ย้อนหลัง (reanalysis)** ซึ่งแม่นกว่า "
        "CAMS **พยากรณ์ (forecast)** ที่ใช้กับค่า \"วันนี้\"/อนาคตในแดชบอร์ด — สอง branch นี้อาจมีขนาด bias ต่างกัน "
        "ตัวคูณที่ใช้จึงอาจแก้ไม่เต็มที่สำหรับค่าพยากรณ์ระยะสั้น (พบว่ายังต่ำกว่าค่าจริงที่วัดได้จาก Air4Thai อยู่พอสมควรในบางวัน)\n"
        "- ช่วงเกินพยากรณ์จริง ใช้การเล่นซ้ำข้อมูลจริงของปีตัวแทนต่อเดือน (representative-year replay) แทน "
        "ไม่ใช่การพยากรณ์ที่มีความน่าจะเป็น — ปีตัวแทนของฝุ่นเลือกได้แค่จากปีที่ CAMS มีข้อมูลจริง (~2022 เป็นต้นมา) "
        "ซึ่งแคบกว่าปีตัวแทนของอากาศ (ย้อนได้ถึง ~10 ปี)\n"
        "- **ฝนที่ทำให้เกิดการรีเซตความสะอาดของ \"วันนี้\" มาจากพยากรณ์เสมอ** (ยังไม่มีข้อมูลจริงวัดแล้ว) ต่างจากวันก่อนหน้าที่มาจาก "
        "Open-Meteo historical archive (ตระกูล ERA5/ECMWF IFS — ผสานข้อมูลสถานี/เรดาร์/ดาวเทียมจริง คนละโมเดลกับ CAMS ที่ใช้กับฝุ่น "
        "และละเอียดกว่า ~40 กม. ของ CAMS) — ถ้าฝนจริงตกน้อยกว่าพยากรณ์ การรีเซตของวันนี้จะถูกยกเลิกอัตโนมัติในการรันครั้งถัดไป "
        "(เมื่อวันนี้กลายเป็น \"เมื่อวาน\" และถูกดึงข้อมูลใหม่จาก archive) แดชบอร์ดจะเตือนด้วยกล่องสีเหลืองทุกครั้งที่การรีเซตล่าสุดยังไม่ยืนยัน "
        "เพราะค่านี้กระทบความสะอาดแผงที่คำนวณของทุกวันถัดไปจนกว่าจะถึงการรีเซตครั้งหน้า — แม้แต่ archive เองก็ยังไม่ใช่ ground truth สมบูรณ์ "
        "(ฝนเป็นตัวแปรที่ reanalysis ทุกเจ้าทำได้แม่นน้อยที่สุด เพราะเป็นฝนแบบ convective กระจุกตัวเป็นจุดในเขตร้อน)\n"
        "- **ตรวจสอบฝนเมื่อวานกับสถานี TMD (กรมอุตุนิยมวิทยา) จริงเพิ่มอีกชั้นทุกเช้า** (`WeatherTodayBySynop/Agro/Hydro`, "
        "116 สถานีทั่วประเทศ) endpoint นี้รายงาน**แค่วันละครั้ง เวลา 07:00 น. เสมอ** เป็นยอดฝนสะสม 24 ชม.ที่ผ่านมา "
        "(ครอบคลุมเมื่อวานเกือบทั้งวัน ไม่ใช่วันนี้) — ระบบใช้ค่านี้ **ยกเลิกการรีเซตของเมื่อวานที่พยากรณ์ผิด** ได้ทันทีทุกเช้าที่มีรายงานใหม่ "
        "เฉพาะทิศทาง \"พยากรณ์บอกว่าฝนจะล้างแผง แต่จริงๆ ฝนน้อยกว่านั้นมาก\" เท่านั้น — ทิศตรงข้าม (พยากรณ์พลาดฝนที่ตกจริง) ยังไม่ได้แก้ "
        "เพราะ TMD ให้แค่ยอดรวม ไม่รู้ว่าฝนตกชั่วโมงไหนแน่ ๆ ส่วน**วันนี้**ยังต้องรอพรุ่งนี้เช้าถึงจะยืนยันได้ "
        "(ดูรายละเอียดการค้นคว้าเต็มใน `docs/data_source_research.md`)\n"
        "- ค่าไฟและค่าจ้างล้างแผงเป็น**ค่าเริ่มต้นสมมติ** ยังไม่ใช่ตัวเลขจริงจากไซต์ — เปลี่ยนได้ในแถบด้านซ้าย"
    )

    st.subheader("การปรับเทียบโมเดล (Calibration)")
    st.markdown(
        "ค่า `deposition velocity` เริ่มต้นของ pvlib (`{'2_5': 0.0009, '10': 0.004}` m/s) ทำให้โมเดลนี้ให้อัตราสกปรกแค่ "
        "**~0.017%/วัน** เมื่อรันกับข้อมูลจริงของกรุงเทพฯ — ต่ำกว่าที่งานวิจัยในภูมิภาคนี้รายงานไว้มาก จึงปรับ (scale ขึ้น 10 เท่า) "
        "โดย**ใช้ข้อมูล PM2.5 ช่วงหน้าแล้ง (ธ.ค.)** เป็นฐานคำนวณ ไม่ใช่ช่วงหน้าฝน เพราะ PM2.5 จริงในข้อมูลของเราต่างกันตามฤดูกาลมาก "
        "(หน้าฝน ~10 µg/m³ vs หน้าแล้ง ~30 µg/m³) — ถ้าคาลิเบรทด้วยช่วงหน้าฝนจะได้ค่าคงที่ที่สูงเกินจริงเมื่อเจอ PM หน้าแล้งจริง "
        "(ปัญหานี้เจอและแก้ระหว่างทำ):\n\n"
        "| แหล่งอ้างอิง | อัตราสกปรกที่รายงาน |\n"
        "|---|---|\n"
        "| งานวิจัยไทย (11 โรงไฟฟ้า 10 จังหวัด, 1 ปี) | performance ratio ช่วงแล้ง 0.77-0.88, optical transmission loss เกิน 40% ที่พีค |\n"
        "| มาเลเซีย (Perak, 1 เดือน) | 4.53% (สูงสุด 5.92%) ≈ 0.15-0.20%/วัน |\n"
        "| สิงคโปร์ (35 วัน) | 2% ≈ 0.057%/วัน |\n"
        "| ค่าเฉลี่ยทั้งเอเชีย (รวมโซนแห้งแล้ง) | ~0.28%/วัน |\n"
        "| **โมเดลนี้ ช่วงหน้าแล้ง (ปรับเทียบแล้ว)** | **~0.17%/วัน** |\n"
        "| **โมเดลนี้ ช่วงหน้าฝน (ค่าคงที่เดียวกัน)** | **~0.12%/วัน** (ต่ำกว่าเพราะ PM จริงต่ำกว่า ไม่ได้ปรับค่าคงที่แยก) |\n"
        "| โมเดลนี้ (ค่า default ของ pvlib, ยังไม่ปรับ) | ~0.017%/วัน |\n\n"
        "**ข้อควรระวัง**: นี่คือการปรับเทียบกับ**ค่าเฉลี่ยภูมิภาค** ไม่ใช่การปรับเทียบกับข้อมูลผลผลิตจริงของไซต์นี้โดยเฉพาะ "
        "(ยังไม่มีข้อมูลนั้น) ความแม่นยำจึงยังจำกัดกว่าการ calibrate ด้วยข้อมูลจริง"
    )

    st.subheader("การตรวจสอบความถูกต้อง (Validation)")
    st.markdown(
        "**มุมเอียงแผงที่เหมาะสม (tilt angle)**: ทดสอบ `compute_expected_generation` ด้วยข้อมูลอากาศจริงทั้งปีปฏิทิน 2025 "
        "(8,760 ชั่วโมง ไม่มีข้อมูลสังเคราะห์ปน) ไล่มุมเอียง 0-30° พบว่าพลังงานรวมทั้งปีสูงสุดที่ **มุม 15°** และมุม **13.5° "
        "(ค่าที่แหล่งข้อมูลอุตสาหกรรมไทยแนะนำสำหรับกรุงเทพฯ) ให้พลังงานต่างจากจุดสูงสุดแค่ 0.02%** — ตรงกับมาตรฐานทั่วไป (10-15°) "
        "แทบสมบูรณ์ ยืนยันว่า pvlib POA transposition (`haydavies` model) คำนวณตำแหน่งดวงอาทิตย์/มุมตกกระทบถูกต้อง\n\n"
        "**ข้อควรระวังตอนอ่านค่าในแดชบอร์ด**: การทดสอบข้างต้นใช้ **ข้อมูลเต็มปี** โดยเฉพาะ แต่กราฟ/สถานะปัจจุบันของแดชบอร์ดนี้ "
        "คำนวณจากช่วง \"มองไปข้างหน้ากี่วัน\" ที่ตั้งไว้ (ค่าเริ่มต้น 90 วัน) ถ้าช่วงเวลานั้นไม่ครอบคลุมฤดูหนาว (ธ.ค.-ก.พ. ซึ่งดวงอาทิตย์อยู่ต่ำ "
        "และมุมเอียงมีประโยชน์มากที่สุด) การไล่มุมเอียงในแดชบอร์ดจะดู \"เหมือน\" มุมต่ำดีที่สุดเสมอ ทั้งที่ไม่ใช่ค่าที่เหมาะกับการติดตั้งถาวร "
        "— ตั้ง horizon ≥ 365 วันถ้าต้องการทดสอบความไวต่อมุมเอียงให้แม่น\n\n"
        "**เกณฑ์ล้างแผงจากฝน — รายชั่วโมง vs รายวัน**: เดิมโมเดลนี้เช็คฝนสะสม**รายวัน**เทียบ threshold (เช่น 6mm/วัน) "
        "ซึ่งแยกไม่ออกระหว่างพายุฝนแรงช่วงสั้น ๆ กับฝนปรอยกระจายทั้งวันที่ผลรวมเท่ากัน — เปลี่ยนมาเช็คเป็น**รายชั่วโมง** "
        "(`rain_accum_period=1h`, threshold default 1.0mm/ชม.) ตามค่าที่งานวิจัย Norde Santos et al. 2024 (Solar RRL) "
        "รายงานว่าให้ผลตรงกับเหตุการณ์ล้างแผงจริงที่สุด และตรงกับค่า default ของ `pvlib.soiling.hsu` เอง "
        "(`rain_accum_period` default = 1 ชั่วโมง ไม่ใช่ 1 วัน) ทดสอบยืนยันแล้วว่าฝน 3mm กระจาย 12 ชั่วโมง (ไม่ถึง threshold รายชั่วโมงเลย) "
        "จะ**ไม่ถูกนับว่าล้างแผง** ต่างจากพายุฝน 3mm ในชั่วโมงเดียวที่นับว่าล้าง แม้ผลรวมรายวันจะเท่ากัน\n\n"
        "**การปรับเทียบฝุ่นกับ Air4Thai**: เทียบ PM2.5 รายวันของสถานี Air4Thai ที่ใกล้พิกัดที่สุด กับค่า CAMS Global "
        "ของ Open-Meteo ช่วงเวลาเดียวกัน (ปี 2025 เต็มปี) แล้วหา correction factor เป็น median ของอัตราส่วนรายเดือน — "
        "ทดสอบยืนยันแล้วว่า**ใช้ได้กับทั้ง 5 จังหวัดใน dropdown** (กรุงเทพฯ/เชียงใหม่/ขอนแก่น/ระยอง/หาดใหญ่) แม้ dataset ต้นทาง "
        "จะตั้งชื่อว่า \"กรุงเทพฯและปริมณฑล\" ก็ตาม (ตรวจสอบเนื้อหาจริงแล้วครอบคลุมกว้างกว่าชื่อ) ตัวอย่างค่าที่พบสำหรับกรุงเทพฯ: "
        "ม.ค. ×1.39 (CAMS ต่ำกว่าจริง), ต.ค. ×0.57 (CAMS สูงกว่าจริง) — ยืนยันว่า bias ไม่คงที่ตลอดปีจริงตามที่คาดไว้"
    )

    st.subheader("แหล่งข้อมูล")
    st.markdown(
        "- สภาพอากาศ + ฝุ่น (ดิบ): [Open-Meteo](https://open-meteo.com/) (Weather API + Air Quality API, CAMS Global)\n"
        "- ฝุ่นจริง (สำหรับปรับเทียบ): [Air4Thai](https://air4thai.pcd.go.th/) (เรียลไทม์) และ "
        "[PCD Open Data Catalog](https://pcd.gdcatalog.go.th/) (ย้อนหลัง, กรมควบคุมมลพิษ)\n"
        "- ฝนจริง (สำหรับตรวจสอบ): [กรมอุตุนิยมวิทยา (TMD) Open Data API]"
        "(https://data.tmd.go.th/api/index1.php) (`WeatherTodayBySynop/Agro/Hydro`)\n"
        "- แบบจำลองฟิสิกส์: [pvlib python](https://pvlib-python.readthedocs.io/) — `pvlib.soiling.hsu`, "
        "PVWatts chain (`pvsystem.pvwatts_dc`, `inverter.pvwatts`)\n"
        "- งานวิจัยไทย: [Characteristics, compositions, and effects of dust accumulated on PV panels in "
        "tropical agricultural area](https://iopscience.iop.org/article/10.35848/1347-4065/addb54) (IOPscience)\n"
        "- งานวิจัยมาเลเซีย: [Impact of Soiling Rate on Solar Photovoltaic Panel in Malaysia]"
        "(https://www.researchgate.net/publication/328742714_Impact_of_Soiling_Rate_on_Solar_Photovoltaic_Panel_in_Malaysia) "
        "(ResearchGate)\n"
        "- Review รวมภูมิภาคเอเชีย: [Impact of dust accumulation on photovoltaic panels: a review paper]"
        "(https://www.tandfonline.com/doi/full/10.1080/19397038.2022.2140222) (Taylor & Francis)\n"
        "- เกณฑ์ล้างแผงรายชั่วโมง: [Cleaning of Photovoltaic Modules through Rain: Experimental Study and Modeling "
        "Approaches](https://onlinelibrary.wiley.com/doi/full/10.1002/solr.202400551) (Norde Santos et al., Solar RRL 2024)"
    )
