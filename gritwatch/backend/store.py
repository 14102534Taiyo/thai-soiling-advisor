"""Site presets -- starting points for the sidebar, not shared/saved state.

Nothing in this module writes to disk. Each visitor's parameter edits stay
in their own browser (see app.js) and are computed fresh per request via
POST /api/compute; nothing here is persisted or shared between visitors.
"""

from __future__ import annotations

from gritwatch.backend.models import SiteConfig, SitePreset
from gritwatch.backend.pipeline import SITE_PRESETS

# app.py's sidebar defaults (see the removed sidebar block / README) -- used
# to seed a SiteConfig's non-location fields for any preset.
_DEFAULT_SYSTEM_PARAMS = dict(
    kwp=100.0,
    dc_ac_ratio=1.2,
    tilt=10.0,
    azimuth=180.0,
    system_efficiency=0.88,
    gamma_pdc=-0.0040,
    cleaning_threshold_mm=1.0,
    price_per_kwh=4.0,
    cleaning_cost_per_kwp=15.0,
    horizon_days=90,
)

_SITE_ID_SLUGS = {
    "กรุงเทพฯ": "bangkok",
    "เชียงใหม่": "chiang_mai",
    "ขอนแก่น": "khon_kaen",
    "ระยอง": "rayong",
    "หาดใหญ่": "hat_yai",
}


def _default_configs() -> dict[str, SiteConfig]:
    configs = {}
    for site_name, coords in SITE_PRESETS.items():
        if coords is None:
            continue
        site_id = _SITE_ID_SLUGS[site_name]
        lat, lon = coords
        configs[site_id] = SiteConfig(
            site_id=site_id, site_name=site_name, lat=lat, lon=lon, **_DEFAULT_SYSTEM_PARAMS,
        )
    return configs


def list_presets() -> list[SitePreset]:
    return [
        SitePreset(site_id=c.site_id, site_name=c.site_name, lat=c.lat, lon=c.lon)
        for c in _default_configs().values()
    ]


def get_default_config(site_id: str) -> SiteConfig | None:
    return _default_configs().get(site_id)
