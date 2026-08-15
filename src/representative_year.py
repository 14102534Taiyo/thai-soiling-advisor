"""Representative-year selection, for projecting beyond Open-Meteo's
forecast horizon (16 days weather, ~5 days air quality) using real
historical data instead of a single arbitrary "last year" replay.

Two independent strategies, chosen per-site depending on Air4Thai/PCD
station coverage there (verified live 2026-08-14 -- see below):

  1. Joint rain+PM (compute_monthly_stats / select_representative_years /
     ensemble_years / fill_hourly_from_monthly_source_years /
     replay_single_year / compute_tmy_projection /
     compute_ensemble_projection): draws BOTH rainfall and PM2.5/PM10 for a
     given month from the SAME real source year, preserving the real
     dry-season correlation between low rain and high PM. Requires an
     Air4Thai station with matching multi-year PM2.5+PM10 history --
     checked live for all 5 of app.py's site presets and only true at
     Bangkok (station 54T, National Housing Authority Dindaeng: real data
     2011-2020 + 2025, with a 2021-2024 gap on PCD's side). The other 4
     presets' nearest stations only have PM2.5 from 2025 onward -- not
     enough years to pick a same-year (rain, PM) pair from.

  2. Weather TMY + CAMS representative-year PM replay
     (compute_monthly_rain_stats / select_representative_weather_years /
     select_pm_representative_years / fill_aq_with_representative_years /
     compute_weather_tmy_projection): weather (rain/GHI/DNI/DHI/temp/wind,
     all together from one real source year per month) paired with REAL
     hourly PM2.5/PM10 replayed from a representative source year per
     month -- same replay mechanics as strategy 1, but the PM source is
     Open-Meteo's own CAMS Global historical AQ archive instead of
     Air4Thai/PCD. No Air4Thai dependency -- works at any site Open-Meteo
     covers (verified live for all 5 presets). CAMS's real (non-null) PM
     coverage only starts ~August 2022 outside Europe though (see
     weather_client.compute_monthly_pm_climatology), so
     select_pm_representative_years picks each month's source year from
     that narrower window, not the full weather-TMY window -- still uses
     the SAME rain_threshold_hours statistic as
     select_representative_weather_years to pick it (not a separate
     PM-based criterion), preserving the dry-season rain/PM correlation
     this module cares about. Use this for the 4 presets strategy 1
     doesn't have enough Air4Thai station history for.

     An earlier version of this strategy (compute_monthly_pm_climatology /
     fill_aq_with_pm_climatology, still in weather_client.py/below for
     reference) broadcast one flat CAMS monthly AVERAGE across every hour
     of a month instead of replaying a real year -- simpler, but wrong for
     a "typical year": it collapses real day-to-day PM variation to a
     constant, which reads as an artificial staircase in any chart plotted
     across the fill boundary. Superseded by the replay approach above,
     which was already sitting right there in strategy 1's machinery.

Both strategies share one rule: never pick rainfall and PM independently
from DIFFERENT source years/products for the same date. Mixing them (e.g.
a low-rain month from one year paired with a high-PM month from another,
or PVGIS TMY weather paired with Open-Meteo analog-year PM) destroys the
real correlation between the two -- dry season is low-rain AND high-PM
together, for the same physical reason.
"""

from __future__ import annotations

import pandas as pd

from src.air4thai_client import get_historical_pm10, get_historical_pm25
from src.soiling_pv_model import (
    HOURLY_CLEANING_THRESHOLD_MM,
    compute_expected_generation,
    compute_soiling_ratio,
)
from src.weather_client import get_historical_air_quality, get_historical_weather

EARLIEST_YEAR = 2011  # Air4Thai/PCD historical PM archive start. air4thai_client
# handles two different CKAN schemas transparently (2011-2020 "wide": one
# column per station; ~2025 "long": station_code/measured_value rows) -- see
# air4thai_client._fetch_year_records. Verified live 2026-08-14: 2021-2023
# (PM10) / 2021-2024 (PM2.5) are listed in the catalog but 404 on datastore
# query -- a real gap on PCD's side, not a parsing issue -- so
# compute_monthly_stats below will have a multi-year hole in the middle of
# its range; callers should not assume every year in [EARLIEST_YEAR, last
# complete year] actually has data.


def compute_monthly_stats(
    lat: float,
    lon: float,
    station_code: str,
    year_start: int = EARLIEST_YEAR,
    year_end: int | None = None,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
) -> pd.DataFrame:
    """Per (year, month): count of hours with rainfall_mm >= cleaning_threshold_mm
    (the statistic that actually drives HSU's binary reset -- see
    soiling_pv_model.py's HOURLY_CLEANING_THRESHOLD_MM) and mean PM2.5
    (ug/m3), both from real historical data for that exact year/month.

    Returns a DataFrame indexed by MultiIndex (year, month), columns
    rain_threshold_hours / pm25_mean. Only rows where BOTH weather and PM2.5
    have data for that year are included -- a year missing one side can't
    contribute a same-year (rain, PM) pair.

    year_end defaults to last year: Air4Thai/PCD only publishes complete
    calendar years (see air4thai_client.get_historical_pm25), so the
    current, still-in-progress year is excluded by default.
    """
    if year_end is None:
        year_end = pd.Timestamp.now().year - 1

    rows = []
    for year in range(year_start, year_end + 1):
        start_date, end_date = f"{year}-01-01", f"{year}-12-31"
        weather_hourly = get_historical_weather(lat, lon, start_date, end_date)
        pm25_daily = get_historical_pm25(station_code, start_date, end_date)
        if weather_hourly.empty or pm25_daily.empty:
            continue

        crossed = weather_hourly["rainfall_mm"] >= cleaning_threshold_mm
        rain_hours_by_month = crossed.groupby(weather_hourly.index.month).sum()
        pm25_by_month = pm25_daily.groupby(pm25_daily.index.month).mean()

        for month in range(1, 13):
            if month not in rain_hours_by_month.index or month not in pm25_by_month.index:
                continue
            rows.append({
                "year": year,
                "month": month,
                "rain_threshold_hours": float(rain_hours_by_month.loc[month]),
                "pm25_mean": float(pm25_by_month.loc[month]),
            })

    return pd.DataFrame(rows).set_index(["year", "month"]).sort_index()


def select_representative_years(monthly_stats: pd.DataFrame) -> dict[int, int]:
    """For each calendar month, the year whose (rain_threshold_hours,
    pm25_mean) jointly deviate least from that month's long-term median
    across all available years -- for approach (a), a single synthetic
    typical year (one representative year per month).

    Both metrics are normalized to a fraction-of-median deviation before
    summing, since rain_threshold_hours (a small integer count) and
    pm25_mean (tens of ug/m3) sit on unrelated scales -- summing raw
    deviations would let whichever metric has the bigger numbers dominate
    the selection regardless of which one actually varies more.

    Returns {month: representative_year}, one entry per month with data.
    """
    representative = {}
    for month, group in monthly_stats.groupby(level="month"):
        rain_median = group["rain_threshold_hours"].median()
        pm25_median = group["pm25_mean"].median()

        rain_dev = (
            (group["rain_threshold_hours"] - rain_median).abs() / rain_median
            if rain_median > 0
            else (group["rain_threshold_hours"] - rain_median).abs()
        )
        pm25_dev = (group["pm25_mean"] - pm25_median).abs() / pm25_median
        combined_dev = rain_dev + pm25_dev

        best_year = combined_dev.index.get_level_values("year")[combined_dev.to_numpy().argmin()]
        representative[int(month)] = int(best_year)

    return representative


def ensemble_years(monthly_stats: pd.DataFrame) -> list[int]:
    """Years with all 12 months present -- the safe candidate set for
    approach (b), an ensemble of full historical years (a year missing even
    one month can't be replayed as a complete year)."""
    month_counts = monthly_stats.groupby(level="year").size()
    return sorted(int(y) for y in month_counts[month_counts == 12].index)


def compute_monthly_rain_stats(
    lat: float,
    lon: float,
    year_start: int = EARLIEST_YEAR,
    year_end: int | None = None,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
) -> pd.DataFrame:
    """Weather-only version of compute_monthly_stats -- no Air4Thai/PCD
    dependency, so it works at any site Open-Meteo covers (its historical
    archive goes back decades everywhere -- verified live to 1990 for
    Bangkok -- unlike Air4Thai's per-station, per-site coverage, which
    turned out deep enough for the joint rain+PM criterion at only 1 of the
    5 site presets).

    Use this + select_representative_weather_years to pick representative
    years for weather ALONE, pairing the result with
    weather_client.compute_monthly_pm_climatology (a CAMS-based monthly PM
    average, also available everywhere) instead of Air4Thai PM2.5/PM10 --
    see fill_hourly_with_weather_tmy_and_pm_climatology.
    """
    if year_end is None:
        year_end = pd.Timestamp.now().year - 1

    rows = []
    for year in range(year_start, year_end + 1):
        weather_hourly = get_historical_weather(lat, lon, f"{year}-01-01", f"{year}-12-31")
        if weather_hourly.empty:
            continue
        crossed = weather_hourly["rainfall_mm"] >= cleaning_threshold_mm
        rain_hours_by_month = crossed.groupby(weather_hourly.index.month).sum()
        for month in range(1, 13):
            if month in rain_hours_by_month.index:
                rows.append({
                    "year": year,
                    "month": month,
                    "rain_threshold_hours": float(rain_hours_by_month.loc[month]),
                })

    return pd.DataFrame(rows).set_index(["year", "month"]).sort_index()


def select_representative_weather_years(monthly_rain_stats: pd.DataFrame) -> dict[int, int]:
    """Weather-only version of select_representative_years: for each
    calendar month, the year whose rain_threshold_hours is closest to that
    month's long-term median. No PM term (this pairs with CAMS monthly
    climatology instead of Air4Thai PM, which already carries its own
    separate seasonal shape -- see compute_monthly_pm_climatology)."""
    representative = {}
    for month, group in monthly_rain_stats.groupby(level="month"):
        median = group["rain_threshold_hours"].median()
        deviation = (group["rain_threshold_hours"] - median).abs()
        best_year = deviation.index.get_level_values("year")[deviation.to_numpy().argmin()]
        representative[int(month)] = int(best_year)
    return representative


def select_pm_representative_years(
    lat: float,
    lon: float,
    year_start: int = EARLIEST_YEAR,
    year_end: int | None = None,
) -> dict[int, int]:
    """Per-month representative year for PM replay (see
    fill_aq_with_representative_years), restricted to years where
    Open-Meteo's CAMS Global archive actually has real (non-null)
    PM2.5/PM10 -- verified live 2026-08-14 to start ~August 2022 outside
    Europe (see weather_client.compute_monthly_pm_climatology), so most of
    [year_start, year_end] will have no real PM at all despite having real
    weather.

    Picks using the SAME rain_threshold_hours statistic as
    select_representative_weather_years (not a separate PM-based
    criterion) so the chosen year's real PM still reflects that year's
    real rain pattern for the month -- preserving the dry-season rain/PM
    correlation this module's docstring cares about -- while guaranteeing,
    unlike the full weather-TMY candidate pool, that the chosen source
    year actually has real PM to replay.

    Returns {} if no year in range has any real PM coverage at all (e.g. a
    site/coordinate CAMS doesn't publish for) -- callers should treat that
    the same as any other "no source year mapped" case (see
    fill_aq_with_representative_years).
    """
    if year_end is None:
        year_end = pd.Timestamp.now().year - 1

    # Checked per (year, month), not just per year: real CAMS coverage
    # started mid-2022, so 2022 itself is a mix of real and null months --
    # a year-level check alone could still pick, say, March 2022 (still
    # null) just because December 2022 (real) makes the year "have data".
    valid_year_months = set()
    for year in range(year_start, year_end + 1):
        aq = get_historical_air_quality(lat, lon, f"{year}-01-01", f"{year}-12-31")
        # Open-Meteo returns a full-length frame even for years/months
        # before real CAMS coverage -- just with pm2_5/pm10 entirely null --
        # so `.empty` alone doesn't detect a no-coverage period; confirmed
        # live 2026-08-15 (2016-2021 come back as 8760-8784 all-null rows,
        # not zero rows).
        if aq.empty:
            continue
        months_with_data = aq.loc[aq["pm2_5"].notna()].index.month.unique()
        valid_year_months.update((year, int(month)) for month in months_with_data)
    if not valid_year_months:
        return {}

    valid_years = {year for year, _month in valid_year_months}
    rain_stats = compute_monthly_rain_stats(lat, lon, year_start=min(valid_years), year_end=year_end)
    rain_stats = rain_stats[
        [(year, month) in valid_year_months for year, month in rain_stats.index]
    ]
    return select_representative_weather_years(rain_stats)


def fill_aq_with_representative_years(
    lat: float,
    lon: float,
    pm_representative_years: dict[int, int],
    target_start: str,
    target_end: str,
) -> pd.DataFrame:
    """Real hourly pm2_5/pm10 for [target_start, target_end], replaying
    each calendar month's REAL CAMS data (Open-Meteo's historical AQ
    archive -- the same source compute_monthly_pm_climatology averages
    away) from pm_representative_years[month] (see
    select_pm_representative_years), shifted onto the target dates --
    preserves real hour-to-hour PM variation within the month, unlike
    fill_aq_with_pm_climatology's flat monthly average. Weather
    counterpart: fill_weather_with_representative_years."""
    target_start_ts = pd.Timestamp(target_start)
    target_end_ts = pd.Timestamp(target_end)

    chunks = []
    cursor = target_start_ts.replace(day=1)
    while cursor <= target_end_ts:
        target_year, target_month = cursor.year, cursor.month
        source_year = pm_representative_years.get(target_month)
        if source_year is None:
            cursor = cursor + pd.DateOffset(months=1)
            continue

        source_period = pd.Period(year=source_year, month=target_month, freq="M")
        source_start = source_period.start_time.date().isoformat()
        source_end = source_period.end_time.date().isoformat()

        aq_month = get_historical_air_quality(lat, lon, source_start, source_end)
        if aq_month.empty:
            cursor = cursor + pd.DateOffset(months=1)
            continue

        aq_month = aq_month[["pm2_5", "pm10"]].copy()
        aq_month.index = aq_month.index + pd.DateOffset(years=target_year - source_year)
        chunks.append(aq_month)
        cursor = cursor + pd.DateOffset(months=1)

    if not chunks:
        return pd.DataFrame(columns=["pm2_5", "pm10"], index=pd.DatetimeIndex([]))

    aq_hourly = pd.concat(chunks).sort_index()
    return aq_hourly[(aq_hourly.index >= target_start_ts) & (aq_hourly.index <= target_end_ts)]


def _pm_daily_to_hourly(pm25_daily: pd.Series, pm10_daily: pd.Series, index_hourly: pd.DatetimeIndex) -> pd.DataFrame:
    """Expand daily Air4Thai PM2.5/PM10 onto an hourly index (forward/back
    -filled within each day) -- compute_soiling_ratio intersects
    weather_hourly's and aq_hourly's indices, so aq_hourly needs a real
    entry at every hour, not just one per day, or every hour except the
    stamped one would fall out of the intersection."""
    dates = index_hourly.normalize()
    pm2_5 = pm25_daily.reindex(dates).ffill().bfill()
    pm10 = pm10_daily.reindex(dates).ffill().bfill()
    return pd.DataFrame({"pm2_5": pm2_5.to_numpy(), "pm10": pm10.to_numpy()}, index=index_hourly)


def fill_hourly_from_monthly_source_years(
    lat: float,
    lon: float,
    station_code: str,
    monthly_source_years: dict[int, int],
    target_start: str,
    target_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (weather_hourly, aq_hourly) for [target_start, target_end] by
    replaying, for each calendar month in that range, the REAL data from
    monthly_source_years[month] (shifted onto the target dates) -- a drop-in
    alternative to weather_client's get_analog_year_weather/
    get_analog_year_air_quality that can use a DIFFERENT source year per
    calendar month instead of one single year for the whole range.

    Passing select_representative_years()'s output gives approach (a) (a
    synthetic typical year, one source year per month). Passing the same
    single year for all 12 months (see replay_single_year below) gives one
    member of approach (b)'s ensemble -- both go through this one function
    so the shift/expand-to-hourly mechanics live in exactly one place.

    Rain/weather comes from Open-Meteo (get_historical_weather); PM2.5/PM10
    from Air4Thai/PCD (get_historical_pm25/get_historical_pm10), expanded
    from daily to hourly. A month with no source year mapped, or whose
    source year has no weather data, is silently skipped -- callers should
    check the returned frames' coverage against [target_start, target_end]
    rather than assume it's gap-free.
    """
    target_start_ts = pd.Timestamp(target_start)
    target_end_ts = pd.Timestamp(target_end)

    weather_chunks = []
    aq_chunks = []

    cursor = target_start_ts.replace(day=1)
    while cursor <= target_end_ts:
        target_year, target_month = cursor.year, cursor.month
        source_year = monthly_source_years.get(target_month)
        if source_year is None:
            cursor = cursor + pd.DateOffset(months=1)
            continue

        source_period = pd.Period(year=source_year, month=target_month, freq="M")
        source_start = source_period.start_time.date().isoformat()
        source_end = source_period.end_time.date().isoformat()

        weather_month = get_historical_weather(lat, lon, source_start, source_end)
        if weather_month.empty:
            cursor = cursor + pd.DateOffset(months=1)
            continue
        pm25_month = get_historical_pm25(station_code, source_start, source_end)
        pm10_month = get_historical_pm10(station_code, source_start, source_end)

        year_shift = target_year - source_year
        weather_month = weather_month.copy()
        weather_month.index = weather_month.index + pd.DateOffset(years=year_shift)
        pm25_month = pm25_month.copy()
        pm25_month.index = pm25_month.index + pd.DateOffset(years=year_shift)
        pm10_month = pm10_month.copy()
        pm10_month.index = pm10_month.index + pd.DateOffset(years=year_shift)

        aq_month = _pm_daily_to_hourly(pm25_month, pm10_month, weather_month.index)

        weather_chunks.append(weather_month)
        aq_chunks.append(aq_month)
        cursor = cursor + pd.DateOffset(months=1)

    if not weather_chunks:
        empty_index = pd.DatetimeIndex([])
        empty_weather = pd.DataFrame(
            columns=["ghi", "dni", "dhi", "rainfall_mm", "temp_air", "wind_speed"], index=empty_index
        )
        empty_aq = pd.DataFrame(columns=["pm2_5", "pm10"], index=empty_index)
        return empty_weather, empty_aq

    weather_hourly = pd.concat(weather_chunks).sort_index()
    aq_hourly = pd.concat(aq_chunks).sort_index()
    # Month-boundary/leap-day shifts can land a few hours outside the exact
    # requested range -- clip back to it.
    weather_hourly = weather_hourly[(weather_hourly.index >= target_start_ts) & (weather_hourly.index <= target_end_ts)]
    aq_hourly = aq_hourly[(aq_hourly.index >= target_start_ts) & (aq_hourly.index <= target_end_ts)]
    return weather_hourly, aq_hourly


def replay_single_year(
    lat: float, lon: float, station_code: str, source_year: int, target_start: str, target_end: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One ensemble member: [target_start, target_end] replayed entirely
    from source_year (rain+PM together, preserving that year's real
    correlation between the two) -- see ensemble_years for candidate years
    and fill_hourly_from_monthly_source_years for the shared mechanics."""
    monthly_source_years = {month: source_year for month in range(1, 13)}
    return fill_hourly_from_monthly_source_years(lat, lon, station_code, monthly_source_years, target_start, target_end)


def fill_weather_with_representative_years(
    lat: float,
    lon: float,
    weather_representative_years: dict[int, int],
    target_start: str,
    target_end: str,
) -> pd.DataFrame:
    """Real weather (rain/GHI/DNI/DHI/temp/wind, all together) for
    [target_start, target_end], replaying each calendar month's REAL data
    from weather_representative_years[month] (see
    select_representative_weather_years), shifted onto the target dates.
    Weather-only, no Air4Thai/PM dependency -- see fill_aq_with_pm_climatology
    for the PM counterpart, and this module's docstring for why app.py keeps
    them on separate gap-start dates (weather's real forecast reaches 16
    days out, PM's only ~5)."""
    target_start_ts = pd.Timestamp(target_start)
    target_end_ts = pd.Timestamp(target_end)

    chunks = []
    cursor = target_start_ts.replace(day=1)
    while cursor <= target_end_ts:
        target_year, target_month = cursor.year, cursor.month
        source_year = weather_representative_years.get(target_month)
        if source_year is None:
            cursor = cursor + pd.DateOffset(months=1)
            continue

        source_period = pd.Period(year=source_year, month=target_month, freq="M")
        source_start = source_period.start_time.date().isoformat()
        source_end = source_period.end_time.date().isoformat()

        weather_month = get_historical_weather(lat, lon, source_start, source_end)
        if weather_month.empty:
            cursor = cursor + pd.DateOffset(months=1)
            continue

        weather_month = weather_month.copy()
        weather_month.index = weather_month.index + pd.DateOffset(years=target_year - source_year)
        chunks.append(weather_month)
        cursor = cursor + pd.DateOffset(months=1)

    if not chunks:
        return pd.DataFrame(
            columns=["ghi", "dni", "dhi", "rainfall_mm", "temp_air", "wind_speed"], index=pd.DatetimeIndex([])
        )

    weather_hourly = pd.concat(chunks).sort_index()
    return weather_hourly[(weather_hourly.index >= target_start_ts) & (weather_hourly.index <= target_end_ts)]


def fill_aq_with_pm_climatology(pm_climatology: dict[int, dict], target_start: str, target_end: str) -> pd.DataFrame:
    """SUPERSEDED by fill_aq_with_representative_years (see this module's
    docstring) -- kept for reference, not called from app.py/main_demo.py.
    Broadcasting one flat monthly average onto every hour of a month is
    the wrong shape for a "typical year": it collapses real day-to-day PM
    variation to a constant, which shows up as an artificial staircase in
    any chart plotted across the fill boundary.

    Hourly pm2_5/pm10 for [target_start, target_end], broadcasting each
    calendar month's CAMS monthly average (see
    weather_client.compute_monthly_pm_climatology) across every hour of
    that month. No API calls -- pm_climatology is precomputed, and this
    just repeats it onto an hourly index."""
    index = pd.date_range(target_start, pd.Timestamp(target_end) + pd.Timedelta(hours=23), freq="h")
    pm2_5 = [pm_climatology.get(ts.month, {}).get("pm2_5") for ts in index]
    pm10 = [pm_climatology.get(ts.month, {}).get("pm10") for ts in index]
    return pd.DataFrame({"pm2_5": pm2_5, "pm10": pm10}, index=index)


def fill_hourly_with_weather_tmy_and_pm_climatology(
    lat: float,
    lon: float,
    weather_representative_years: dict[int, int],
    pm_climatology: dict[int, dict],
    target_start: str,
    target_end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience wrapper combining fill_weather_with_representative_years +
    fill_aq_with_pm_climatology (the superseded flat-climatology PM fill --
    swap in fill_aq_with_representative_years for the current approach)
    over the SAME window -- use this when
    weather_hourly and aq_hourly are needed together for one model run (e.g.
    compute_weather_tmy_projection below). app.py's actual pipeline calls
    the two fill_* functions separately instead, each with its own gap-start
    date, since weather's real forecast (16 days) outlasts PM's (~5 days)
    and this wrapper always fills both from the same start.
    """
    weather_hourly = fill_weather_with_representative_years(lat, lon, weather_representative_years, target_start, target_end)
    aq_hourly = fill_aq_with_pm_climatology(pm_climatology, target_start, target_end)
    if not weather_hourly.empty:
        # Reindex onto weather_hourly's exact index (small clock/DST-ish
        # mismatches between the two independently-built hourly indices
        # would otherwise drop hours from compute_soiling_ratio's
        # index.intersection).
        aq_hourly = aq_hourly.reindex(weather_hourly.index).ffill().bfill()
    return weather_hourly, aq_hourly


def compute_weather_tmy_projection(
    lat: float,
    lon: float,
    weather_representative_years: dict[int, int],
    pm_climatology: dict[int, dict],
    target_start: str,
    target_end: str,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
    system_efficiency: float = 1.0,
    gamma_pdc: float = -0.0040,
) -> tuple[pd.Series, pd.Series]:
    """The universal fallback projection (real weather TMY + CAMS monthly PM
    climatology, see fill_hourly_with_weather_tmy_and_pm_climatology) -- use
    this instead of compute_tmy_projection/compute_ensemble_projection when
    the site's nearest Air4Thai station doesn't have deep enough matched
    PM2.5+PM10 history (check via compute_monthly_stats/ensemble_years
    first)."""
    weather_hourly, aq_hourly = fill_hourly_with_weather_tmy_and_pm_climatology(
        lat, lon, weather_representative_years, pm_climatology, target_start, target_end
    )
    soiling_ratio = compute_soiling_ratio(
        weather_hourly, aq_hourly, cleaning_threshold_mm=cleaning_threshold_mm, tilt=tilt
    )
    expected_generation = compute_expected_generation(
        weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
        system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
    )
    return soiling_ratio, expected_generation


def compute_tmy_projection(
    lat: float,
    lon: float,
    station_code: str,
    representative_years: dict[int, int],
    target_start: str,
    target_end: str,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
    system_efficiency: float = 1.0,
    gamma_pdc: float = -0.0040,
) -> tuple[pd.Series, pd.Series]:
    """Approach (a): one deterministic soiling_ratio/expected_generation run
    using the joint-typical year from select_representative_years. Cheaper
    than compute_ensemble_projection (one model run instead of N) but loses
    the spread/uncertainty an ensemble preserves -- see this module's
    docstring for when TMY vs ensemble is the better fit."""
    weather_hourly, aq_hourly = fill_hourly_from_monthly_source_years(
        lat, lon, station_code, representative_years, target_start, target_end
    )
    soiling_ratio = compute_soiling_ratio(
        weather_hourly, aq_hourly, cleaning_threshold_mm=cleaning_threshold_mm, tilt=tilt
    )
    expected_generation = compute_expected_generation(
        weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
        system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
    )
    return soiling_ratio, expected_generation


def compute_ensemble_projection(
    lat: float,
    lon: float,
    station_code: str,
    years: list[int],
    target_start: str,
    target_end: str,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
    system_efficiency: float = 1.0,
    gamma_pdc: float = -0.0040,
) -> tuple[pd.Series, pd.Series]:
    """Approach (b): run compute_soiling_ratio + compute_expected_generation
    once per year in `years` (see ensemble_years), each independently
    replayed onto the SAME [target_start, target_end] window via
    replay_single_year, then average both results day-by-day across
    members. Averaging happens AFTER modeling each year separately (not on
    the raw rain/PM inputs) -- see soiling_pv_model.py's
    HOURLY_CLEANING_THRESHOLD_MM docstring for why averaging raw rainfall
    first would wash out the hourly threshold HSU actually resets on.

    Raises ValueError if no year in `years` produced usable data for this
    range (e.g. all requested years fall in a gap -- see this module's
    EARLIEST_YEAR note on the 2021-2023/2024 PCD catalog gap).
    """
    soiling_ratio_members = []
    expected_generation_members = []
    for year in years:
        weather_hourly, aq_hourly = replay_single_year(lat, lon, station_code, year, target_start, target_end)
        if weather_hourly.empty or aq_hourly.empty:
            continue
        soiling_ratio_members.append(
            compute_soiling_ratio(weather_hourly, aq_hourly, cleaning_threshold_mm=cleaning_threshold_mm, tilt=tilt)
        )
        expected_generation_members.append(
            compute_expected_generation(
                weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth,
                system_efficiency=system_efficiency, gamma_pdc=gamma_pdc,
            )
        )

    if not soiling_ratio_members:
        raise ValueError(f"no usable ensemble members among years={years} for [{target_start}, {target_end}]")

    soiling_ratio = pd.concat(soiling_ratio_members, axis=1).mean(axis=1)
    expected_generation = pd.concat(expected_generation_members, axis=1).mean(axis=1)
    soiling_ratio.name = "soiling_ratio"
    expected_generation.name = "expected_generation_kwh"
    return soiling_ratio, expected_generation


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    bangkok_lat, bangkok_lon = 13.7563, 100.5018
    # Nearest-station selection (air4thai_client.find_nearest_station) picks
    # whichever station is geographically closest as of the CURRENT
    # real-time list -- but the historical CKAN archive's station network
    # grew from 1 station (2011) to 100+ (2025), so "nearest today" is often
    # a station with only a few years of history. Verified live 2026-08-14:
    # station 54T (National Housing Authority Dindaeng, ~5.5km from this
    # Bangkok reference point) is one of the few reporting since 2011 --
    # hardcoded here for the demo rather than using find_nearest_station,
    # which would silently pick a shorter-history station instead.
    station_code = "54T"
    print(f"Using station {station_code} (National Housing Authority Dindaeng) for PM2.5/PM10")

    print("\n=== compute_monthly_stats (first run is slow: ~15 years x weather+PM2.5 API calls) ===")
    stats = compute_monthly_stats(bangkok_lat, bangkok_lon, station_code)
    years_present = sorted(stats.index.get_level_values("year").unique())
    print(f"{len(stats)} (year, month) rows, years present: {years_present}")

    print("\n=== select_representative_years (one year per calendar month) ===")
    representative = select_representative_years(stats)
    for month in range(1, 13):
        if month in representative:
            year = representative[month]
            row = stats.loc[(year, month)]
            print(f"  month {month:2d} -> {year}  "
                  f"(rain_threshold_hours={row['rain_threshold_hours']:.0f}, pm25_mean={row['pm25_mean']:.1f})")

    print("\n=== ensemble_years (years with all 12 months present) ===")
    years = ensemble_years(stats)
    print(f"{len(years)} usable ensemble years: {years}")

    # Simulate the "beyond forecast horizon" fill window: a 30-day stretch
    # starting where Open-Meteo's real forecast would run out.
    from datetime import date, timedelta
    horizon_start = date.today() + timedelta(days=17)
    horizon_end = horizon_start + timedelta(days=29)
    target_start, target_end = horizon_start.isoformat(), horizon_end.isoformat()
    print(f"\nProjection window: {target_start} .. {target_end}")

    system_kwargs = dict(kwp=100.0, dc_ac_ratio=1.2, tilt=10.0, azimuth=180.0, system_efficiency=0.88)

    print("\n=== compute_tmy_projection (approach a: single synthetic typical year) ===")
    tmy_sr, tmy_gen = compute_tmy_projection(
        bangkok_lat, bangkok_lon, station_code, representative, target_start, target_end, **system_kwargs
    )
    print(f"soiling_ratio: {len(tmy_sr)} days, mean={tmy_sr.mean():.4f}, min={tmy_sr.min():.4f}")
    print(f"expected_generation_kwh: mean/day={tmy_gen.mean():.1f}")

    print("\n=== compute_ensemble_projection (approach b: average of full-year replays) ===")
    ens_sr, ens_gen = compute_ensemble_projection(
        bangkok_lat, bangkok_lon, station_code, years, target_start, target_end, **system_kwargs
    )
    print(f"soiling_ratio: {len(ens_sr)} days, mean={ens_sr.mean():.4f}, min={ens_sr.min():.4f}")
    print(f"expected_generation_kwh: mean/day={ens_gen.mean():.1f}")

    print("\n=== day-by-day comparison (TMY vs ensemble mean) ===")
    compare = pd.DataFrame({"tmy_sr": tmy_sr, "ensemble_sr": ens_sr}).dropna()
    print(compare.head(15))
