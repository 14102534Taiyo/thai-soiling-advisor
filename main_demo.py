"""End-to-end wiring for one example site: weather_client -> soiling_pv_model
-> economics. Day 1 integration check, not the dashboard (that's Day 2).

Placeholder config below (location, system size, prices) stands in until
real site/economic numbers are supplied -- see docs/interfaces.md and
README.md for caveats this demo does NOT yet handle (HSU's binary
rain-cleaning; PM bias uncorrected against Thai ground stations).

Decision logic: optimize only over the dry stretch between today's last
actual clean and the next *natural* (rain) reset visible within
PROJECTION_HORIZON_DAYS. If nature would clean the panel before the
economic optimum is reached, recommend waiting instead of a manual clean.
"""

from datetime import date, timedelta

import pandas as pd

from src.economics import compute_acr_curve, recommend_cleaning
from src.representative_year import (
    compute_monthly_rain_stats,
    fill_aq_with_representative_years,
    fill_weather_with_representative_years,
    select_pm_representative_years,
    select_representative_weather_years,
)
from src.soiling_pv_model import compute_expected_generation, compute_soiling_ratio
from src.weather_client import (
    get_forecast_air_quality,
    get_forecast_weather,
    get_historical_air_quality,
    get_historical_weather,
)

LAT, LON = 13.7563, 100.5018  # Bangkok placeholder -- swap for a real site
KWP = 100.0
DC_AC_RATIO = 1.2
TILT = 10.0
AZIMUTH = 180.0
SYSTEM_EFFICIENCY = 0.88  # non-soiling system losses (wiring, mismatch, shading, degradation) -- NREL PVWatts default minus its soiling share
GAMMA_PDC = -0.0040  # module temperature coefficient, fraction/degC -- generic crystalline-silicon default
CLEANING_THRESHOLD_MM = 1.0  # mm/hour (research-informed hourly threshold, not mm/day -- see soiling_pv_model.py)
PRICE_PER_KWH = 4.0  # THB, placeholder
CLEANING_COST_PER_KWP = 15.0  # THB, placeholder

HIST_DAYS = 90
FORECAST_DAYS = 16
AQ_FORECAST_DAYS = 5
PROJECTION_HORIZON_DAYS = 90  # total days into the future to project, forecast + beyond-horizon fill
REPRESENTATIVE_YEAR_WINDOW = 10  # years of history behind the weather-TMY + CAMS-climatology fill


def _concat_hourly(hist: pd.DataFrame, fcst: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([hist, fcst])
    return combined[~combined.index.duplicated(keep="last")].sort_index()


def get_beyond_horizon_fill_params(lat: float, lon: float):
    """Representative source years per calendar month -- one mapping for
    weather, one for PM -- for filling weather_hourly/aq_hourly beyond
    Open-Meteo's real forecast horizon (16 days weather, ~5 days air
    quality). See src/representative_year.py's module docstring and
    app.py's identical function for the full rationale: both weather and
    PM are REPLAYED from a real source year per month (chosen for that
    month's rain-threshold-hours typicality; PM's candidate years are
    further restricted to ones where CAMS actually has real coverage,
    ~2022 onward), preserving real hour-to-hour variation -- rather than
    the single-arbitrary-"last year" analog replay this project started
    with, or the flat monthly-average PM climatology that superseded it
    next (see select_pm_representative_years/fill_aq_with_representative_years
    docstrings for why that average was itself superseded). Not cached
    here (unlike app.py's @st.cache_data version) since this script runs
    once and exits."""
    year_end = date.today().year - 1
    year_start = year_end - REPRESENTATIVE_YEAR_WINDOW + 1
    rain_stats = compute_monthly_rain_stats(lat, lon, year_start=year_start, year_end=year_end)
    representative_weather_years = select_representative_weather_years(rain_stats)
    pm_representative_years = select_pm_representative_years(lat, lon, year_start=year_start, year_end=year_end)
    return representative_weather_years, pm_representative_years


def main() -> None:
    today = date.today()
    hist_start = (today - timedelta(days=HIST_DAYS)).isoformat()
    hist_end = (today - timedelta(days=1)).isoformat()

    weather_hourly = _concat_hourly(
        get_historical_weather(LAT, LON, hist_start, hist_end),
        get_forecast_weather(LAT, LON, forecast_days=FORECAST_DAYS),
    )
    aq_hourly = _concat_hourly(
        get_historical_air_quality(LAT, LON, hist_start, hist_end),
        get_forecast_air_quality(LAT, LON, forecast_days=AQ_FORECAST_DAYS),
    )

    horizon_end = today + timedelta(days=PROJECTION_HORIZON_DAYS)
    weather_forecast_end = weather_hourly.index.max().date()
    aq_forecast_end = aq_hourly.index.max().date()

    if weather_forecast_end < horizon_end or aq_forecast_end < horizon_end:
        representative_weather_years, pm_representative_years = get_beyond_horizon_fill_params(LAT, LON)

    if weather_forecast_end < horizon_end:
        gap_start = weather_forecast_end + timedelta(days=1)
        fill_weather = fill_weather_with_representative_years(
            LAT, LON, representative_weather_years, gap_start.isoformat(), horizon_end.isoformat()
        )
        weather_hourly = _concat_hourly(weather_hourly, fill_weather)

    if aq_forecast_end < horizon_end:
        gap_start = aq_forecast_end + timedelta(days=1)
        fill_aq = fill_aq_with_representative_years(
            LAT, LON, pm_representative_years, gap_start.isoformat(), horizon_end.isoformat()
        )
        aq_hourly = _concat_hourly(aq_hourly, fill_aq)

    print(f"Weather: real data through {weather_forecast_end}, weather-TMY fill "
          f"{weather_forecast_end + timedelta(days=1)}..{horizon_end}")
    print(f"Air quality: real data through {aq_forecast_end}, PM representative-year fill "
          f"{aq_forecast_end + timedelta(days=1)}..{horizon_end}")

    weather_daily = weather_hourly.resample("D").agg(
        {"rainfall_mm": "sum", "ghi": "mean", "dni": "mean", "dhi": "mean",
         "temp_air": "mean", "wind_speed": "mean"}
    )
    aq_daily = aq_hourly.resample("D").mean()

    soiling_ratio = compute_soiling_ratio(
        weather_hourly, aq_hourly,
        cleaning_threshold_mm=CLEANING_THRESHOLD_MM, tilt=TILT,
    )
    expected_generation = compute_expected_generation(
        weather_hourly, LAT, LON, KWP, DC_AC_RATIO, TILT, AZIMUTH,
        system_efficiency=SYSTEM_EFFICIENCY, gamma_pdc=GAMMA_PDC,
    )

    # "Last clean" must anchor to today, not to the latest reset anywhere in
    # the projected window -- a future analog-year rain event is not when
    # the panel was actually last cleaned.
    today_ts = pd.Timestamp(today)
    past_resets = soiling_ratio.index[(soiling_ratio.index <= today_ts) & (soiling_ratio >= 0.999)]
    last_clean_date = past_resets.max() if len(past_resets) else soiling_ratio.index.min()
    assumed_reset = len(past_resets) == 0

    # Nature may clean the panel for us before any manual-cleaning economic
    # optimum is reached -- find that next rain-reset and only optimize over
    # the dry stretch leading up to it (or the full horizon if none is seen).
    projection_index = soiling_ratio.index[soiling_ratio.index > last_clean_date]
    future_resets = projection_index[soiling_ratio.loc[projection_index] >= 0.999]
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

    print(f"Site: lat={LAT}, lon={LON}")
    print(f"Weather+AQ window: {weather_daily.index.min().date()} .. {weather_daily.index.max().date()}")
    print(f"Last actual clean/reset date: {last_clean_date.date()}"
          f"{' (ASSUMED -- no rain event >= threshold found on or before today)' if assumed_reset else ''}")
    if next_natural_reset_date is not None:
        print(f"Next natural (rain) reset expected: {next_natural_reset_date.date()} (in {days_until_natural} days)")
    else:
        print(f"No natural reset found within the {PROJECTION_HORIZON_DAYS}-day horizon ({horizon_end}) "
              f"-- the dry stretch may extend beyond what we can currently see.")
    print(f"Dry-stretch days available to optimize over: {len(sr_stretch)}")

    if len(sr_stretch) < 2:
        if next_natural_reset_date is not None:
            print(f"\nNo manual cleaning needed -- natural cleaning expected in {days_until_natural} day(s) "
                  f"on {next_natural_reset_date.date()}, too soon for meaningful soiling buildup.")
        else:
            print("\nNot enough post-clean data to run the economic optimizer yet "
                  "(reset happened too close to today). Rerun tomorrow.")
        return

    acr_curve = compute_acr_curve(
        sr_stretch, gen_stretch,
        PRICE_PER_KWH, CLEANING_COST_PER_KWP, KWP,
        max_days=len(sr_stretch),
    )
    # Floor the search at "today" -- see recommend_cleaning's docstring:
    # the ACR curve's unconstrained minimum can sit at a T that's already
    # behind us, which would otherwise produce a recommendation dated in
    # the past.
    min_T = max(1, (today_ts - last_clean_date).days)
    result = recommend_cleaning(acr_curve, last_clean_date=last_clean_date, min_T=min_T)
    hits_edge = result["optimal_T"] >= len(sr_stretch)

    print("\n=== Recommendation ===")
    if next_natural_reset_date is not None and hits_edge:
        print(f"  No manual cleaning needed. Natural cleaning (rain) is expected in "
              f"{days_until_natural} day(s), on {next_natural_reset_date.date()} -- waiting for it "
              f"costs less than cleaning manually beforehand.")
    else:
        for k, v in result.items():
            print(f"  {k}: {v}")
        if next_natural_reset_date is not None:
            print(f"  (Cheaper to clean manually on day {result['optimal_T']} than to wait for the "
                  f"natural reset expected on day {days_until_natural}.)")
        elif hits_edge:
            print(f"\n  Note: optimal_T={result['optimal_T']} sits at the edge of the {len(sr_stretch)}-day "
                  f"visible horizon and no natural reset was found in that window -- the true optimum may "
                  f"lie further out. Raising PROJECTION_HORIZON_DAYS would extend visibility.")


if __name__ == "__main__":
    main()
