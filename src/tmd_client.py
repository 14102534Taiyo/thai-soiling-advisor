"""TMD (Thai Meteorological Department) real-time station rainfall.

Used to sanity-check Open-Meteo's FORECAST-based rain against TMD's own
measured ground stations -- see app.py / README for why this matters: rain
is the soiling-ratio reset trigger, so a wrong forecast can falsely mark
the panel as cleaned, which then biases every downstream day until the
next real reset.

No personal API key needed for this project's scope. TMD's own API
documentation (https://data.tmd.go.th/api/index1.php) publishes a shared
demo credential (uid=api, ukey=api12345) that works against the live API --
verified against http://data.tmd.go.th/api/WeatherTodayBySynop/v1/index.php
returning real station data (station coords, rainfall in mm, observation
time). This is a published sample credential, not a private key -- for
production use, register a personal key via the link on that same
documentation page.

Endpoints combine three station networks (Synop/Agro/Hydro) for wider
coverage. IMPORTANT, verified empirically (repeated fetches at different
times of day all returned the same DateTime): the `WeatherToday` family of
endpoints publishes ONCE per calendar day, always timestamped 07:00,
reporting the trailing 24 hours as of that observation -- it does NOT
update through the day at each synoptic hour the way the separate
`Weather3Hours` endpoints do. That means a station's `Rainfall` field
covers roughly "07:00 yesterday to 07:00 today," i.e. essentially
YESTERDAY, not today -- this can only sanity-check/correct yesterday's
reset (fresh every morning), never today's, and only as a daily total, not
a specific hour crossing the model's hourly threshold.
"""

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests

CACHE_DIR = ".cache"
DEMO_UID = "api"
DEMO_UKEY = "api12345"
BASE_URL = "http://data.tmd.go.th/api"
STATION_NETWORKS = ["Synop", "Agro", "Hydro"]
# The three WeatherTodayBy{network} endpoints above (115 stations combined,
# verified live 2026-08-16) are each restricted to ONE station type -- they
# do NOT include every station TMD publishes. The general (un-suffixed)
# WeatherToday v2 endpoint returns a broader 127-station set that includes
# stations missing from all three networks above, e.g. CHIANG MAI (WMO
# 48327) and DON MUANG AIRPORT (WMO 48456) -- found live when a Chiang Mai
# rain-correction lookup silently fell back to a Lamphun station 0.23 deg
# away because the real nearest station (Chiang Mai itself, ~0.02 deg away)
# was never being fetched at all. Query this general endpoint too, in
# addition to the three specialized ones -- get_realtime_rain_stations
# already dedupes by station_id, so overlap between this and the networked
# endpoints is harmless.
GENERAL_WEATHER_TODAY_URL_PATH = "WeatherToday/v2/index.php"
MAX_STATION_DISTANCE_DEG = 1.0  # ~110km at Thailand's latitude, matches air4thai_client's threshold


def _cache_path(params: dict) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key_params = {k: params[k] for k in sorted(params)}
    digest = hashlib.sha256(json.dumps(key_params, sort_keys=True).encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"tmd_{digest}.xml")


def _fetch_xml(url: str, params: dict, cache_extra: dict) -> str:
    cache_key = {"url": url, **params, **cache_extra}
    path = _cache_path(cache_key)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    with open(path, "w", encoding="utf-8") as f:
        f.write(r.text)
    return r.text


RAIN_CORRECTION_LOG_PATH = os.path.join(CACHE_DIR, "tmd_rain_corrections.json")


def _site_key(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def _load_rain_corrections() -> dict:
    if not os.path.exists(RAIN_CORRECTION_LOG_PATH):
        return {}
    with open(RAIN_CORRECTION_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rain_correction(lat: float, lon: float, date_str: str, corrected_rainfall_mm: float = 0.0) -> None:
    """Persist a TMD-confirmed rain correction to a local JSON log, keyed by
    site (lat/lon) and date. Without this, a correction found on one day's
    run (e.g. "yesterday's forecast rain didn't really happen") would be
    silently LOST once that date rolls out of the "yesterday" check window
    on a later day's run -- weather_hourly is rebuilt from scratch from
    Open-Meteo every time, with nothing else remembering the fix. Persisted
    corrections are re-applied on every run, for every date they cover, not
    just yesterday's."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    corrections = _load_rain_corrections()
    site = corrections.setdefault(_site_key(lat, lon), {})
    site[date_str] = corrected_rainfall_mm
    with open(RAIN_CORRECTION_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(corrections, f, indent=2)


def get_rain_corrections_for_site(lat: float, lon: float) -> dict:
    """All persisted {date_str: corrected_rainfall_mm} corrections for this
    site (any date, not just yesterday) -- apply these to weather_hourly on
    every run so a correction found once stays fixed permanently."""
    corrections = _load_rain_corrections()
    return corrections.get(_site_key(lat, lon), {})


CONFIRMED_CLEAN_LOG_PATH = os.path.join(CACHE_DIR, "confirmed_clean_dates.json")


def _load_confirmed_clean_dates() -> dict:
    if not os.path.exists(CONFIRMED_CLEAN_LOG_PATH):
        return {}
    with open(CONFIRMED_CLEAN_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_confirmed_clean_date(lat: float, lon: float, clean_date_str: str) -> None:
    """Persist "the panel was cleaned on clean_date_str" as a settled fact
    for this site, once that date is no longer provisional (see app.py's
    last_clean_date_unconfirmed -- confirmed once TMD's real station data
    has verified the rain that triggered it, not just a forecast).

    Whatever cleaning_threshold_mm was in effect at confirmation time is
    what decided this -- deliberately NOT re-derived from a later, different
    threshold. A user exploring "what if the real threshold is higher/lower"
    for FUTURE projections shouldn't retroactively rewrite an already
    -confirmed historical event; only a genuinely newer confirmed reset
    should move this forward (see app.py's ratchet logic around this)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    confirmed = _load_confirmed_clean_dates()
    confirmed[_site_key(lat, lon)] = clean_date_str
    with open(CONFIRMED_CLEAN_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(confirmed, f, indent=2)


def get_confirmed_clean_date(lat: float, lon: float) -> str | None:
    """The most recently CONFIRMED last-clean date persisted for this site,
    or None if nothing has been confirmed yet (fresh site, or every reset
    seen so far has still been provisional)."""
    confirmed = _load_confirmed_clean_dates()
    return confirmed.get(_site_key(lat, lon))


def _parse_stations_xml(xml_text: str, network_label: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for station in root.iter("Station"):
        lat_el = station.find("Latitude")
        lon_el = station.find("Longitude")
        obs = station.find("Observation")
        if lat_el is None or lon_el is None or obs is None:
            continue
        rain_el = obs.find("Rainfall")
        rows.append({
            "station_id": station.findtext("WmoStationNumber"),
            "name_th": station.findtext("StationNameThai"),
            "name_en": station.findtext("StationNameEnglish"),
            "province": station.findtext("Province"),
            "network": network_label,
            "lat": float(lat_el.text),
            "lon": float(lon_el.text),
            "rainfall_mm_today": float(rain_el.text) if rain_el is not None and rain_el.text else None,
            "observed_at": obs.findtext("DateTime"),
        })
    return rows


def get_realtime_rain_stations() -> pd.DataFrame:
    """Today's rainfall-so-far (mm) at TMD stations nationwide, combining
    the general WeatherToday endpoint with the Synop/Agro/Hydro networks
    (see GENERAL_WEATHER_TODAY_URL_PATH's comment for why both are needed).
    Columns: station_id, name_th, name_en, province, network, lat, lon,
    rainfall_mm_today, observed_at. Cache-busted hourly since this is a
    same-day cumulative reading that updates through the day.
    """
    hour_bucket = datetime.now().strftime("%Y-%m-%d-%H")
    params = {"uid": DEMO_UID, "ukey": DEMO_UKEY}
    rows = []

    general_url = f"{BASE_URL}/{GENERAL_WEATHER_TODAY_URL_PATH}"
    general_xml = _fetch_xml(general_url, params, cache_extra={"t": hour_bucket, "network": "General"})
    rows.extend(_parse_stations_xml(general_xml, "General"))

    for network in STATION_NETWORKS:
        url = f"{BASE_URL}/WeatherTodayBy{network}/v1/index.php"
        xml_text = _fetch_xml(url, params, cache_extra={"t": hour_bucket, "network": network})
        rows.extend(_parse_stations_xml(xml_text, network))

    if not rows:
        return pd.DataFrame(columns=["station_id", "name_th", "name_en", "province", "network", "lat", "lon", "rainfall_mm_today", "observed_at"])
    # General endpoint listed first, so when a station appears in both (e.g.
    # some Synop stations also show up in General), drop_duplicates' default
    # keep="first" keeps a consistent single source per station rather than
    # depending on dict/loop ordering.
    return pd.DataFrame(rows).drop_duplicates(subset=["station_id"]).reset_index(drop=True)


def find_nearest_station(lat: float, lon: float, stations_df: pd.DataFrame) -> pd.Series:
    distances = ((stations_df["lat"] - lat) ** 2 + (stations_df["lon"] - lon) ** 2) ** 0.5
    nearest_idx = distances.idxmin()
    result = stations_df.loc[nearest_idx].copy()
    result["distance_deg"] = distances.loc[nearest_idx]
    return result


def get_realtime_rain_today(lat: float, lon: float) -> dict | None:
    """Nearest TMD station's rainfall-so-far today (mm, cumulative since
    local midnight as of the latest synoptic observation), or None if no
    station is close enough (see MAX_STATION_DISTANCE_DEG) or its reading
    is missing."""
    stations = get_realtime_rain_stations()
    if len(stations) == 0:
        return None
    nearest = find_nearest_station(lat, lon, stations)
    if nearest["distance_deg"] > MAX_STATION_DISTANCE_DEG or nearest["rainfall_mm_today"] is None:
        return None
    return {
        "rainfall_mm_today": float(nearest["rainfall_mm_today"]),
        # Thai name first -- the whole dashboard's UI text is Thai, and an
        # English station name embedded mid-sentence ("สถานี TMD จริง
        # (BANGKOK METROPOLIS, ...)") read oddly. Fall back to the English
        # name only if TMD's own StationNameThai field is missing.
        "station_name": nearest["name_th"] or nearest["name_en"],
        "province": nearest["province"],
        "distance_deg": float(nearest["distance_deg"]),
        "observed_at": nearest["observed_at"],
    }


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    sites = {
        "Bangkok": (13.7563, 100.5018),
        "Chiang Mai": (18.7883, 98.9853),
        "Khon Kaen": (16.4419, 102.8360),
        "Rayong": (12.6814, 101.2816),
        "Hat Yai": (7.0086, 100.4747),
    }
    stations = get_realtime_rain_stations()
    print(f"Fetched {len(stations)} TMD stations across {STATION_NETWORKS}")
    for name, (lat, lon) in sites.items():
        result = get_realtime_rain_today(lat, lon)
        print(name, "->", result)
