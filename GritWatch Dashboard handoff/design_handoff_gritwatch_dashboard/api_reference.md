# API reference

A sketch, not finished code. It shows where each existing `src/` function plugs in. Types are in
`data_contract.ts`.

## Layout

```
api/
  main.py          FastAPI app, CORS, router mounting
  routes.py        the endpoints below
  jobs.py          the nightly pipeline + the recompute queue
  store.py         read/write the per-site JSON blob
  models.py        pydantic models mirroring data_contract.ts
src/               unchanged
```

## The nightly job (`jobs.py`)

This is `main_demo.py` with its `print` calls replaced by a serialization step. Reuse that file's
logic verbatim — including the two corrections it already carries:

- `last_clean_date` anchors to today, never to a future analog-year reset.
- `min_T = max(1, (today - last_clean_date).days)` floors the optimizer so the recommended date
  cannot land in the past.

```python
def compute_dashboard(config: SiteConfig) -> DashboardPayload:
    today = date.today()
    hist_start = (today - timedelta(days=90)).isoformat()
    hist_end = (today - timedelta(days=1)).isoformat()

    weather_hourly = _concat_hourly(
        get_historical_weather(config.lat, config.lon, hist_start, hist_end),
        get_forecast_weather(config.lat, config.lon, forecast_days=16),
    )
    aq_hourly = _concat_hourly(
        get_historical_air_quality(config.lat, config.lon, hist_start, hist_end),
        get_forecast_air_quality(config.lat, config.lon, forecast_days=5),
    )

    weather_forecast_end = weather_hourly.index.max().date()
    aq_forecast_end = aq_hourly.index.max().date()
    horizon_end = today + timedelta(days=config.horizon_days)

    # representative-year fill past the real forecast horizon
    # (see main_demo.get_beyond_horizon_fill_params — cache this per site,
    #  it is the slowest part of the whole pipeline)
    ...

    soiling_ratio = compute_soiling_ratio(
        weather_hourly, aq_hourly,
        cleaning_threshold_mm=config.cleaning_threshold_mm,
        tilt=config.tilt,
    )
    expected_generation = compute_expected_generation(
        weather_hourly, config.lat, config.lon, config.kwp, config.dc_ac_ratio,
        config.tilt, config.azimuth,
        system_efficiency=config.system_efficiency, gamma_pdc=config.gamma_pdc,
    )
    hourly_power = compute_expected_generation_hourly(...)  # today only

    # dry stretch up to the next natural reset, exactly as main_demo does it
    sr_stretch, gen_stretch, next_natural_reset_date = _dry_stretch(
        soiling_ratio, expected_generation, today
    )

    acr_curve = compute_acr_curve(
        sr_stretch, gen_stretch,
        config.price_per_kwh, config.cleaning_cost_per_kwp, config.kwp,
        max_days=len(sr_stretch),
    )
    rec = recommend_cleaning(
        acr_curve, last_clean_date=last_clean_date,
        min_T=max(1, (today - last_clean_date).days),
    )
    rec["wait_for_rain"] = next_natural_reset_date is not None and rec["optimal_T"] >= len(sr_stretch)
    rec["insufficient_data"] = len(sr_stretch) < 2

    return DashboardPayload(...)   # serialize; store.write(config.site_id, payload)
```

Schedule it for early morning, after Open-Meteo's overnight refresh and after `tmd_client`'s
rain verification has run for the previous day. On failure, keep the previous blob and set an error
flag rather than serving nothing.

## Endpoints (`routes.py`)

```python
@router.get("/sites", response_model=list[SiteSummary])
def list_sites(): ...

@router.get("/sites/{site_id}", response_model=SiteConfig)
def get_config(site_id: str): ...

@router.put("/sites/{site_id}", response_model=SiteConfig)
def put_config(site_id: str, config: SiteConfig):
    """Saves and queues a recompute. Returns immediately; the stored blob
    keeps serving with meta.config_stale = true until the job lands."""

@router.get("/sites/{site_id}/dashboard", response_model=DashboardPayload)
def get_dashboard(site_id: str):
    """Reads the precomputed blob. Never runs pvlib. Milliseconds."""

@router.post("/sites/{site_id}/recompute", response_model=RecomputeJob)
def recompute(site_id: str, background: BackgroundTasks): ...

@router.get("/jobs/{job_id}", response_model=RecomputeJob)
def get_job(job_id: str): ...

@router.post("/acr", response_model=AcrResult)
def recompute_acr(req: AcrRequest):
    """Price-only recompute: reloads the stored soiling/generation series and
    reruns compute_acr_curve + recommend_cleaning. No network, no pvlib —
    fast enough to call on slider release if you prefer it to client-side math."""
```

## Notes

- CORS: allow the frontend origin during development.
- Cache headers: `GET /dashboard` can carry `Cache-Control: max-age=300` — the data only changes
  once a day.
- Numbers cross the wire as plain JSON floats. Do not pre-round; the UI formats
  (soiling ratio to 3 decimals, THB to whole numbers, rates to 2 decimals).
- pandas Series must be converted explicitly — `Timestamp` is not JSON-serializable. Use
  `.strftime("%Y-%m-%d")` for dates and `float()` for values.
- Keep the Streamlit app working during the migration; the two can share `jobs.py` and the store.
