import os
import osmnx as ox

GRAPH_PATH = "data/rhode_island.graphml"

def get_graph():
    if os.path.exists(GRAPH_PATH):
        return ox.load_graphml(GRAPH_PATH)
    
    os.makedirs("data", exist_ok=True)
    G = ox.graph_from_place("Rhode Island, USA", network_type="drive")
    ox.distance.add_edge_lengths(G)
    ox.save_graphml(G, GRAPH_PATH)
    return G
