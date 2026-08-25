"""
temp_grid.py
──────────────────────────────────────────────────────────────
Spatial index over the FortyGuard heatmap tiles.

Usage:
    grid = TempGrid(features)           # build once at startup
    temp = grid.temp_at(lat, lng)       # O(log n) point-in-polygon lookup
"""

from __future__ import annotations

from shapely.geometry import Point, shape


class TempGrid:
    """
    Wraps a list of GeoJSON heatmap features in a Shapely STRtree for fast
    spatial lookups of temperature at any (lat, lng) coordinate.

    The tree stores Shapely polygon objects; each has a `temp_c` attribute
    that holds the average surface temperature in °C (from FortyGuard).
    """

    def __init__(self, features: list[dict], fallback_temp: float = 25.0) -> None:
        """
        Parameters
        ----------
        features:
            GeoJSON feature list returned by `get_ri_heatmap()`.
            Each feature is expected to have:
              - geometry: Polygon / MultiPolygon  (lon, lat)
              - properties.average_temperature: float (°C)
        fallback_temp:
            Temperature (°C) to return when a point is not covered by any tile
            (e.g. ocean / outside bounding box).
        """
        self.fallback_temp = fallback_temp
        self._polys: list = []
        self._temps: list[float] = []

        for feat in features:
            try:
                poly = shape(feat["geometry"])
                props = feat.get("properties", {})
                # filter_type=3 (single-day) returns average_temperature
                temp = props.get("average_temperature") or props.get("temperature")
                if temp is None:
                    continue
                self._polys.append(poly)
                self._temps.append(float(temp))
            except Exception:
                continue

        # Build STRtree for O(log n) bounding-box pre-filtering
        from shapely.strtree import STRtree
        self._tree = STRtree(self._polys)

    def temp_at(self, lat: float, lng: float) -> float:
        """Return temperature °C at the given point, or fallback if not covered."""
        pt = Point(lng, lat)  # Shapely / GeoJSON uses (x=lon, y=lat)
        # Query bounding-box candidates from the tree
        candidates = self._tree.query(pt)
        for idx in candidates:
            if self._polys[idx].contains(pt):
                return self._temps[idx]
        return self.fallback_temp

    def temp_for_edge(self, u_lat: float, u_lng: float,
                      v_lat: float, v_lng: float) -> float:
        """Return temperature at the midpoint of a road edge (u→v)."""
        mid_lat = (u_lat + v_lat) / 2.0
        mid_lng = (u_lng + v_lng) / 2.0
        return self.temp_at(mid_lat, mid_lng)
