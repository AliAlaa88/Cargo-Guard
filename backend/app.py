from flask import Flask, request, jsonify, send_from_directory
from graph_loader import get_graph
from routing import find_routes

import osmnx as ox

app = Flask(__name__)
print("Loading graph, this may take a moment on first run...")
G_multi = get_graph()
print("Converting graph to DiGraph...")
G = ox.convert.to_digraph(G_multi, weight="length")
print("Ready!")

@app.route("/")
def index():
    return send_from_directory("../frontend", "index.html")

@app.route("/style.css")
def style():
    return send_from_directory("../frontend", "style.css")

@app.route("/map.js")
def map_js():
    return send_from_directory("../frontend", "map.js")

@app.route("/health")
def health():
    return jsonify(status="ok", nodes=len(G.nodes), edges=len(G.edges))

@app.route("/route", methods=["POST"])
def route():
    data = request.get_json()
    if not data or not all(k in data for k in ("src_lat", "src_lng", "dst_lat", "dst_lng")):
        return jsonify(error="Missing coordinates"), 400
        
    try:
        routes = find_routes(
            G,
            float(data["src_lat"]), float(data["src_lng"]),
            float(data["dst_lat"]), float(data["dst_lng"])
        )
        if not routes:
            return jsonify(error="No route found"), 404
        return jsonify(routes=routes)
    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    app.run(port=5000, debug=False)
