import sys
from pathlib import Path

# Inject Azure App Service pre-built packages path if present
_site_packages = Path(__file__).resolve().parent.parent / ".python_packages" / "lib" / "site-packages"
if _site_packages.exists() and str(_site_packages) not in sys.path:
    sys.path.insert(0, str(_site_packages))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import osmnx as ox


from graph_loader import get_graph, get_graph_info
from routing import find_routes
from fortyguard_client import get_ri_heatmap
from temp_grid import TempGrid
from cost import shortest_cost, fastest_cost, temp_safe_cost
from decision_engine import (
    select_best_route, compare_routes, evaluate_tradeoff, should_reroute,
    evaluate_departure_time, DepartureWindow,
    route_score_to_dict, tradeoff_to_dict, reroute_decision_to_dict, departure_decision_to_dict,
)

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


# ── Decision Engine endpoints ──────────────────────────────────────────────────

@app.route("/route/decide", methods=["POST"])
def route_decide():
    """
    Select the best route from pre-computed candidates using the deterministic
    decision engine and optionally explain it with the AI agent.

    POST body:
    {
      "routes": [ ... ],           # list of route dicts from /route
      "cargo_profile": { ... },    # CargoProfile dict
      "deadline_minutes": float,   # optional
      "explain": true              # optional, triggers AI explanation
    }
    """
    from cargo_profiles import CargoProfile
    from agent import explain_route_decision

    data = request.get_json(force=True, silent=True) or {}
    routes = data.get("routes", [])
    cargo_dict = data.get("cargo_profile")
    deadline = data.get("deadline_minutes")
    explain = bool(data.get("explain", False))

    if not routes:
        return jsonify(error="No routes provided"), 400
    if not cargo_dict:
        return jsonify(error="cargo_profile required"), 400

    try:
        cargo = CargoProfile(**cargo_dict)
    except Exception as e:
        return jsonify(error=f"Invalid cargo_profile: {e}"), 400

    try:
        deadline_f = float(deadline) if deadline is not None else None
        decision = select_best_route(routes, cargo, deadline_minutes=deadline_f)
        decision_dict = {
            "selected_route_id": decision.selected_route_id,
            "action": decision.action,
            "reason": decision.reason,
            "warnings": decision.warnings,
            "scores": [route_score_to_dict(s) for s in decision.scores],
        }

        explanation = None
        if explain:
            explanation = explain_route_decision(cargo, decision_dict, routes)

        return jsonify(
            decision=decision_dict,
            explanation=explanation,
            cargo_profile=cargo.model_dump(),
        )
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/route/reroute", methods=["POST"])
def route_reroute():
    """
    Evaluate whether to reroute during an active trip.

    POST body:
    {
      "current_route": { ... },      # current route dict with live thermal metrics
      "alternative_route": { ... },  # candidate alternative route
      "cargo_profile": { ... },      # CargoProfile dict
      "trip_progress_pct": float,    # 0-100, how far along the trip
      "deadline_minutes": float,     # optional remaining deadline
      "explain": true                # optional AI alert
    }
    """
    from cargo_profiles import CargoProfile
    from agent import explain_reroute_decision

    data = request.get_json(force=True, silent=True) or {}
    current   = data.get("current_route")
    alt       = data.get("alternative_route")
    cargo_dict= data.get("cargo_profile")
    progress  = float(data.get("trip_progress_pct", 0.0))
    deadline  = data.get("deadline_minutes")
    explain   = bool(data.get("explain", False))

    if not current or not alt:
        return jsonify(error="current_route and alternative_route required"), 400
    if not cargo_dict:
        return jsonify(error="cargo_profile required"), 400

    try:
        cargo = CargoProfile(**cargo_dict)
    except Exception as e:
        return jsonify(error=f"Invalid cargo_profile: {e}"), 400

    try:
        deadline_f = float(deadline) if deadline is not None else None
        decision = should_reroute(current, alt, cargo, trip_progress_pct=progress, deadline_minutes=deadline_f)
        decision_dict = reroute_decision_to_dict(decision)

        explanation = None
        if explain:
            explanation = explain_reroute_decision(cargo, decision_dict, current, alt)

        return jsonify(
            decision=decision_dict,
            explanation=explanation,
            cargo_profile=cargo.model_dump(),
        )
    except Exception as e:
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    app.run(port=5000, debug=False)
