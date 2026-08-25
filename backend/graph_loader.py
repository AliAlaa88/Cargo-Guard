import os
import json
import osmnx as ox

GRAPH_PATH = "data/rhode_island.graphml"
BOUNDARY_PATH = "data/rhode_island_boundary.json"
PLACE_NAME = "Rhode Island, USA"

def get_graph():
    if os.path.exists(GRAPH_PATH):
        return ox.load_graphml(GRAPH_PATH)

    os.makedirs("data", exist_ok=True)
    G = ox.graph_from_place(PLACE_NAME, network_type="drive")
    ox.distance.add_edge_lengths(G)
    # Phase 1.4: enrich with speed (kph) and travel time (seconds)
    ox.add_edge_speeds(G)
    ox.add_edge_travel_times(G)
    ox.save_graphml(G, GRAPH_PATH)
    return G


def get_boundary():
    """Return GeoJSON dict for Rhode Island boundary."""
    if os.path.exists(BOUNDARY_PATH):
        with open(BOUNDARY_PATH, "r") as f:
            return json.load(f)

    os.makedirs("data", exist_ok=True)
    gdf = ox.geocode_to_gdf(PLACE_NAME)
    geo_interface = gdf.geometry.iloc[0].__geo_interface__
    with open(BOUNDARY_PATH, "w") as f:
        json.dump(geo_interface, f)
    return geo_interface


def get_graph_info(G):
    """Return a metadata dict about the loaded graph."""
    nodes = G.nodes(data=True)
    lats = [d["y"] for _, d in nodes]
    lons = [d["x"] for _, d in nodes]
    return {
        "place": PLACE_NAME,
        "nodes": len(G.nodes),
        "edges": len(G.edges),
        "bbox": {
            "min_lat": round(min(lats), 6),
            "max_lat": round(max(lats), 6),
            "min_lng": round(min(lons), 6),
            "max_lng": round(max(lons), 6),
        },
        "boundary": get_boundary()
    }

