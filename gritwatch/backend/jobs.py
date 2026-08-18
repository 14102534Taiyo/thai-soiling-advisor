"""compute_dashboard: the whole pipeline, one pure function.

Runs src.pipeline.run_pipeline (the same pipeline app.py's Streamlit UI
drives) and maps its return dict onto DashboardPayload per the field-mapping
table in GritWatch Dashboard handoff/.../README.md. Does not touch src/ --
only wires its existing, calibrated functions together.

Stateless on purpose: takes a config, returns a payload, touches no shared
storage. Each visitor's edits stay in their own browser (see
POST /api/compute in routes.py) rather than being written anywhere shared.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from gritwatch.backend.models import (
    AcrBlock,
    AcrPoint,
    DashboardMeta,
    DashboardPayload,
    GenerationBlock,
    GenerationDay,
    GenerationHour,
    Recommendation,
    SiteConfig,
    SoilingBlock,
    SoilingDay,
    SystemEvent,
    WeatherToday,
)
from gritwatch.backend.pipeline import RESET_RATIO_THRESHOLD, run_pipeline
from gritwatch.backend.utils import to_date as _to_date
from src.tmd_client import get_rain_corrections_for_site


def _build_soiling_and_generation(pipeline_result: dict, config: SiteConfig):
    soiling_ratio = pipeline_result["soiling_ratio"]
    expected_generation = pipeline_result["expected_generation"]
    weather_daily = pipeline_result["weather_daily"]
    aq_daily = pipeline_result["aq_daily"]
    weather_hourly = pipeline_result["weather_hourly"]
    last_clean_date = pipeline_result["last_clean_date"]
    weather_forecast_end = pipeline_result["weather_forecast_end"]
    aq_forecast_end = pipeline_result["aq_forecast_end"]

    real_data_cutoff = min(weather_forecast_end, aq_forecast_end)
    max_hourly_rain_daily = weather_hourly["rainfall_mm"].resample("D").max()

    series_index = soiling_ratio.loc[last_clean_date:].index
    soiling_days: list[SoilingDay] = []
    generation_days: list[GenerationDay] = []
    for ts in series_index:
        day = ts.date()
        sr = float(soiling_ratio.loc[ts])
        gen_clean = float(expected_generation.reindex([ts]).iloc[0]) if ts in expected_generation.index else 0.0
        is_real = day <= real_data_cutoff

        soiling_days.append(SoilingDay(
            date=day,
            soiling_ratio=sr,
            pm2_5=float(aq_daily["pm2_5"].reindex([ts]).iloc[0]) if ts in aq_daily.index else 0.0,
            pm10=float(aq_daily["pm10"].reindex([ts]).iloc[0]) if ts in aq_daily.index else 0.0,
            rainfall_mm=float(weather_daily["rainfall_mm"].reindex([ts]).iloc[0]) if ts in weather_daily.index else 0.0,
            max_hourly_rain_mm=float(max_hourly_rain_daily.reindex([ts]).iloc[0]) if ts in max_hourly_rain_daily.index else 0.0,
            is_rain_reset=sr >= RESET_RATIO_THRESHOLD,
            is_real_data=is_real,
        ))
        generation_days.append(GenerationDay(
            date=day,
            expected_kwh_clean=gen_clean,
            expected_kwh_soiled=gen_clean * sr,
            is_real_data=is_real,
        ))

    return soiling_days, generation_days


def _build_weather_today(pipeline_result: dict, today: date) -> WeatherToday:
    """Today's raw readings, as opposed to the derived soiling_ratio_today.
    weather_daily/aq_daily are already resampled to daily means/sums inside
    run_pipeline; rain_today/rain_today_max_hourly are already scalars
    computed there too, reused here rather than re-deriving them."""
    today_ts = pd.Timestamp(today)
    weather_daily = pipeline_result["weather_daily"]
    aq_daily = pipeline_result["aq_daily"]
    weather_row = weather_daily.loc[today_ts] if today_ts in weather_daily.index else None
    aq_row = aq_daily.loc[today_ts] if today_ts in aq_daily.index else None

    return WeatherToday(
        rainfall_mm=pipeline_result["rain_today"] or 0.0,
        max_hourly_rain_mm=pipeline_result["rain_today_max_hourly"] or 0.0,
        pm2_5=pipeline_result["pm25_today"] or 0.0,
        pm10=float(aq_row["pm10"]) if aq_row is not None and pd.notna(aq_row["pm10"]) else 0.0,
        temp_air_c=float(weather_row["temp_air"]) if weather_row is not None and pd.notna(weather_row["temp_air"]) else 0.0,
        wind_speed_ms=float(weather_row["wind_speed"]) if weather_row is not None and pd.notna(weather_row["wind_speed"]) else 0.0,
        ghi_avg_wm2=float(weather_row["ghi"]) if weather_row is not None and pd.notna(weather_row["ghi"]) else 0.0,
    )


def _build_generation_hourly(pipeline_result: dict, today: date) -> list[GenerationHour]:
    power = pipeline_result["expected_power_kw_today"]
    weather_hourly = pipeline_result["weather_hourly"]
    if power is None:
        return []
    today_ghi = weather_hourly.loc[weather_hourly.index.date == today, "ghi"]
    hours = []
    for ts, power_kw in power.items():
        ghi = float(today_ghi.reindex([ts]).iloc[0]) if ts in today_ghi.index else 0.0
        hours.append(GenerationHour(timestamp=ts.to_pydatetime(), power_kw=float(power_kw), ghi=ghi))
    return hours


def _build_recommendation(pipeline_result: dict, today: date) -> Recommendation:
    result = pipeline_result["result"]
    last_clean_date = pipeline_result["last_clean_date"]
    days_since_clean = pipeline_result["days_since_clean"]
    min_T = max(1, days_since_clean)

    if result is None:
        # insufficient_data: dry stretch under 2 days, the optimizer did not
        # run -- every other field here is a placeholder the UI must not
        # render (see data_contract.ts's Recommendation.insufficient_data).
        return Recommendation(
            optimal_T=0, recommended_date=today,
            band_low_T=0, band_high_T=0, band_low_date=today, band_high_date=today,
            acr_at_optimal=0.0, savings_vs_daily_clean=0.0, savings_vs_never_clean=0.0,
            wait_for_rain=False, insufficient_data=True, min_T=min_T,
        )

    next_natural_reset_date = pipeline_result["next_natural_reset_date"]
    hits_edge = pipeline_result["hits_edge"]
    wait_for_rain = next_natural_reset_date is not None and bool(hits_edge)

    return Recommendation(
        optimal_T=result["optimal_T"],
        recommended_date=_to_date(result["recommended_date"]),
        band_low_T=result["band_low_T"],
        band_high_T=result["band_high_T"],
        band_low_date=_to_date(result["band_low_date"]),
        band_high_date=_to_date(result["band_high_date"]),
        acr_at_optimal=result["acr_at_optimal"],
        savings_vs_daily_clean=result["savings_vs_daily_clean"],
        savings_vs_never_clean=result["savings_vs_never_clean"],
        wait_for_rain=wait_for_rain,
        insufficient_data=False,
        min_T=min_T,
    )


def _build_events(pipeline_result: dict, config: SiteConfig, soiling_days: list[SoilingDay]) -> list[SystemEvent]:
    events: list[SystemEvent] = []

    for day in soiling_days:
        if day.is_rain_reset:
            events.append(SystemEvent(
                date=day.date, kind="rain_reset",
                title="ฝนล้างแผงอัตโนมัติ",
                effect=f"ฝนเกิน {config.cleaning_threshold_mm:g} มม./ชม. ในวันนี้ -- ความสกปรกรีเซ็ตเป็น 0",
            ))

    corrections = get_rain_corrections_for_site(config.lat, config.lon)
    for date_str, corrected_mm in corrections.items():
        events.append(SystemEvent(
            date=date.fromisoformat(date_str), kind="tmd_correction",
            title="แก้ไขฝนพยากรณ์ด้วยสถานี TMD",
            effect=f"สถานี TMD ยืนยันว่าฝนจริงคือ {corrected_mm:g} มม. -- ยกเลิกการล้างแผงจากฝนพยากรณ์ที่ผิด",
        ))

    if pipeline_result["pm_correction"] is not None:
        events.append(SystemEvent(
            date=date.today(), kind="pm_recalibration",
            title="ปรับเทียบค่าฝุ่นรายเดือน",
            effect="ใช้ตัวคูณปรับเทียบ CAMS Global เทียบกับสถานี Air4Thai จริงรายเดือน",
        ))

    real_cutoff = min(pipeline_result["weather_forecast_end"], pipeline_result["aq_forecast_end"])
    events.append(SystemEvent(
        date=real_cutoff + timedelta(days=1), kind="fill_boundary",
        title="สิ้นสุดข้อมูลพยากรณ์จริง",
        effect="เริ่มใช้ข้อมูลทดแทนจากปีตัวแทน (representative-year replay) หลังจากวันนี้",
    ))

    events.sort(key=lambda e: e.date, reverse=True)
    return events


def compute_dashboard(config: SiteConfig) -> DashboardPayload:
    today = date.today()
    pipeline_result = run_pipeline(
        config.lat, config.lon, config.kwp, config.dc_ac_ratio, config.tilt, config.azimuth,
        config.cleaning_threshold_mm, config.price_per_kwh, config.cleaning_cost_per_kwp,
        config.horizon_days, system_efficiency=config.system_efficiency, gamma_pdc=config.gamma_pdc,
    )

    soiling_days, generation_days = _build_soiling_and_generation(pipeline_result, config)
    weather_today = _build_weather_today(pipeline_result, today)
    hourly_today = _build_generation_hourly(pipeline_result, today)
    recommendation = _build_recommendation(pipeline_result, today)
    events = _build_events(pipeline_result, config, soiling_days)

    acr_curve_df = pipeline_result["acr_curve"]
    acr_points = (
        [AcrPoint(T=int(row.T), acr=float(row.acr), loss_cost_per_day=float(row.loss_cost_per_day),
                  clean_cost_per_day=float(row.clean_cost_per_day))
         for row in acr_curve_df.itertuples(index=False)]
        if acr_curve_df is not None else []
    )

    last_clean_date = _to_date(pipeline_result["last_clean_date"])
    next_natural_reset_date = pipeline_result["next_natural_reset_date"]

    return DashboardPayload(
        config=config,
        meta=DashboardMeta(
            computed_at=datetime.now(),
            data_through=min(pipeline_result["weather_forecast_end"], pipeline_result["aq_forecast_end"]),
            weather_forecast_end=pipeline_result["weather_forecast_end"],
            aq_forecast_end=pipeline_result["aq_forecast_end"],
        ),
        weather_today=weather_today,
        soiling=SoilingBlock(
            series=soiling_days,
            soiling_ratio_today=pipeline_result["soiling_today"] or 0.0,
            daily_rate=_estimate_daily_rate(pipeline_result),
            last_clean_date=last_clean_date,
            last_clean_assumed=pipeline_result["assumed_reset"],
            days_since_clean=pipeline_result["days_since_clean"],
            next_natural_reset_date=_to_date(next_natural_reset_date) if next_natural_reset_date is not None else None,
            days_until_natural_reset=pipeline_result["days_until_natural"],
        ),
        generation=GenerationBlock(
            daily=generation_days,
            hourly_today=hourly_today,
            peak_power_kw=max((h.power_kw for h in hourly_today), default=0.0),
        ),
        acr=AcrBlock(curve=acr_points),
        recommendation=recommendation,
        events=events,
    )


def _estimate_daily_rate(pipeline_result: dict) -> float:
    """Mean day-over-day drop in soiling_ratio across the dry stretch, as a
    fraction (0.0017 = 0.17%/day) -- data_contract.ts's SoilingBlock.daily_rate."""
    sr_stretch = pipeline_result["sr_stretch"]
    if len(sr_stretch) < 2:
        return 0.0
    diffs = sr_stretch.diff().dropna()
    dry_diffs = diffs[diffs < 0]
    return float(-dry_diffs.mean()) if len(dry_diffs) else 0.0
