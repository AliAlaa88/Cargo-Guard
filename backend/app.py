from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import osmnx as ox

from graph_loader import get_graph, get_graph_info
from routing import find_routes
from fortyguard_client import get_ri_heatmap
from temp_grid import TempGrid
from cost import shortest_cost, fastest_cost, temp_safe_cost

app = Flask(__name__)
CORS(app)

# ── Startup: load graph ───────────────────────────────────────────────────────
print("Loading graph, this may take a moment on first run…")
G_multi = get_graph()
print("Converting graph to DiGraph…")
G = ox.convert.to_digraph(G_multi, weight="length")
G_nodes = G.nodes  # keep a reference to avoid repeated attribute lookups
print("Ready!")

# ── Startup: load FortyGuard heatmap (cached on disk after first fetch) ───────
print("Loading FortyGuard temperature heatmap…")
HEATMAP_POINTS = []
try:
    heatmap_features = get_ri_heatmap()
    TEMP_GRID = TempGrid(heatmap_features)
    
    # Pre-extract point coordinates [lat, lon, temp] for fast frontend rendering
    for idx, feat in enumerate(heatmap_features):
        if idx % 4 != 0:  # sample every 4th tile for high resolution with fast payload
            continue
        geom = feat.get("geometry", {})
        props = feat.get("properties", {})
        temp = props.get("average_temperature") or props.get("temperature")
        if temp is not None and geom.get("type") == "Polygon":
            coords = geom["coordinates"][0]
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            c_lat = sum(lats) / len(lats)
            c_lon = sum(lons) / len(lons)
            HEATMAP_POINTS.append([round(c_lat, 5), round(c_lon, 5), round(float(temp), 2)])

    print(f"Temperature grid ready ({len(heatmap_features)} tiles, {len(HEATMAP_POINTS)} display points).")
except Exception as exc:
    print(f"⚠ FortyGuard heatmap unavailable: {exc}")
    TEMP_GRID = None
    HEATMAP_POINTS = []

# ── Cost-function registry ─────────────────────────────────────────────────────
def _resolve_cost(mode: str, safe_threshold: float = 20.0, alpha: float = 0.08):
    """Return the appropriate (weight_fn, grid) pair for the given mode."""
    grid = TEMP_GRID
    if mode == "shortest":
        return shortest_cost(), grid
    if mode == "fastest":
        return fastest_cost(), grid
    if mode == "temp_safe":
        if TEMP_GRID is None:
            # Graceful degradation: fall back to fastest if heatmap unavailable
            return fastest_cost(), None
        return temp_safe_cost(TEMP_GRID, G_nodes, threshold=safe_threshold, alpha=alpha), grid
    # Default fallback
    return shortest_cost(), grid


# ── Static files ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("../frontend", "index.html")

@app.route("/style.css")
def style():
    return send_from_directory("../frontend", "style.css")

@app.route("/map.js")
def map_js():
    return send_from_directory("../frontend", "map.js")


# ── API endpoints ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify(
        status="ok",
        nodes=len(G.nodes),
        edges=len(G.edges),
        heatmap_tiles=len(HEATMAP_POINTS),
    )


@app.route("/graph/info")
def graph_info():
    """Metadata about the road network + RI boundary polygon for the frontend mask."""
    return jsonify(get_graph_info(G_multi))


@app.route("/temperature/heatmap")
def temperature_heatmap():
    """
    Return the FortyGuard temperature points for the frontend thermal overlay.
    """
    return jsonify(points=HEATMAP_POINTS)


# ── Cargo Profile & Agent Endpoints ───────────────────────────────────────────

@app.route("/cargo/profiles")
def cargo_profiles_list():
    """Return all built-in cargo profile guidelines from the JSON knowledge base."""
    from cargo_profiles import get_all_builtin_profiles
    return jsonify(profiles=get_all_builtin_profiles())


@app.route("/cargo/parse", methods=["POST"])
def cargo_parse():
    """
    Parse a natural language cargo requirement or free-text query into a validated
    Pydantic CargoProfile using the OpenAI Agent with knowledge-base tool calling.

    POST body: { "query": "Transporting insulin at 4C" }
    """
    from agent import parse_cargo_input
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify(error="Empty cargo query"), 400

    try:
        profile = parse_cargo_input(query)
        return jsonify(cargo_profile=profile.model_dump())
    except Exception as exc:
        return jsonify(error=str(exc)), 500


# ── Routing API ────────────────────────────────────────────────────────────────

@app.route("/route", methods=["POST"])
def route():
    """
    POST body:
    {
      "src_lat": float, "src_lng": float,
      "dst_lat": float, "dst_lng": float,
      "mode": "shortest" | "fastest" | "temp_safe",
      "safe_threshold": float (optional, default 20.0),
      "alpha": float (optional, default 0.08),
      "cargo_profile": dict (optional, Pydantic CargoProfile object)
    }
    """
    from cargo_profiles import CargoProfile

    data = request.get_json()
    required = ("src_lat", "src_lng", "dst_lat", "dst_lng")
    if not data or not all(k in data for k in required):
        return jsonify(error="Missing coordinates"), 400

    mode = data.get("mode", "shortest")
    safe_threshold = float(data.get("safe_threshold", 20.0))
    alpha = float(data.get("alpha", 0.08))

    # If a CargoProfile is supplied, override threshold and alpha with cargo-specific values
    cargo_dict = data.get("cargo_profile")
    active_profile = None
    if cargo_dict:
        try:
            active_profile = CargoProfile(**cargo_dict)
            safe_threshold = active_profile.ambient_trigger_c
            alpha = active_profile.routing_alpha
        except Exception as err:
            print(f"Warning: Invalid cargo_profile received ({err}), using defaults.")

    weight_fn, grid = _resolve_cost(mode, safe_threshold=safe_threshold, alpha=alpha)

    try:
        routes = find_routes(
            G,
            float(data["src_lat"]), float(data["src_lng"]),
            float(data["dst_lat"]), float(data["dst_lng"]),
            weight_fn=weight_fn,
            grid=grid,
            safe_threshold=safe_threshold,
        )
        if not routes:
            return jsonify(error="No route found"), 404

        return jsonify(
            routes=routes,
            mode=mode,
            safe_threshold=safe_threshold,
            alpha=alpha,
            cargo_profile=active_profile.model_dump() if active_profile else None,
        )
    except Exception as e:
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    app.run(port=5000, debug=False)
