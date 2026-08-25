"""
cost.py
──────────────────────────────────────────────────────────────
Pluggable edge-cost factories and rich thermal exposure metrics
for the deterministic routing foundation.

Three core routing modes:
-------------------------
1. shortest:      weight = road length (metres)
2. fastest:       weight = travel time (seconds)
3. temp_safe:     weight = travel_time × (1 + α × max(0, T_edge − T_safe))

Refinements:
------------
  • Cumulative Thermal Exposure (Degree-Minutes):
      ThermalExposure = ∑ max(0, T_e - T_safe) × (time_e / 60)
  • Thermal Stress Score (0 - 100 normalized index).
  • Time spent in thermal exceedance (seconds and percentage).
  • Simplified cargo thermal-response model estimation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from temp_grid import TempGrid

DEFAULT_SAFE_THRESHOLD = 20.0   # °C — baseline safe ambient ceiling
DEFAULT_ALPHA = 0.08            # cost penalty multiplier per °C excess


def shortest_cost():
    """Weight = road length in metres."""
    def _cost(u, v, data):
        return float(data.get("length", 0.0))
    return _cost


def fastest_cost():
    """Weight = travel time in seconds."""
    def _cost(u, v, data):
        return float(data.get("travel_time", data.get("length", 0.0) / 10.0))
    return _cost


def temp_safe_cost(
    grid: "TempGrid",
    G_nodes: dict,
    threshold: float = DEFAULT_SAFE_THRESHOLD,
    alpha: float = DEFAULT_ALPHA,
):
    """
    Weight = travel_time × (1 + α × max(0, T_edge − threshold))
    """
    def _cost(u, v, data):
        travel_time = float(data.get("travel_time", data.get("length", 0.0) / 10.0))

        u_lat, u_lng = G_nodes[u]["y"], G_nodes[u]["x"]
        v_lat, v_lng = G_nodes[v]["y"], G_nodes[v]["x"]
        temp_c = grid.temp_for_edge(u_lat, u_lng, v_lat, v_lng)

        excess = max(0.0, temp_c - threshold)
        multiplier = 1.0 + alpha * excess
        return travel_time * multiplier

    return _cost


# ── Rich Route-Level Thermal Metrics ──────────────────────────────────────────

def route_temp_stats(
    path: list,
    G,
    G_nodes: dict,
    grid: "TempGrid",
    safe_threshold: float = DEFAULT_SAFE_THRESHOLD,
) -> dict:
    """
    Compute comprehensive thermal exposure and risk metrics along a path.

    Returns
    -------
    {
      "avg_temp_c": float,           # Average road surface temp (°C)
      "max_temp_c": float,           # Peak road surface temp (°C)
      "min_temp_c": float,           # Min road surface temp (°C)
      "thermal_exposure": float,     # Cumulative Degree-Minutes above threshold
      "thermal_stress_score": float, # 0 - 100 risk score
      "exposure_time_s": int,        # Seconds spent above safe_threshold
      "exposure_pct": float,         # % of journey duration spent above safe_threshold
      "hotspot_segments": int,       # Number of road segments exceeding threshold
      "risk_level": "low"|"moderate"|"high",
      "estimated_cargo_temp_c": float # Estimated internal cargo temp (°C)
    }
    """
    if not path or len(path) < 2 or grid is None:
        return {
            "avg_temp_c": round(safe_threshold, 1),
            "max_temp_c": round(safe_threshold, 1),
            "min_temp_c": round(safe_threshold, 1),
            "thermal_exposure": 0.0,
            "thermal_stress_score": 0.0,
            "exposure_time_s": 0,
            "exposure_pct": 0.0,
            "hotspot_segments": 0,
            "risk_level": "low",
            "estimated_cargo_temp_c": round(safe_threshold, 1),
        }

    edge_temps: list[float] = []
    edge_times: list[float] = []

    cumulative_deg_mins = 0.0
    exposure_time_s = 0.0
    hotspot_count = 0

    # Cargo thermal response simulation state (initializes at safe_threshold)
    # k_insulation represents thermal ingress rate for standard insulated transport
    k_insulation = 0.0003
    simulated_cargo_temp = safe_threshold

    for u, v in zip(path[:-1], path[1:]):
        edge_data = G[u][v]
        travel_s = float(edge_data.get("travel_time", edge_data.get("length", 0.0) / 10.0))

        u_lat, u_lng = G_nodes[u]["y"], G_nodes[u]["x"]
        v_lat, v_lng = G_nodes[v]["y"], G_nodes[v]["x"]
        temp_c = float(grid.temp_for_edge(u_lat, u_lng, v_lat, v_lng))

        edge_temps.append(temp_c)
        edge_times.append(travel_s)

        # Cumulative thermal exposure: Degree-Minutes above threshold
        excess = max(0.0, temp_c - safe_threshold)
        if excess > 0:
            cumulative_deg_mins += excess * (travel_s / 60.0)
            exposure_time_s += travel_s
            hotspot_count += 1

        # Simulate cargo core temperature delta over edge travel time
        simulated_cargo_temp += k_insulation * (temp_c - simulated_cargo_temp) * travel_s

    total_time_s = sum(edge_times) if edge_times else 1.0
    avg_temp = sum(edge_temps) / len(edge_temps) if edge_temps else safe_threshold
    max_temp = max(edge_temps) if edge_temps else safe_threshold
    min_temp = min(edge_temps) if edge_temps else safe_threshold

    exposure_pct = (exposure_time_s / total_time_s * 100.0) if total_time_s > 0 else 0.0

    # Thermal Stress Score (0 to 100 scale based on cumulative exposure & peak)
    # 50 degree-minutes or peak > 35°C escalates score
    stress_from_exposure = min(70.0, (cumulative_deg_mins / 50.0) * 70.0)
    stress_from_peak = min(30.0, max(0.0, (max_temp - safe_threshold) / 15.0) * 30.0)
    stress_score = round(min(100.0, stress_from_exposure + stress_from_peak), 1)

    # Risk level classification
    if stress_score < 25.0 and max_temp < 30.0:
        risk_level = "low"
    elif stress_score < 60.0 and max_temp < 36.0:
        risk_level = "moderate"
    else:
        risk_level = "high"

    return {
        "avg_temp_c": round(avg_temp, 1),
        "max_temp_c": round(max_temp, 1),
        "min_temp_c": round(min_temp, 1),
        "thermal_exposure": round(cumulative_deg_mins, 1),
        "thermal_stress_score": stress_score,
        "exposure_time_s": round(exposure_time_s),
        "exposure_pct": round(exposure_pct, 1),
        "hotspot_segments": hotspot_count,
        "risk_level": risk_level,
        "estimated_cargo_temp_c": round(simulated_cargo_temp, 1),
    }
