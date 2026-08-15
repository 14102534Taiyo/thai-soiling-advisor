"""PV Physics module: soiling ratio (pvlib.soiling.hsu) and clean-panel
expected generation (pvlib PVWatts chain). See docs/interfaces.md."""

import numpy as np
import pandas as pd
import pvlib

# pvlib's own HSU default ({'2_5': 0.0009, '10': 0.004} m/s) produces a
# soiling rate of ~0.017%/day against real Bangkok PM/rainfall (Open-Meteo,
# 2026) -- 6-20x lower than published Southeast Asian tropical soiling
# studies (Singapore ~0.057%/day over 35 days; Malaysia ~0.15-0.20%/day
# over 1 month; Thai dry-season optical transmission loss >40% at 11 PV
# plants, IOPscience 10.35848/1347-4065/addb54).
#
# IMPORTANT: the scale factor must be fit against DRY-SEASON PM, not
# whatever period happens to be "now". Bangkok's real PM2.5 averages
# ~10 ug/m3 in rainy season (May-Sep) vs ~30 ug/m3 in dry season
# (Oct-Apr) -- a first attempt at this calibration accidentally used a
# rainy-season window (mean PM2.5 ~10.5 ug/m3) to hit the target rate,
# which inflated deposition_velocity to compensate for low PM and would
# have overshot badly once real dry-season PM (3x higher) hit the model.
# Fit instead against a December (dry-season) window: 10x scale-up lands
# the 30-day dry-season rate at ~0.17%/day (matching the Malaysia study),
# and the SAME constant naturally gives ~0.12%/day in the rainy-season
# window purely because rainy-season PM is lower in the input data --
# deposition_velocity itself is a physical stickiness constant and
# shouldn't need to vary by season; the input PM should carry that.
# Literature-anchored proxy, NOT a fit to a real Thai site's measured
# output (no such ground truth exists yet; see README "Known limitations").
LITERATURE_CALIBRATED_DEPO_VELOC = {"2_5": 0.0009 * 10, "10": 0.004 * 10}


# Research-informed hourly rain-cleaning threshold. A fixed daily total
# (e.g. 6mm/day) can't distinguish a genuine washing downpour from the same
# total spread thin as all-day drizzle -- Norde Santos et al. 2024 (Solar
# RRL, doi:10.1002/solr.202400551) found a ~1.0 mm/hour threshold gives the
# best match to observed natural-cleaning events, and reports <0.2mm can
# make soiling WORSE (mud-spotting) rather than clean it. pvlib's own HSU
# default rain_accum_period is 1 hour (recommended range: 1h-24h) -- this
# project originally ran it at 1-day resolution purely because the pipeline
# resampled weather/AQ to daily before this function ever saw it; that
# discarded real hourly rain-intensity data Open-Meteo already provides.
HOURLY_CLEANING_THRESHOLD_MM = 1.0


def compute_soiling_ratio(
    weather_hourly: pd.DataFrame,
    aq_hourly: pd.DataFrame,
    cleaning_threshold_mm: float = HOURLY_CLEANING_THRESHOLD_MM,
    tilt: float = 10.0,
    depo_veloc: dict | None = None,
) -> pd.Series:
    """Daily soiling ratio (0-1, 1=clean) via pvlib.soiling.hsu, computed at
    HOURLY resolution (rain_accum_period=1h) then resampled to daily
    (end-of-day value) for compatibility with the rest of the pipeline.
    Index of the inputs must be hourly; the intersection of
    weather_hourly/aq_hourly timestamps is used.

    cleaning_threshold_mm is now a PER-HOUR threshold (mm of rain within a
    single hour), not per-day -- see HOURLY_CLEANING_THRESHOLD_MM above.
    A day is a "cleaning day" if ANY hour within it crosses this threshold,
    which is a materially different (more accurate, per the cited
    research) criterion than the old daily-total >= 6mm check.

    depo_veloc is additive to the documented signature. Leaving it unset
    (or passing None) now uses LITERATURE_CALIBRATED_DEPO_VELOC (see
    above), not pvlib's raw default -- pass pvlib's own
    {'2_5': 0.0009, '10': 0.004} explicitly to compare against the
    uncalibrated baseline.
    """
    if depo_veloc is None:
        depo_veloc = LITERATURE_CALIBRATED_DEPO_VELOC
    common_index = weather_hourly.index.intersection(aq_hourly.index).sort_values()

    rainfall = weather_hourly.loc[common_index, "rainfall_mm"].astype(float).fillna(0.0)
    # pvlib 0.15.2's hsu docstring specifies pm2_5/pm10 in [g/m^3], while
    # ambient monitoring (and aq_hourly per interfaces.md) reports ug/m^3.
    # Skipping this 1e-6 conversion saturates the model at its floor
    # (SR ~= 0.6563) on day one for any realistic urban PM level.
    pm2_5 = aq_hourly.loc[common_index, "pm2_5"].astype(float).ffill().bfill() * 1e-6
    pm10 = aq_hourly.loc[common_index, "pm10"].astype(float).ffill().bfill() * 1e-6

    soiling_ratio_hourly = pvlib.soiling.hsu(
        rainfall=rainfall,
        cleaning_threshold=cleaning_threshold_mm,
        surface_tilt=tilt,
        pm2_5=pm2_5,
        pm10=pm10,
        depo_veloc=depo_veloc,
        rain_accum_period=pd.Timedelta(hours=1),
    )
    soiling_ratio = soiling_ratio_hourly.resample("D").last()
    soiling_ratio.name = "soiling_ratio"
    return soiling_ratio


def _compute_hourly_pac_kw(
    weather_hourly: pd.DataFrame,
    lat: float,
    lon: float,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    gamma_pdc: float,
) -> pd.Series:
    """Hourly AC power (kW), CLEAN panel, no system_efficiency/soiling
    derate applied -- the shared PVWatts-chain computation behind both
    compute_expected_generation (daily kWh) and
    compute_expected_generation_hourly (within-day kW shape)."""
    location = pvlib.location.Location(lat, lon)

    # weather_hourly's DatetimeIndex is tz-naive LOCAL time (interfaces.md),
    # but pvlib's solar position code treats a naive index as UTC ("Must be
    # localized or UTC will be assumed" - Location.get_solarposition). Left
    # uncorrected, sun position would be ~7h out of phase with Thailand's
    # clock. Shift by a longitude-derived fixed UTC offset before computing
    # solar position, then relabel back onto the original local index.
    utc_offset_hours = round(lon / 15)
    times_utc = pd.DatetimeIndex(
        weather_hourly.index - pd.Timedelta(hours=utc_offset_hours), tz="UTC"
    )
    solpos = location.get_solarposition(times_utc)
    solpos.index = weather_hourly.index

    dni_extra = pvlib.irradiance.get_extra_radiation(weather_hourly.index)

    ghi = weather_hourly["ghi"].clip(lower=0)
    dni = weather_hourly["dni"].clip(lower=0)
    dhi = weather_hourly["dhi"].clip(lower=0)

    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=dhi,
        dni_extra=dni_extra,
        model="haydavies",
    )
    poa_global = poa["poa_global"].clip(lower=0)

    # pvlib 0.15.2 has no `temperature.pvwatts_cell` (NREL's PVWatts thermal
    # sub-model isn't implemented in this version). Faiman is used as a
    # generic, parameter-light substitute -- only needs poa/temp_air/wind
    # speed, no module-specific database lookup.
    cell_temp = pvlib.temperature.faiman(
        poa_global=poa_global,
        temp_air=weather_hourly["temp_air"],
        wind_speed=weather_hourly["wind_speed"],
    )

    pdc0_array = kwp * 1000.0

    pdc = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=poa_global,
        temp_cell=cell_temp,
        pdc0=pdc0_array,
        gamma_pdc=gamma_pdc,
    )

    pdc0_inverter = pdc0_array / dc_ac_ratio
    pac = pvlib.inverter.pvwatts(pdc=pdc, pdc0=pdc0_inverter).clip(lower=0)
    return pac / 1000.0


def compute_expected_generation(
    weather_hourly: pd.DataFrame,
    lat: float,
    lon: float,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    system_efficiency: float = 1.0,
    gamma_pdc: float = -0.0040,
) -> pd.Series:
    """Daily expected AC energy (kWh), CLEAN panel (no soiling derate),
    via a manual pvlib PVWatts chain. Index = calendar dates.

    system_efficiency (0-1] is a flat multiplier for real-world losses this
    chain doesn't otherwise model -- wiring/connections, module mismatch,
    shading, degradation, inverter/system downtime. Defaults to 1.0 (none)
    for backward compatibility. Soiling is NOT part of this -- it's already
    computed separately by compute_soiling_ratio and applied downstream in
    economics.py; folding it in here would double-count it.

    gamma_pdc is the module's power temperature coefficient (fraction/degC,
    negative -- power drops as cells heat above the 25 degC STC reference).
    Defaults to -0.0040 (-0.40%/degC), a generic crystalline-silicon value;
    real panels range roughly -0.0025 to -0.0050/degC depending on
    technology/brand -- matters more in Thailand's heat, where cell temps
    routinely exceed 25 degC by 5-10 degC.
    """
    power_kw = _compute_hourly_pac_kw(weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth, gamma_pdc)
    energy_kwh = (power_kw * system_efficiency).resample("D").sum()
    energy_kwh.name = "expected_generation_kwh"
    return energy_kwh


def compute_expected_generation_hourly(
    weather_hourly: pd.DataFrame,
    lat: float,
    lon: float,
    kwp: float,
    dc_ac_ratio: float,
    tilt: float,
    azimuth: float,
    system_efficiency: float = 1.0,
    gamma_pdc: float = -0.0040,
) -> pd.Series:
    """Hourly expected AC power (kW), CLEAN panel -- same PVWatts chain and
    parameters as compute_expected_generation, but without the daily
    resample. For charts that need the within-day shape (e.g. a
    power-vs-irradiance profile for a single day) rather than daily
    totals. Soiling is still not applied here, same rationale as
    compute_expected_generation."""
    power_kw = _compute_hourly_pac_kw(weather_hourly, lat, lon, kwp, dc_ac_ratio, tilt, azimuth, gamma_pdc)
    power_kw = power_kw * system_efficiency
    power_kw.name = "expected_power_kw"
    return power_kw


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    n_days = 30

    daily_index = pd.date_range("2024-01-01", periods=n_days, freq="D")
    hourly_index = pd.date_range("2024-01-01", periods=n_days * 24, freq="h")

    # Rain scenarios to exercise the hourly threshold (default 1.0mm/hour):
    # day 9 -- 15mm dumped in a single hour (a real storm; should clean).
    # day 19 -- 3mm spread evenly across 12 hours, 0.25mm/hour each (never
    #           crosses the hourly threshold; should NOT clean, unlike the
    #           old daily-total check which would have treated 3mm >= a
    #           lowered threshold as a clean event).
    # day 24 -- 20mm in a single hour (storm; should clean).
    rainfall_mm = np.zeros(len(hourly_index))
    rainfall_mm[9 * 24 + 14] = 15.0  # day 9, 14:00
    rainfall_mm[19 * 24 + 8: 19 * 24 + 20] = 3.0 / 12  # day 19, 08:00-20:00 drizzle
    rainfall_mm[24 * 24 + 15] = 20.0  # day 24, 15:00
    weather_hourly_rain = pd.DataFrame({"rainfall_mm": rainfall_mm}, index=hourly_index)

    pm2_5_daily = rng.uniform(20, 40, n_days)
    pm10_daily = pm2_5_daily + rng.uniform(10, 30, n_days)
    aq_hourly = pd.DataFrame(
        {"pm2_5": np.repeat(pm2_5_daily, 24), "pm10": np.repeat(pm10_daily, 24)},
        index=hourly_index,
    )

    hours = hourly_index.hour + hourly_index.minute / 60.0
    daylight = np.clip(np.sin(np.pi * (hours - 6) / 12), 0, None)
    cloud_noise = rng.uniform(0.75, 1.0, len(hourly_index))
    ghi = 950 * daylight**1.2 * cloud_noise
    dni = 750 * daylight**1.5 * cloud_noise
    dhi = ghi - dni * np.sin(np.pi / 2 - np.pi * np.abs(hours - 12) / 24)
    dhi = np.clip(dhi, 0, ghi)
    temp_air = 27 + 6 * np.sin(np.pi * (hours - 9) / 12) + rng.uniform(-0.5, 0.5, len(hourly_index))
    wind_speed = rng.uniform(1.0, 3.0, len(hourly_index))

    weather_hourly = pd.DataFrame(
        {
            "ghi": ghi,
            "dni": dni,
            "dhi": dhi,
            "temp_air": temp_air,
            "wind_speed": wind_speed,
        },
        index=hourly_index,
    )

    soiling_ratio = compute_soiling_ratio(weather_hourly_rain, aq_hourly)
    expected_generation_kwh = compute_expected_generation(
        weather_hourly,
        lat=13.7563,
        lon=100.5018,
        kwp=5.0,
        dc_ac_ratio=1.2,
        tilt=10.0,
        azimuth=180.0,
    )

    rain_daily_total = weather_hourly_rain["rainfall_mm"].resample("D").sum()
    print("=== soiling_ratio ===")
    print(soiling_ratio.describe())
    print(soiling_ratio.head(15))
    print("\nrain days (index, daily_total_rainfall_mm, soiling_ratio) -- day 9/24 are "
          "single-hour storms (should clean), day 19 is 3mm drizzle over 12h (should NOT clean):")
    for i in [9, 10, 19, 20, 24, 25]:
        d = daily_index[i]
        print(d.date(), rain_daily_total.loc[d], soiling_ratio.loc[d])

    print("\n=== expected_generation_kwh ===")
    print(expected_generation_kwh.describe())
    print(expected_generation_kwh.head(10))
    print("\nkWh/kWp/day:", (expected_generation_kwh / 5.0).mean())
