/**
 * GritWatch dashboard — API data contract.
 *
 * Field names mirror the Python side (snake_case) so FastAPI can serialize
 * dataclasses / pydantic models straight through without a rename layer.
 *
 * Dates are ISO 8601 date strings ("2026-08-15"); timestamps are ISO 8601
 * datetimes in the site's LOCAL time, tz-naive, matching the convention in
 * docs/interfaces.md.
 */

export type IsoDate = string;
export type IsoDateTime = string;

/** Everything the sidebar can change. Persisted per site. */
export interface SiteConfig {
  site_id: string;
  site_name: string;              // "กรุงเทพมหานคร"
  lat: number;                    // 13.7563
  lon: number;                    // 100.5018

  kwp: number;                    // 100
  dc_ac_ratio: number;            // 1.2
  tilt: number;                   // degrees, 0-45; 15 is the calibrated Thai optimum
  azimuth: number;                // degrees, 0-359; 180 = south
  system_efficiency: number;      // 0-1, e.g. 0.88 (NOT a percentage)
  gamma_pdc: number;              // fraction/degC, negative, e.g. -0.0040

  cleaning_threshold_mm: number;  // mm PER HOUR, default 1.0
  price_per_kwh: number;          // THB
  cleaning_cost_per_kwp: number;  // THB per wash
  horizon_days: number;           // 30-120, projection horizon
}

export interface SiteSummary {
  site_id: string;
  site_name: string;
  kwp: number;
  /** Convenience for a site list: null when no manual clean is recommended. */
  recommended_date: IsoDate | null;
  soiling_ratio_today: number;
  computed_at: IsoDateTime;
}

/** One day of the projected series. Ordered, one entry per calendar day. */
export interface SoilingDay {
  date: IsoDate;
  soiling_ratio: number;          // 0-1, 1 = clean
  pm2_5: number;                  // ug/m3, bias-corrected
  pm10: number;                   // ug/m3
  rainfall_mm: number;            // daily total, for reference
  max_hourly_rain_mm: number;     // what the hourly cleaning threshold is tested against
  is_rain_reset: boolean;         // an hour crossed cleaning_threshold_mm
  /** false once past the real forecast horizon (representative-year replay). */
  is_real_data: boolean;
}

export interface SoilingBlock {
  series: SoilingDay[];
  soiling_ratio_today: number;
  /** Mean daily loss across dry days, as a fraction (0.0017 = 0.17%/day). */
  daily_rate: number;
  last_clean_date: IsoDate;
  /** true when no rain reset was found on or before today and the date is inferred. */
  last_clean_assumed: boolean;
  days_since_clean: number;
  /** null when no reset is visible inside the horizon. */
  next_natural_reset_date: IsoDate | null;
  days_until_natural_reset: number | null;
}

export interface GenerationDay {
  date: IsoDate;
  expected_kwh_clean: number;     // compute_expected_generation, no soiling derate
  expected_kwh_soiled: number;    // clean * soiling_ratio for that day
  is_real_data: boolean;
}

export interface GenerationHour {
  timestamp: IsoDateTime;
  power_kw: number;               // compute_expected_generation_hourly, clean panel
  ghi: number;                    // W/m2, for the shaded backdrop
}

export interface GenerationBlock {
  /** At least the next 7 days; the chart renders the first 7. */
  daily: GenerationDay[];
  /** Today only, hourly. */
  hourly_today: GenerationHour[];
  peak_power_kw: number;
}

/** One row of compute_acr_curve's DataFrame. */
export interface AcrPoint {
  T: number;                      // 1..horizon
  acr: number;                    // THB/day
  loss_cost_per_day: number;      // THB/day
  clean_cost_per_day: number;     // THB/day
}

/** recommend_cleaning()'s return dict, verbatim, plus UI-facing flags. */
export interface Recommendation {
  optimal_T: number;
  recommended_date: IsoDate;
  band_low_T: number;
  band_high_T: number;
  band_low_date: IsoDate;
  band_high_date: IsoDate;
  acr_at_optimal: number;         // THB/day
  savings_vs_daily_clean: number; // THB/day
  savings_vs_never_clean: number; // THB/day

  /**
   * true when optimal_T sits at the edge of the dry stretch AND a natural
   * rain reset arrives first — main_demo.py's "hits_edge" case. The UI shows
   * "ไม่ต้องล้าง — ฝนจะล้างให้" instead of a date.
   */
  wait_for_rain: boolean;
  /**
   * true when the dry stretch is under 2 days and the optimizer did not run.
   * Every other field in this object is then meaningless — render an empty state.
   */
  insufficient_data: boolean;
  /** Floor applied to the T search: max(1, days elapsed since last clean). */
  min_T: number;
}

export type EventKind =
  | "rain_reset"          // an hourly rain event cleaned the panel
  | "tmd_correction"      // TMD station data cancelled a mis-forecast reset
  | "pm_recalibration"    // Air4Thai/PCD monthly bias correction applied
  | "fill_boundary"       // real forecast ended, representative-year replay began
  | "manual_clean";       // a wash was recorded

export interface SystemEvent {
  date: IsoDate;
  kind: EventKind;
  title: string;                  // Thai, display-ready
  effect: string;                 // Thai, display-ready
}

export interface DashboardMeta {
  computed_at: IsoDateTime;       // when the nightly job produced this blob
  /** Last day backed by real observation/forecast, not representative-year fill. */
  data_through: IsoDate;
  weather_forecast_end: IsoDate;
  aq_forecast_end: IsoDate;
  /** Set when the blob predates the current config — UI shows a stale badge. */
  config_stale: boolean;
}

/** The single payload GET /api/sites/{id}/dashboard returns. */
export interface DashboardPayload {
  config: SiteConfig;
  meta: DashboardMeta;
  soiling: SoilingBlock;
  generation: GenerationBlock;
  acr: { curve: AcrPoint[] };
  recommendation: Recommendation;
  events: SystemEvent[];
}

/** POST /api/acr — cheap price-only recompute (no pvlib, no network). */
export interface AcrRequest {
  site_id: string;
  price_per_kwh: number;
  cleaning_cost_per_kwp: number;
  band_tolerance?: number;        // default 0.05
}

export interface AcrResult {
  curve: AcrPoint[];
  recommendation: Recommendation;
}

/** POST /api/sites/{id}/recompute */
export interface RecomputeJob {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  queued_at: IsoDateTime;
  finished_at: IsoDateTime | null;
  error: string | null;
}
