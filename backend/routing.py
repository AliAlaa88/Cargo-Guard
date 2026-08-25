"""
routing.py
──────────────────────────────────────────────────────────────
Deterministic top-k pathfinding engine with rich route telemetry
and thermal metrics.
"""

from __future__ import annotations

from itertools import islice
import networkx as nx
import osmnx as ox

from cost import route_temp_stats, DEFAULT_SAFE_THRESHOLD


def find_routes(
    G,
    src_lat: float,
    src_lng: float,
    dst_lat: float,
    dst_lng: float,
    k: int = 3,
    weight_fn=None,
    grid=None,
    safe_threshold: float = DEFAULT_SAFE_THRESHOLD,
) -> list[dict]:
    """
    Find top-k candidate routes between coordinates and attach rich metrics.

    Parameters
    ----------
    G:               NetworkX DiGraph (enriched with length + travel_time per edge)
    src_lat, src_lng: Origin coordinates
    dst_lat, dst_lng: Destination coordinates
    k:               Number of alternative paths to return (default 3)
    weight_fn:       Edge weight callable(u, v, data) -> float
    grid:            TempGrid instance for spatial thermal lookups
    safe_threshold:  Safe temperature threshold (°C)
    """
    src_node = ox.distance.nearest_nodes(G, X=src_lng, Y=src_lat)
    dst_node = ox.distance.nearest_nodes(G, X=dst_lng, Y=dst_lat)

    try:
        gen = nx.shortest_simple_paths(G, src_node, dst_node, weight=weight_fn or "length")
        paths = list(islice(gen, k))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []

    G_nodes = G.nodes
    results = []

    for i, path in enumerate(paths):
        coords = []
        dist_m = 0.0
        duration_s = 0.0

        for u, v in zip(path[:-1], path[1:]):
            edge_data = G[u][v]
            dist_m += float(edge_data.get("length", 0.0))
            duration_s += float(edge_data.get("travel_time", edge_data.get("length", 0.0) / 10.0))

            if "geometry" in edge_data:
                curve_coords = [(y, x) for x, y in edge_data["geometry"].coords]
                coords.extend(curve_coords)
            else:
                coords.append((G_nodes[u]["y"], G_nodes[u]["x"]))
                coords.append((G_nodes[v]["y"], G_nodes[v]["x"]))

        # Base geometric and navigation metrics
        route_data = {
            "route_id": f"route_{i + 1}",
            "name": f"Route {i + 1}",
            "coords": coords,
            "distance_m": round(dist_m),
            "distance_km": round(dist_m / 1000.0, 2),
            "duration_s": round(duration_s),
            "eta_minutes": round(duration_s / 60.0, 1),
        }

        # Thermal metrics if TempGrid is attached
        if grid is not None:
            thermal_metrics = route_temp_stats(
                path=path,
                G=G,
                G_nodes=G_nodes,
                grid=grid,
                safe_threshold=safe_threshold,
            )
            route_data.update(thermal_metrics)

        results.append(route_data)

    return results
