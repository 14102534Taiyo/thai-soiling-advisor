# Module interface contracts (Day 1)

Three modules are being built in parallel by separate agents. Each module
must conform to these signatures exactly so they compose without changes.
No module should import from another under active development — treat
these signatures as the integration boundary.

## `src/weather_client.py` (Team: Data Ingestion)

```python
def get_historical_weather(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    """Hourly. DatetimeIndex (UTC-naive, local time of lat/lon). Columns:
    ghi, dni, dhi (W/m2), rainfall_mm, temp_air (C), wind_speed (m/s)."""

def get_forecast_weather(lat: float, lon: float, forecast_days: int = 16) -> pd.DataFrame:
    """Same columns/index convention as above, starting today."""

def get_historical_air_quality(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    """Hourly. DatetimeIndex. Columns: pm2_5, pm10 (ug/m3)."""

def get_forecast_air_quality(lat: float, lon: float, forecast_days: int = 5) -> pd.DataFrame:
    """Same columns/index convention as above. Open-Meteo AQ forecast is
    shorter horizon than weather forecast -- if requested forecast_days
    exceeds what the API returns, return what's available and let the
    caller handle the gap (do not raise)."""
```

Both historical functions must use Open-Meteo's free endpoints (no API
key). Cache raw JSON responses under `.cache/` (create dir if missing) so
repeated calls during dev don't hammer the API.

## `src/soiling_pv_model.py` (Team: PV Physics)

```python
def compute_soiling_ratio(
    weather_daily: pd.DataFrame,   # columns: rainfall_mm
    aq_daily: pd.DataFrame,        # columns: pm2_5, pm10
    cleaning_threshold_mm: float = 6.0,
    tilt: float = 10.0,
) -> pd.Series:
    """Daily soiling ratio (0-1, 1=clean) via pvlib.soiling.hsu.
    Index must be the intersection of weather_daily/aq_daily dates."""

def compute_expected_generation(
    weather_hourly: pd.DataFrame,  # columns: ghi, dni, dhi, temp_air, wind_speed
    lat: float, lon: float,
    kwp: float, dc_ac_ratio: float, tilt: float, azimuth: float,
) -> pd.Series:
    """Daily expected AC energy (kWh), CLEAN panel (no soiling derate),
    via pvlib PVWatts ModelChain. Index = calendar dates."""
```

Assume `weather_client.py` will hand you DataFrames matching the shapes
documented above — build/test against a small synthetic DataFrame with
the same columns rather than waiting on the other module.

## `src/economics.py` (Team: Economics/Optimization)

```python
def compute_acr_curve(
    soiling_ratio: pd.Series,      # daily, 0-1, indexed by date since last clean (day 0 = 1.0)
    expected_generation: pd.Series, # daily kWh, clean-panel baseline, same index
    price_per_kwh: float,
    cleaning_cost_per_kwp: float,
    kwp: float,
    max_days: int = 60,
) -> pd.DataFrame:
    """Columns: T (int, 1..max_days), acr (float, currency/day).
    acr[T] = (sum_{t=1..T} (1-soiling_ratio[t]) * expected_generation[t] * price_per_kwh) / T
             + (cleaning_cost_per_kwp * kwp) / T
    If soiling_ratio/expected_generation are shorter than max_days, clip
    max_days to the available length rather than raising."""

def recommend_cleaning(acr_curve: pd.DataFrame, last_clean_date, min_T: int = 1, band_tolerance: float = 0.05) -> dict:
    """min_T floors the search at max(1, days already elapsed since
    last_clean_date) -- without it, the unconstrained ACR minimum can be
    a T that's already in the past, producing a recommended_date before
    today. band_low_T/band_high_T is the contiguous T-range around
    optimal_T within band_tolerance fraction of its cost -- ACR can be
    multi-modal (see recommend_cleaning's own docstring), so a single
    date can look unstable across reruns even when the cost difference
    is negligible; report the range instead of over-trusting one day.
    Returns {"optimal_T": int, "recommended_date": date, "band_low_T":
    int, "band_high_T": int, "band_low_date": date, "band_high_date":
    date, "acr_at_optimal": float, "savings_vs_daily_clean": float,
    "savings_vs_never_clean": float}."""
```

Build/test against a synthetic monotonically-decaying `soiling_ratio`
series and a flat `expected_generation` series — don't wait on the other
two modules.

## Integration

After all three land, `main_demo.py` (built by the integrator, not by
these agents) wires them together end-to-end for one example site and
prints the recommended cleaning date.
