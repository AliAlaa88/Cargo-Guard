"""
decision_engine.py
==================
Deterministic Decision Engine for Cargo-Guard (Phase 3.6).

All logic is rule-based and configuration-driven. No LLM numbers.
The AI agent calls these functions and explains the structured results
in natural language. It does NOT produce verdicts or thresholds itself.

Public API
----------
compare_routes(routes, cargo_profile, deadline_minutes)         -> RouteComparison
select_best_route(routes, cargo_profile, deadline_minutes)      -> RouteDecision
evaluate_tradeoff(current, candidate, cargo_profile, deadline)  -> TradeoffResult
should_reroute(current, alt, cargo_profile, progress, deadline) -> RerouteDecision
evaluate_departure_time(windows, cargo_profile, deadline)       -> DepartureDecision

Route dict contract (from routing.find_routes + cost.route_temp_stats)
----------------------------------------------------------------------
route_id, eta_minutes, distance_km, thermal_exposure,
thermal_stress_score, risk_level, avg_temp_c,
max_temp_c (or peak_temperature)
Optional: cargo_constraint_violations  list[str]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cargo_profiles import CargoProfile


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_RISK_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2}


def _risk_rank(level: str) -> int:
    return _RISK_RANK.get(str(level).lower(), 1)


def _peak(r: dict) -> float:
    return float(r.get("peak_temperature") or r.get("max_temp_c") or r.get("avg_temp_c", 20.0))


def _exp(r: dict) -> float:
    return float(r.get("thermal_exposure", 0.0))


def _stress(r: dict) -> float:
    return float(r.get("thermal_stress_score", 0.0))


def _risk(r: dict) -> str:
    return str(r.get("risk_level", "moderate")).lower()


# ---------------------------------------------------------------------------
# Tunable thresholds  (edit here, not in prompts or LLM calls)
# ---------------------------------------------------------------------------

MIN_EXPOSURE_REDUCTION_DM  = 10.0   # min absolute DegMin reduction to justify reroute
MIN_EXPOSURE_REDUCTION_PCT = 15.0   # min relative % reduction to justify reroute
MIN_STRESS_IMPROVEMENT     = 15.0   # 0-100 stress score improvement threshold
MAX_ETA_RATIO_HARD_LIMIT   = 2.0    # alternative must be < 2x current ETA (hard cap)
LATE_TRIP_THRESHOLD_PCT    = 80.0   # suppress reroute if trip >= this % complete
MIN_WINDOW_IMPROVEMENT_PCT = 20.0   # min exposure reduction % to prefer delayed departure


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RouteScore:
    """Computed composite score and feasibility metadata for one route."""
    route_id: str
    eta_minutes: float
    distance_km: float
    thermal_exposure: float
    thermal_stress_score: float
    risk_level: str
    peak_temp_c: float
    cargo_violations: list[str]
    composite_score: float       # lower = better
    feasible: bool
    infeasibility_reasons: list[str]


@dataclass
class RouteComparison:
    """Full comparative ranking of all candidate routes."""
    scores: list[RouteScore]
    recommended_route_id: str
    comparison_summary: list[dict]
    cargo_profile_name: str


@dataclass
class RouteDecision:
    """Final route selection decision."""
    selected_route_id: str
    reason: str
    action: Literal["USE_ROUTE", "NO_FEASIBLE_ROUTE"]
    scores: list[RouteScore]
    warnings: list[str]


@dataclass
class TradeoffResult:
    """Quantified ETA / exposure / risk trade-off between two routes."""
    eta_delta_minutes: float
    exposure_delta_pct: float        # negative = alternative is better
    stress_delta: float              # negative = alternative is better
    risk_improved: bool
    risk_levels: dict                # {"current": str, "alternative": str}
    eta_within_cargo_limit: bool
    eta_within_deadline: bool
    significant_exposure_reduction: bool
    verdict: Literal["REROUTE", "DO_NOT_REROUTE", "MARGINAL"]
    reason: str


@dataclass
class RerouteDecision:
    """Active-trip rerouting decision."""
    action: Literal["REROUTE", "CONTINUE", "OPERATOR_REQUIRED"]
    reason: str
    tradeoff: TradeoffResult | None
    urgency: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    late_trip_penalty_applied: bool


@dataclass
class DepartureWindow:
    """A candidate departure time with associated route metrics."""
    label: str
    delay_minutes: float
    estimated_exposure: float
    estimated_risk_level: str
    eta_minutes: float
    feasible: bool


@dataclass
class DepartureDecision:
    """Departure-time optimisation result."""
    recommended_window: DepartureWindow
    action: Literal["DEPART_NOW", "DELAY_RECOMMENDED", "NO_FEASIBLE_WINDOW"]
    reason: str
    windows: list[DepartureWindow]


# ---------------------------------------------------------------------------
# Internal scoring
# ---------------------------------------------------------------------------

def _score_route(
    route: dict,
    cargo: CargoProfile,
    deadline_minutes: float | None = None,
) -> RouteScore:
    """
    Weighted composite score (lower = better):

        score = time_priority  * (eta / 60)
              + safety_priority * (stress / 100)
              + violation_penalty

    Feasibility is checked independently of score.
    """
    route_id   = str(route.get("route_id", "unknown"))
    eta        = float(route.get("eta_minutes", 60.0))
    dist       = float(route.get("distance_km", 0.0))
    exposure   = _exp(route)
    stress     = _stress(route)
    risk       = _risk(route)
    peak       = _peak(route)
    violations: list[str] = list(route.get("cargo_constraint_violations") or [])
    infeasible: list[str] = []

    # --- Hard constraint checks ---

    # Peak road temperature significantly above cargo safe max (with 5 C ambient buffer)
    if peak > cargo.safe_max_c + 5.0:
        violations.append(
            f"Peak road temp {peak:.1f} C exceeds cargo safe max "
            f"{cargo.safe_max_c:.1f} C (+5 C buffer)"
        )

    # Cumulative thermal exposure over cargo limit
    if exposure > cargo.max_allowed_exposure:
        violations.append(
            f"Thermal exposure {exposure:.1f} DegMin > cargo limit "
            f"{cargo.max_allowed_exposure:.1f} DegMin"
        )
        infeasible.append("Thermal exposure exceeds cargo limit")

    # Delivery deadline
    if deadline_minutes is not None and eta > deadline_minutes:
        infeasible.append(
            f"ETA {eta:.0f} min exceeds deadline {deadline_minutes:.0f} min"
        )

    # Risk tolerance
    if cargo.risk_tolerance == "strict" and risk == "high":
        infeasible.append("HIGH risk unacceptable for strict cargo")
    elif (
        cargo.risk_tolerance == "moderate"
        and risk == "high"
        and exposure > cargo.max_allowed_exposure * 0.8
    ):
        infeasible.append(
            "HIGH risk with near-limit exposure unacceptable for moderate cargo"
        )

    feasible = len(infeasible) == 0
    violation_penalty = len(violations) * 0.5 + (2.0 if not feasible else 0.0)
    composite = (
        cargo.time_priority * (eta / 60.0)
        + cargo.safety_priority * (stress / 100.0)
        + violation_penalty
    )

    return RouteScore(
        route_id=route_id,
        eta_minutes=eta,
        distance_km=dist,
        thermal_exposure=exposure,
        thermal_stress_score=stress,
        risk_level=risk,
        peak_temp_c=peak,
        cargo_violations=violations,
        composite_score=round(composite, 4),
        feasible=feasible,
        infeasibility_reasons=infeasible,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare_routes(
    routes: list[dict],
    cargo_profile: CargoProfile,
    deadline_minutes: float | None = None,
) -> RouteComparison:
    """
    Score and rank all candidate routes for a cargo profile.

    Parameters
    ----------
    routes:
        Route dicts from routing.find_routes() enriched with thermal metrics.
    cargo_profile:
        Validated CargoProfile.
    deadline_minutes:
        Optional hard delivery deadline. Routes exceeding it are infeasible.

    Returns
    -------
    RouteComparison with all scores ranked (feasible first, then infeasible)
    and a recommended_route_id pointing to the best option.
    """
    if not routes:
        return RouteComparison([], "", [], cargo_profile.name)

    scores = [_score_route(r, cargo_profile, deadline_minutes) for r in routes]
    feasible   = sorted([s for s in scores if s.feasible],     key=lambda s: s.composite_score)
    infeasible = sorted([s for s in scores if not s.feasible], key=lambda s: s.composite_score)
    ranked = feasible + infeasible
    best = ranked[0]

    summaries = []
    for s in scores:
        if s.route_id == best.route_id:
            summaries.append({"route_id": s.route_id, "is_recommended": True, "vs_best": None})
            continue
        eta_delta = round(s.eta_minutes - best.eta_minutes, 1)
        exp_delta_pct = (
            round(
                (s.thermal_exposure - best.thermal_exposure) / best.thermal_exposure * 100.0, 1
            )
            if best.thermal_exposure > 0
            else 0.0
        )
        summaries.append({
            "route_id": s.route_id,
            "is_recommended": False,
            "vs_best": {
                "eta_delta_minutes": eta_delta,
                "exposure_delta_pct": exp_delta_pct,
                "stress_delta": round(s.thermal_stress_score - best.thermal_stress_score, 1),
                "risk_this": s.risk_level,
                "risk_best": best.risk_level,
            },
        })

    return RouteComparison(
        scores=ranked,
        recommended_route_id=best.route_id,
        comparison_summary=summaries,
        cargo_profile_name=cargo_profile.name,
    )


def select_best_route(
    routes: list[dict],
    cargo_profile: CargoProfile,
    deadline_minutes: float | None = None,
) -> RouteDecision:
    """
    Select the single best route.

    Priority order:
      1. Filter infeasible routes.
      2. Pick lowest composite score among feasible routes.
      3. If no feasible route exists, return the least-bad infeasible route
         with action=NO_FEASIBLE_ROUTE.
    """
    comparison = compare_routes(routes, cargo_profile, deadline_minutes)
    warnings: list[str] = []

    if not comparison.scores:
        return RouteDecision("", "No routes provided.", "NO_FEASIBLE_ROUTE", [], [])

    feasible = [s for s in comparison.scores if s.feasible]

    if not feasible:
        best = comparison.scores[0]
        reasons = "; ".join(best.infeasibility_reasons)
        warnings.append(
            f"No fully feasible route for '{cargo_profile.name}'. "
            f"Best available ({best.route_id}) violates: {reasons}."
        )
        return RouteDecision(
            selected_route_id=best.route_id,
            reason=(
                f"All routes exceed cargo constraints for {cargo_profile.name}. "
                f"Best option is {best.route_id} -- ETA {best.eta_minutes:.0f} min, "
                f"exposure {best.thermal_exposure:.1f} DegMin. Operator review recommended."
            ),
            action="NO_FEASIBLE_ROUTE",
            scores=comparison.scores,
            warnings=warnings,
        )

    best = feasible[0]
    runner_up = feasible[1] if len(feasible) > 1 else None

    if best.cargo_violations:
        warnings.extend(best.cargo_violations)

    parts = [
        f"Selected {best.route_id} for {cargo_profile.name}.",
        f"ETA: {best.eta_minutes:.0f} min, "
        f"exposure: {best.thermal_exposure:.1f} DegMin, "
        f"risk: {best.risk_level.upper()}.",
    ]
    if runner_up:
        eta_diff = runner_up.eta_minutes - best.eta_minutes
        exp_diff = runner_up.thermal_exposure - best.thermal_exposure
        parts.append(
            f"Next alternative ({runner_up.route_id}) is "
            f"{abs(eta_diff):.0f} min {'slower' if eta_diff > 0 else 'faster'} "
            f"with {abs(exp_diff):.1f} DegMin "
            f"{'more' if exp_diff > 0 else 'less'} exposure."
        )

    return RouteDecision(
        selected_route_id=best.route_id,
        reason=" ".join(parts),
        action="USE_ROUTE",
        scores=comparison.scores,
        warnings=warnings,
    )


def evaluate_tradeoff(
    current: dict,
    candidate: dict,
    cargo_profile: CargoProfile,
    deadline_minutes: float | None = None,
) -> TradeoffResult:
    """
    Quantify the ETA / exposure / risk trade-off between the current route
    and a candidate alternative. Returns a deterministic REROUTE / DO_NOT_REROUTE
    / MARGINAL verdict with a reason string.

    Reroute rules (all must be satisfied):
      - Candidate ETA within cargo's max_eta_increase_pct limit  (or ultra-sensitive exception).
      - Candidate ETA within hard deadline (if set).
      - Exposure reduction >= MIN_EXPOSURE_REDUCTION_PCT % AND >= MIN_EXPOSURE_REDUCTION_DM DegMin.
      - Risk level improves OR stress score drops by >= MIN_STRESS_IMPROVEMENT.

    Examples from the spec:
      current: ETA=31, risk=HIGH  |  alt: ETA=34, risk=LOW  =>  +3 min, -61% exposure => REROUTE
      current: ETA=31             |  alt: ETA=42, risk 8% better  => DO_NOT_REROUTE
    """
    cur_eta    = float(current.get("eta_minutes", 60.0))
    alt_eta    = float(candidate.get("eta_minutes", 60.0))
    cur_exp    = _exp(current)
    alt_exp    = _exp(candidate)
    cur_stress = _stress(current)
    alt_stress = _stress(candidate)
    cur_risk   = _risk(current)
    alt_risk   = _risk(candidate)

    eta_delta     = round(alt_eta - cur_eta, 1)
    stress_delta  = round(alt_stress - cur_stress, 1)
    exp_delta_pct = (
        round((alt_exp - cur_exp) / cur_exp * 100.0, 1) if cur_exp > 0 else 0.0
    )
    risk_improved = _risk_rank(alt_risk) < _risk_rank(cur_risk)

    max_eta_abs         = cur_eta * (1.0 + cargo_profile.max_eta_increase_pct / 100.0)
    eta_within_cargo    = alt_eta <= max_eta_abs
    eta_within_deadline = (deadline_minutes is None) or (alt_eta <= deadline_minutes)

    sig_exp_reduction = (
        exp_delta_pct <= -MIN_EXPOSURE_REDUCTION_PCT
        and (cur_exp - alt_exp) >= MIN_EXPOSURE_REDUCTION_DM
    )
    sig_stress_improvement = stress_delta <= -MIN_STRESS_IMPROVEMENT

    # --- Verdict logic ---

    if not eta_within_deadline:
        verdict = "DO_NOT_REROUTE"
        reason  = (
            f"Alternative ETA ({alt_eta:.0f} min) exceeds delivery deadline "
            f"({deadline_minutes:.0f} min). Do not reroute."
        )

    elif not eta_within_cargo:
        risk_drop = _risk_rank(cur_risk) - _risk_rank(alt_risk)
        hard_ok   = alt_eta <= cur_eta * MAX_ETA_RATIO_HARD_LIMIT
        if cargo_profile.thermal_sensitivity >= 0.85 and risk_drop >= 2 and hard_ok:
            verdict = "REROUTE"
            reason  = (
                f"ETA increase (+{eta_delta:.0f} min) exceeds cargo limit, but risk drops "
                f"{cur_risk.upper()} -> {alt_risk.upper()} for ultra-sensitive cargo. "
                f"Rerouting is justified."
            )
        else:
            verdict = "DO_NOT_REROUTE"
            reason  = (
                f"Alternative ETA (+{eta_delta:.0f} min) exceeds the "
                f"{cargo_profile.max_eta_increase_pct:.0f}% cargo limit. Do not reroute."
            )

    elif sig_exp_reduction and (risk_improved or sig_stress_improvement):
        verdict  = "REROUTE"
        risk_str = f"Risk: {cur_risk.upper()} -> {alt_risk.upper()}. " if risk_improved else ""
        reason   = (
            f"+{eta_delta:.0f} min, "
            f"{abs(exp_delta_pct):.0f}% thermal exposure reduction. "
            f"{risk_str}Rerouting is justified."
        )

    elif exp_delta_pct >= -MIN_EXPOSURE_REDUCTION_PCT and not risk_improved:
        verdict = "DO_NOT_REROUTE"
        reason  = (
            f"Exposure reduction only {abs(exp_delta_pct):.0f}% "
            f"(minimum required: {MIN_EXPOSURE_REDUCTION_PCT:.0f}%) "
            f"with no risk-level improvement. Do not reroute."
        )

    else:
        verdict = "MARGINAL"
        reason  = (
            f"+{eta_delta:.0f} min delay, {abs(exp_delta_pct):.0f}% exposure reduction. "
            f"Risk: {cur_risk.upper()} -> {alt_risk.upper()}. "
            f"Marginal trade-off -- operator judgment recommended."
        )

    return TradeoffResult(
        eta_delta_minutes=eta_delta,
        exposure_delta_pct=exp_delta_pct,
        stress_delta=stress_delta,
        risk_improved=risk_improved,
        risk_levels={"current": cur_risk, "alternative": alt_risk},
        eta_within_cargo_limit=eta_within_cargo,
        eta_within_deadline=eta_within_deadline,
        significant_exposure_reduction=sig_exp_reduction,
        verdict=verdict,
        reason=reason,
    )


def should_reroute(
    current: dict,
    alternative: dict,
    cargo_profile: CargoProfile,
    trip_progress_pct: float = 0.0,
    deadline_minutes: float | None = None,
) -> RerouteDecision:
    """
    Active-trip rerouting decision with trip-progress awareness.

    Additional rules on top of evaluate_tradeoff:
      - Late-trip suppression: if trip >= LATE_TRIP_THRESHOLD_PCT% complete
        and urgency < HIGH, rerouting is suppressed (CONTINUE action).
      - Urgency classification:
          CRITICAL -- HIGH risk + thermal_sensitivity >= 0.85
          HIGH     -- HIGH risk
          MEDIUM   -- MODERATE risk, exposure > 60% of cargo limit
          LOW      -- everything else
    """
    tradeoff   = evaluate_tradeoff(current, alternative, cargo_profile, deadline_minutes)
    cur_risk   = _risk(current)
    late_trip  = trip_progress_pct >= LATE_TRIP_THRESHOLD_PCT

    if cur_risk == "high" and cargo_profile.thermal_sensitivity >= 0.85:
        urgency: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "CRITICAL"
    elif cur_risk == "high":
        urgency = "HIGH"
    elif cur_risk == "moderate" and _exp(current) > cargo_profile.max_allowed_exposure * 0.6:
        urgency = "MEDIUM"
    else:
        urgency = "LOW"

    # Late-trip suppression
    if late_trip and tradeoff.verdict == "REROUTE" and urgency not in ("HIGH", "CRITICAL"):
        return RerouteDecision(
            action="CONTINUE",
            reason=(
                f"Trip is {trip_progress_pct:.0f}% complete. "
                f"Rerouting suppressed (urgency: {urgency}). Continue on current route."
            ),
            tradeoff=tradeoff,
            urgency=urgency,
            late_trip_penalty_applied=True,
        )

    if tradeoff.verdict == "REROUTE":
        action = "REROUTE"
        reason = tradeoff.reason
    elif tradeoff.verdict == "DO_NOT_REROUTE":
        action = "CONTINUE"
        reason = tradeoff.reason
    else:  # MARGINAL
        if urgency in ("HIGH", "CRITICAL"):
            action = "OPERATOR_REQUIRED"
            reason = (
                f"Marginal trade-off with {urgency} urgency. "
                f"Operator intervention recommended. " + tradeoff.reason
            )
        else:
            action = "CONTINUE"
            reason = "Marginal improvement; continuing. " + tradeoff.reason

    return RerouteDecision(
        action=action,
        reason=reason,
        tradeoff=tradeoff,
        urgency=urgency,
        late_trip_penalty_applied=False,
    )


def evaluate_departure_time(
    windows: list[DepartureWindow],
    cargo_profile: CargoProfile,
    deadline_minutes: float | None = None,
) -> DepartureDecision:
    """
    Recommend the best departure window (lowest exposure within deadline).

    Logic:
      1. Mark windows where delay + eta > deadline as infeasible.
      2. If best exposure is at windows[0] (depart now), return DEPART_NOW.
      3. If delay improves exposure by >= MIN_WINDOW_IMPROVEMENT_PCT, return DELAY_RECOMMENDED.
      4. Otherwise return DEPART_NOW.
      5. If no window is feasible, return NO_FEASIBLE_WINDOW.
    """
    if not windows:
        dummy = DepartureWindow("N/A", 0, 0, "unknown", 0, False)
        return DepartureDecision(dummy, "NO_FEASIBLE_WINDOW", "No windows provided.", [])

    for w in windows:
        if deadline_minutes is not None and (w.delay_minutes + w.eta_minutes) > deadline_minutes:
            w.feasible = False

    feasible = [w for w in windows if w.feasible]

    if not feasible:
        return DepartureDecision(
            windows[0],
            "NO_FEASIBLE_WINDOW",
            "All departure windows exceed the delivery deadline.",
            windows,
        )

    now_w  = feasible[0]
    best_w = min(feasible, key=lambda w: w.estimated_exposure)

    if best_w is now_w:
        return DepartureDecision(
            now_w,
            "DEPART_NOW",
            f"Departing now offers lowest exposure "
            f"({now_w.estimated_exposure:.1f} DegMin, "
            f"risk: {now_w.estimated_risk_level.upper()}). No benefit from waiting.",
            windows,
        )

    now_exp = now_w.estimated_exposure
    improvement_pct = (
        (now_exp - best_w.estimated_exposure) / now_exp * 100.0 if now_exp > 0 else 0.0
    )

    if improvement_pct >= MIN_WINDOW_IMPROVEMENT_PCT:
        return DepartureDecision(
            best_w,
            "DELAY_RECOMMENDED",
            f"Delaying by {best_w.delay_minutes:.0f} min reduces exposure by "
            f"{improvement_pct:.0f}% "
            f"({now_exp:.1f} -> {best_w.estimated_exposure:.1f} DegMin). "
            f"Risk: {now_w.estimated_risk_level.upper()} -> "
            f"{best_w.estimated_risk_level.upper()}.",
            windows,
        )

    return DepartureDecision(
        now_w,
        "DEPART_NOW",
        f"Delay reduces exposure by only {improvement_pct:.0f}% "
        f"(minimum required: {MIN_WINDOW_IMPROVEMENT_PCT:.0f}%). Departing now.",
        windows,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers (for Flask JSON responses)
# ---------------------------------------------------------------------------

def route_score_to_dict(s: RouteScore) -> dict:
    return {
        "route_id": s.route_id,
        "eta_minutes": s.eta_minutes,
        "distance_km": s.distance_km,
        "thermal_exposure": s.thermal_exposure,
        "thermal_stress_score": s.thermal_stress_score,
        "risk_level": s.risk_level,
        "peak_temp_c": s.peak_temp_c,
        "cargo_violations": s.cargo_violations,
        "composite_score": s.composite_score,
        "feasible": s.feasible,
        "infeasibility_reasons": s.infeasibility_reasons,
    }


def tradeoff_to_dict(t: TradeoffResult) -> dict:
    return {
        "eta_delta_minutes": t.eta_delta_minutes,
        "exposure_delta_pct": t.exposure_delta_pct,
        "stress_delta": t.stress_delta,
        "risk_improved": t.risk_improved,
        "risk_levels": t.risk_levels,
        "eta_within_cargo_limit": t.eta_within_cargo_limit,
        "eta_within_deadline": t.eta_within_deadline,
        "significant_exposure_reduction": t.significant_exposure_reduction,
        "verdict": t.verdict,
        "reason": t.reason,
    }


def reroute_decision_to_dict(r: RerouteDecision) -> dict:
    return {
        "action": r.action,
        "reason": r.reason,
        "urgency": r.urgency,
        "late_trip_penalty_applied": r.late_trip_penalty_applied,
        "tradeoff": tradeoff_to_dict(r.tradeoff) if r.tradeoff else None,
    }


def departure_decision_to_dict(d: DepartureDecision) -> dict:
    def _w(w: DepartureWindow) -> dict:
        return {
            "label": w.label,
            "delay_minutes": w.delay_minutes,
            "estimated_exposure": w.estimated_exposure,
            "estimated_risk_level": w.estimated_risk_level,
            "eta_minutes": w.eta_minutes,
            "feasible": w.feasible,
        }
    return {
        "action": d.action,
        "reason": d.reason,
        "recommended_window": _w(d.recommended_window),
        "windows": [_w(w) for w in d.windows],
    }

