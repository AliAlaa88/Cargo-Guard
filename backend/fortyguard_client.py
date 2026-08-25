"""
fortyguard_client.py
──────────────────────────────────────────────────────────────
Thin wrapper that imports the FortyGuard SDK from the local
quickstart repo and exposes the one method we need:
  get_ri_heatmap() → { features: [...] }   (GeoJSON tiles, each with °C temps)

Key design decisions after reviewing the API docs:
- The heatmap endpoint is async/batch (30 s – 5 min per call).
  We must call it at startup, not per route-request.
- Rhode Island (~1,545 mi²) exceeds both Basic (≤10 mi²) and
  Premium (≤50 mi²) per-call AOI limits.
  → We tile RI into a grid of small rectangles, fetch each tile,
    and merge the results.  Each cell is ~0.25° × 0.25° (~25 km²
    / ~9.6 mi²) which fits comfortably within the Basic limit.
- filter_type=3 (single-day) gives us average_temperature,
  min_temperature, max_temperature per GeoJSON tile — ideal for
  a representative "today's heat profile".
- We pick a fixed recent date for the demo (latest available
  historical date).  In a production system, swap this to
  yesterday's date dynamically.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

# ── load .env from the backend directory ──────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

# ── inject the local SDK onto sys.path ────────────────────────────────────────
_SDK_ROOT = Path(r"D:\fortyguard\temperature-api-quickstart")
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from fortyguard import FortyGuardClient  # noqa: E402  (import after path fix)

# ── constants ─────────────────────────────────────────────────────────────────
# Rhode Island bounding box (with small margin)
RI_BBOX = {
    "min_lat": 41.14,  # southernmost point
    "max_lat": 42.02,  # northernmost
    "min_lng": -71.91,  # westernmost
    "max_lng": -71.08,  # easternmost
}

# Tile size in degrees.  ~0.22° lat × 0.22° lng ≈ ~24 km × ~18 km ≈ ~432 km²
# ≈ ~9.6 mi² — safely within the Basic-tier limit (≤ 10 mi²).
TILE_DEG = 0.22

# Spatial resolution of returned temperature tiles (meters)
GRANULARITY = 100

# A recent historical date that is known to be available.
# In production you would use (date.today() - timedelta(days=1)).isoformat()
HEATMAP_DATE = (date.today() - timedelta(days=1)).isoformat()

# Cache path for the merged heatmap GeoJSON feature list
HEATMAP_CACHE = Path(__file__).parent / "data" / "ri_heatmap_cache.json"


def _make_polygon_aoi(min_lat: float, min_lng: float,
                      max_lat: float, max_lng: float) -> dict:
    """Build a GeoJSON FeatureCollection polygon (lon, lat) as required by the API."""
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lng, min_lat],
                    [max_lng, min_lat],
                    [max_lng, max_lat],
                    [min_lng, max_lat],
                    [min_lng, min_lat],
                ]],
            },
        }],
    }


def _fetch_all_tiles(client: FortyGuardClient) -> list[dict]:
    """Tile RI into a grid, fetch each cell's heatmap, return merged feature list."""
    lat_steps = math.ceil((RI_BBOX["max_lat"] - RI_BBOX["min_lat"]) / TILE_DEG)
    lng_steps = math.ceil((RI_BBOX["max_lng"] - RI_BBOX["min_lng"]) / TILE_DEG)

    total = lat_steps * lng_steps
    print(f"[FortyGuard] Fetching {total} heatmap tiles for Rhode Island "
          f"({lat_steps}×{lng_steps} grid, date={HEATMAP_DATE}) …")

    all_features: list[dict] = []

    for i in range(lat_steps):
        min_lat = RI_BBOX["min_lat"] + i * TILE_DEG
        max_lat = min(min_lat + TILE_DEG, RI_BBOX["max_lat"])
        if max_lat <= min_lat:
            continue

        for j in range(lng_steps):
            min_lng = RI_BBOX["min_lng"] + j * TILE_DEG
            max_lng = min(min_lng + TILE_DEG, RI_BBOX["max_lng"])
            if max_lng <= min_lng:
                continue

            aoi = _make_polygon_aoi(min_lat, min_lng, max_lat, max_lng)
            cell_num = i * lng_steps + j + 1
            print(f"  [{cell_num}/{total}] lat=[{min_lat:.2f},{max_lat:.2f}] "
                  f"lng=[{min_lng:.2f},{max_lng:.2f}]")

            try:
                resp = client.create_heatmap(
                    polygon_aoi=aoi,
                    start_date=HEATMAP_DATE,
                    filter_type=3,        # single-day → avg/min/max per tile
                    granularity=GRANULARITY,
                    verbose=False,
                )
                features = resp["result"]["map_data"]["features"]
                all_features.extend(features)
            except Exception as exc:
                print(f"    ⚠ Cell failed: {exc} — skipping")

    return all_features


def get_ri_heatmap(force_refresh: bool = False) -> list[dict]:
    """
    Return a list of GeoJSON features for Rhode Island.

    Each feature has:
      geometry  – Polygon (lon, lat)
      properties.average_temperature  – °C
      properties.min_temperature      – °C
      properties.max_temperature      – °C

    Results are disk-cached so the expensive API round-trip only happens once
    (or when force_refresh=True).
    """
    if not force_refresh and HEATMAP_CACHE.exists():
        print(f"[FortyGuard] Loading heatmap from cache ({HEATMAP_CACHE})")
        with HEATMAP_CACHE.open() as f:
            return json.load(f)

    client = FortyGuardClient()
    features = _fetch_all_tiles(client)

    HEATMAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with HEATMAP_CACHE.open("w") as f:
        json.dump(features, f)
    print(f"[FortyGuard] Heatmap cached → {HEATMAP_CACHE} ({len(features)} features)")
    return features
