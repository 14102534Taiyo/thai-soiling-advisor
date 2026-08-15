"""GritWatch API: a stateless calculator.

GET /api/sites and GET /api/sites/{id} just hand back starting presets --
nothing here is shared or saved. POST /api/compute takes a full SiteConfig
and returns a freshly-computed DashboardPayload; every visitor's edits stay
in their own browser and never touch another visitor's view.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gritwatch.backend import jobs, store
from gritwatch.backend.models import DashboardPayload, SiteConfig, SitePreset

router = APIRouter(prefix="/api")


@router.get("/sites", response_model=list[SitePreset])
def list_sites():
    return store.list_presets()


@router.get("/sites/{site_id}", response_model=SiteConfig)
def get_default_config(site_id: str):
    config = store.get_default_config(site_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"unknown site_id: {site_id}")
    return config


@router.post("/compute", response_model=DashboardPayload)
def compute(config: SiteConfig):
    """Runs the full pipeline for this exact config and returns the result.
    Nothing is persisted -- call again with different values for a fresh
    answer, same as any other calculator."""
    return jobs.compute_dashboard(config)
