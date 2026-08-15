"""Pydantic models mirroring GritWatch Dashboard handoff/design_handoff_gritwatch_dashboard/data_contract.ts.

Field names match the TypeScript side (snake_case) on purpose, per that
file's own header comment, so these serialize straight through with no
rename layer on the frontend.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class SiteConfig(BaseModel):
    site_id: str
    site_name: str
    lat: float
    lon: float

    kwp: float
    dc_ac_ratio: float
    tilt: float
    azimuth: float
    system_efficiency: float
    gamma_pdc: float

    cleaning_threshold_mm: float
    price_per_kwh: float
    cleaning_cost_per_kwp: float
    horizon_days: int


class SitePreset(BaseModel):
    """A starting point for the sidebar -- lat/lon/site_name only. Not a
    saved/shared state; each visitor's edits from here stay local to their
    own browser session (see POST /api/compute)."""
    site_id: str
    site_name: str
    lat: float
    lon: float


class SoilingDay(BaseModel):
    date: date
    soiling_ratio: float
    pm2_5: float
    pm10: float
    rainfall_mm: float
    max_hourly_rain_mm: float
    is_rain_reset: bool
    is_real_data: bool


class SoilingBlock(BaseModel):
    series: list[SoilingDay]
    soiling_ratio_today: float
    daily_rate: float
    last_clean_date: date
    last_clean_assumed: bool
    days_since_clean: int
    next_natural_reset_date: date | None
    days_until_natural_reset: int | None


class GenerationDay(BaseModel):
    date: date
    expected_kwh_clean: float
    expected_kwh_soiled: float
    is_real_data: bool


class GenerationHour(BaseModel):
    timestamp: datetime
    power_kw: float
    ghi: float


class GenerationBlock(BaseModel):
    daily: list[GenerationDay]
    hourly_today: list[GenerationHour]
    peak_power_kw: float


class AcrPoint(BaseModel):
    T: int
    acr: float
    loss_cost_per_day: float
    clean_cost_per_day: float


class Recommendation(BaseModel):
    optimal_T: int
    recommended_date: date
    band_low_T: int
    band_high_T: int
    band_low_date: date
    band_high_date: date
    acr_at_optimal: float
    savings_vs_daily_clean: float
    savings_vs_never_clean: float

    wait_for_rain: bool
    insufficient_data: bool
    min_T: int


EventKind = Literal["rain_reset", "tmd_correction", "pm_recalibration", "fill_boundary", "manual_clean"]


class SystemEvent(BaseModel):
    date: date
    kind: EventKind
    title: str
    effect: str


class DashboardMeta(BaseModel):
    computed_at: datetime
    data_through: date
    weather_forecast_end: date
    aq_forecast_end: date


class AcrBlock(BaseModel):
    curve: list[AcrPoint]


class DashboardPayload(BaseModel):
    config: SiteConfig
    meta: DashboardMeta
    soiling: SoilingBlock
    generation: GenerationBlock
    acr: AcrBlock
    recommendation: Recommendation
    events: list[SystemEvent]
