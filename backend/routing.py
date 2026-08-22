import networkx as nx
import osmnx as ox
from itertools import islice

def find_routes(G, src_lat, src_lng, dst_lat, dst_lng, k=3):
    src_node = ox.distance.nearest_nodes(G, X=src_lng, Y=src_lat)
    dst_node = ox.distance.nearest_nodes(G, X=dst_lng, Y=dst_lat)

    try:
        gen = nx.shortest_simple_paths(G, src_node, dst_node, weight="length")
        paths = list(islice(gen, k))
    except nx.NetworkXNoPath:
        return []

    results = []
    for path in paths:
        coords = []
        dist = 0
        for u, v in zip(path[:-1], path[1:]):
            edge_data = G[u][v]
            dist += edge_data.get("length", 0)
            
            # If the road is curved, it has a 'geometry' LineString attribute
            if "geometry" in edge_data:
                # Extract lat/lng points along the curve (Shapely uses x=lng, y=lat)
                curve_coords = [(y, x) for x, y in edge_data["geometry"].coords]
                coords.extend(curve_coords)
            else:
                # Straight road: just connect the two intersections
                coords.append((G.nodes[u]["y"], G.nodes[u]["x"]))
                coords.append((G.nodes[v]["y"], G.nodes[v]["x"]))
                
        results.append({"coords": coords, "distance_m": round(dist)})
        
    return results
