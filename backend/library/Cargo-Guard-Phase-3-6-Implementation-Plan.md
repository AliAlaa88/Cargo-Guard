# Cargo-Guard — Phase 3+ Implementation Plan

## Goal

Transform Cargo-Guard from a temperature-aware routing system into an **autonomous AI logistics agent** that can:

- Understand cargo-specific thermal requirements.
- Evaluate route safety using cumulative thermal exposure.
- Compare safety, ETA, distance, and cargo risk.
- Explain route decisions in natural language.
- Monitor an active delivery in real time.
- Detect changing thermal conditions and incidents.
- Autonomously recommend or execute rerouting.
- Recommend delaying departure when waiting is safer than rerouting.
- Fall back to operator intervention when no safe option exists.

> **Core principle:** Keep routing, thermal calculations, constraints, and safety decisions deterministic. The AI agent should reason over structured results, orchestrate actions, and explain decisions rather than inventing mathematical routing parameters.

---

# Current State

## Phase 1 — Routing Foundation ✅

- OSM road network.
- ~46,982 nodes / ~120,132 edges.
- Speed limits and travel times.
- Top-K alternative routes with NetworkX.
- Interactive Leaflet map.
- Origin/destination selection.
- Rhode Island boundary masking.

## Phase 2 — Thermal Intelligence ✅

- FortyGuard tOS integration.
- ~349,514 cached thermal tiles.
- Shapely STRtree for fast spatial temperature lookup.
- Shortest / Fastest / Temp-Safe routing.
- Thermal cost function.
- Live thermal heatmap.
- Route average/peak temperature.
- Thermal risk badges.

## Phase 2 Refinements Before Phase 3

Do not rewrite Phase 2. Refactor only where necessary:

1. Extend thermal scoring from peak-temperature-focused routing to **cumulative thermal exposure**.
2. Make route results return richer metrics.
3. Prepare the thermal layer for time-dependent temperatures.
4. Keep the current routing implementation as the deterministic routing foundation.

---

# Target Architecture

```text
                         ┌──────────────────────────────┐
                         │       AI LOGISTICS AGENT     │
                         │                              │
                         │ Cargo understanding          │
                         │ Situation reasoning          │
                         │ Decision explanation         │
                         │ Action orchestration         │
                         └───────────────┬──────────────┘
                                         │
                         ┌───────────────▼──────────────┐
                         │      DECISION ENGINE         │
                         │                              │
                         │ Route selection              │
                         │ Reroute decision             │
                         │ Departure-time optimization  │
                         │ Trade-off evaluation         │
                         └───────────────┬──────────────┘
                                         │
                ┌────────────────────────┼─────────────────────┐
                │                        │                     │
                ▼                        ▼                     ▼
        ┌───────────────┐       ┌────────────────┐    ┌────────────────┐
        │ ROUTING       │       │ THERMAL ENGINE │    │ CARGO ENGINE   │
        │               │       │                │    │                │
        │ OSM/Provider  │       │ FortyGuard     │    │ Cargo profiles │
        │ Alternatives  │       │ Heat exposure  │    │ Constraints     │
        │ Route metrics │       │ Forecast       │    │ Thermal model  │
        └───────────────┘       └────────────────┘    └────────────────┘
                │                        │                     │
                └────────────────────────┼─────────────────────┘
                                         │
                               ┌─────────▼──────────┐
                               │ DELIVERY MONITOR   │
                               │                    │
                               │ GPS / telemetry    │
                               │ Trip state         │
                               │ Incident simulator │
                               │ SSE                │
                               └─────────┬──────────┘
                                         │
                                         ▼
                               ┌───────────────────┐
                               │ FRONTEND DASHBOARD│
                               │                   │
                               │ Map               │
                               │ Agent reasoning   │
                               │ Risk              │
                               │ Live trip         │
                               │ Reroute alerts    │
                               └───────────────────┘
```

---

# Phase 3 — Cargo-Aware Risk & Decision Engine

## Objective

Make route optimization depend on **what is being transported**, not only environmental conditions.

The system should answer:

> Given the cargo, route, temperature, travel time, and expected thermal exposure, which option is safest while respecting delivery constraints?

---

## 3.1 Cargo Profiles

### Create / update

```text
backend/agent.py
backend/cargo_profiles.py   # recommended separation if agent.py becomes too large
```

### Built-in profiles

#### Pharmaceuticals / Biologics

- Safe range: 2–8°C where applicable.
- Very high thermal sensitivity.
- Strong safety priority.
- Strict maximum thermal exposure.
- Large penalty for unsafe exposure.

#### Frozen Goods / Ice Cream

- Very high sensitivity.
- Frozen-temperature requirements.
- Strong safety priority.

#### Fresh Produce / Flowers

- Moderate thermal sensitivity.
- Balanced safety/time trade-off.

#### Standard Freight

- Low thermal sensitivity.
- Strong speed/time priority.

### Profile structure

Use a structured model rather than only `alpha` and `threshold`:

```python
CargoProfile(
    name,
    safe_min,
    safe_max,
    thermal_sensitivity,
    risk_tolerance,
    max_allowed_exposure,
    time_priority,
    safety_priority,
    max_eta_increase
)
```

---

## 3.2 Custom Cargo Input

Support:

- Custom minimum/maximum temperature.
- Cargo sensitivity.
- Maximum allowed exposure.
- Delivery deadline.
- Free-text requirements.

Example:

```text
"Transporting insulin, priority on lowest heat exposure."
```

The AI can convert this into a structured cargo configuration, but the resulting configuration must be validated before being used by the deterministic engine.

---

# 3.3 Cumulative Thermal Exposure

## Objective

Move beyond peak temperature.

Current concept:

```text
Cost(edge) =
    travel_time *
    (1 + alpha * max(0, temperature - threshold))
```

Add cumulative exposure:

\[
ThermalExposure =
\sum_e
\max(0, T_e - T_{safe})
	imes time_e
\]

For a continuous/time-aware model:

\[
ThermalExposure =
\int_0^T
f(T_{road}(t), Cargo)\,dt
\]

### Important

Call this a:

- Thermal Exposure Score
- Thermal Stress Score
- Cargo Thermal Risk Index

Do **not** call it an exact spoilage probability unless a validated degradation model exists.

---

# 3.4 Separate Road Temperature and Cargo Temperature

Do not assume:

```text
road temperature = cargo temperature
```

Add a simplified thermal-response model.

Concept:

\[
T_{cargo}(t+\Delta t)
=
T_{cargo}(t)
+
k(T_{ambient}-T_{cargo}(t))\Delta t
\]

Where `k` represents thermal response / insulation / refrigeration effectiveness.

The model can initially be simulated and does not need to represent full refrigeration physics.

Expose:

```text
Road Temperature
Ambient Truck Temperature
Cargo Core Temperature
Cargo Safe Range
Thermal Exposure Duration
```

---

# 3.5 Rich Route Metrics

Update `routing.py` and `cost.py` so every candidate route can return:

```text
distance
ETA
average_temperature
peak_temperature
thermal_exposure
thermal_risk_score
cargo_constraint_violations
```

Example:

```json
{
  "route_id": "route_1",
  "distance_km": 18.4,
  "eta_minutes": 31,
  "average_temperature": 29.4,
  "peak_temperature": 37.2,
  "thermal_exposure": 91.4,
  "risk_score": 0.08
}
```

This structured output becomes the input to the decision engine.

---

# 3.6 Decision Engine

## New module

```text
backend/decision_engine.py
```

Responsibilities:

```text
compare_routes()
select_best_route()
should_reroute()
evaluate_tradeoff()
evaluate_departure_time()
```

The decision engine should consider:

- Thermal exposure.
- Cargo sensitivity.
- ETA.
- Distance.
- Delivery deadline.
- Risk reduction.
- Maximum acceptable delay.
- Current trip progress.

Example:

```text
Current route:
ETA = 31 min
Risk = HIGH

Alternative:
ETA = 34 min
Risk = LOW

Decision:
+3 min
-61% thermal exposure

→ REROUTE
```

But:

```text
Alternative:
ETA = 42 min
Risk reduction = 8%

→ DO NOT REROUTE
```

The engine should use deterministic rules/configuration rather than LLM-generated numerical decisions.

---

# 3.7 AI Agent Role

The AI agent should:

1. Understand cargo requirements.
2. Interpret structured route results.
3. Understand current delivery state.
4. Decide which deterministic action/tool should be invoked.
5. Explain the decision.
6. Communicate alerts.
7. Orchestrate rerouting and delivery-window optimization.

The AI should **not** directly calculate:

- shortest paths,
- thermal integrals,
- route costs,
- safety thresholds,
- arbitrary alpha values.

Those remain deterministic.

Example agent output:

> Route 2 is recommended for the vaccine shipment. It adds 3 minutes but reduces projected thermal exposure by 50%. The current route passes through a high-heat corridor, increasing the risk of exceeding the cargo's safe temperature range.

---

# Phase 4 — Autonomous Delivery Monitoring & Rerouting

## Objective

Turn Cargo-Guard into an active logistics agent rather than a route-planning application.

---

# 4.1 Delivery State Machine

Implement:

```text
PLANNED
   ↓
ACTIVE
   ↓
MONITORING
   ↓
RISK_DETECTED
   ↓
EVALUATING
   ↓
REROUTING
   ↓
MONITORING
   ↓
DELIVERED
```

Optional state:

```text
DELAY_RECOMMENDED
```

---

# 4.2 Live Delivery Simulator

## New module

```text
backend/monitor.py
```

Simulate:

- GPS movement.
- Current route position.
- Vehicle speed.
- Elapsed time.
- ETA.
- Road temperature.
- Ambient temperature.
- Cargo core temperature.
- Thermal exposure.
- Risk score.

Frontend should display a moving truck and live telemetry.

---

# 4.3 Incident Simulator

Create controlled incidents such as:

```text
Heatwave spike
Traffic congestion
High-temperature road corridor
Unexpected thermal increase
Route blockage
```

Each incident should modify the environment or travel conditions.

Example:

```text
13:10
Route A thermal risk = LOW

13:12
Heatwave incident detected

Route A thermal risk = HIGH

Agent evaluates alternatives

Route B:
+3 min
-70% thermal exposure

→ REROUTE
```

---

# 4.4 SSE Event Stream

Use Server-Sent Events for the simulation.

Recommended event types:

```text
trip_started
telemetry_update
thermal_risk_changed
incident_detected
reroute_analysis
reroute_recommended
reroute_executed
delivery_window_changed
trip_completed
```

Frontend subscribes to the SSE stream and updates the UI in real time.

---

# 4.5 Autonomous Rerouting Logic

Do not reroute on every small temperature change.

Flow:

```text
Current route
      ↓
Monitor conditions
      ↓
Calculate future risk
      ↓
Unsafe / significantly worse?
      │
   ┌──┴──┐
   NO    YES
   │      │
Continue  Generate alternatives
             ↓
       Evaluate alternatives
             ↓
       Significant improvement?
          │          │
         NO         YES
          │          │
       Continue    Reroute
```

The reroute decision should account for:

```text
risk reduction
+
additional ETA
+
additional distance
+
cargo sensitivity
+
remaining delivery deadline
```

---

# Phase 5 — Delivery Window Optimization

## Objective

Allow the agent to decide whether:

> Reroute now

or:

> Wait and depart later

is the better decision.

Example:

```text
Departure: 13:00
Thermal risk: HIGH

Departure: 14:00
Thermal risk: MEDIUM

Departure: 16:00
Thermal risk: LOW
```

Agent may recommend:

> Delaying departure by 90 minutes is expected to reduce thermal exposure by 64% while remaining within the delivery deadline.

This can initially use simulated time-dependent thermal data.

---

# 5.1 Time-Dependent Thermal Conditions

Prepare the thermal system for:

```text
current_temperature
temperature(t + 5 min)
temperature(t + 10 min)
temperature(t + 20 min)
...
```

The first implementation may use a simulation model over FortyGuard's current thermal data.

The architecture should allow a real forecast provider to replace the simulator later.

---

# Phase 6 — Evaluation, Reliability & Demo

## Objective

Prove that the autonomous agent behaves correctly.

Create reproducible scenarios.

---

## Scenario A — Normal Conditions

Expected:

```text
Fastest ≈ Temp-Safe

Agent should NOT unnecessarily reroute.
```

---

## Scenario B — Hot Corridor

Expected:

```text
Fastest:
ETA = 30 min
Thermal exposure = HIGH

Alternative:
ETA = 34 min
Thermal exposure = LOW

→ Agent selects alternative for sensitive cargo.
```

---

## Scenario C — Mid-Trip Thermal Incident

Expected:

```text
Current route becomes unsafe
        ↓
Agent detects incident
        ↓
Generate alternatives
        ↓
Decision engine evaluates
        ↓
Agent reroutes
```

---

## Scenario D — No Useful Alternative

Expected:

```text
All alternatives unsafe
        ↓
Do NOT pretend rerouting solves the problem
        ↓
Recommend delay / operator intervention
```

---

## Scenario E — Low-Sensitivity Cargo

Expected:

```text
Small thermal difference
        ↓
Agent prioritizes ETA
        ↓
Fast route selected
```

---

## Scenario F — Delivery Deadline Constraint

Expected:

```text
Safe route exceeds deadline
        ↓
Agent considers trade-off
        ↓
Chooses safest feasible option
        ↓
Explains constraint
```

---

# Evaluation Metrics

Compare the autonomous agent against:

### Baseline 1

Shortest route.

### Baseline 2

Fastest route.

### Baseline 3

Static Temp-Safe route.

### Agent

Dynamic cargo-aware route + monitoring + rerouting.

Measure:

```text
Total ETA
Distance
Thermal exposure
Peak cargo temperature
Time outside safe range
Number of reroutes
Avoided unsafe exposure
Deadline violations
```

The goal is to demonstrate that the agent can reduce thermal risk **without blindly sacrificing delivery time**.

---

# Recommended Backend Structure

Target structure:

```text
backend/
├── app.py
│
├── agent.py
├── cargo_profiles.py
├── decision_engine.py
├── monitor.py
│
├── routing.py
├── cost.py
├── thermal_model.py
├── temp_grid.py
│
├── fortyguard_client.py
├── graph_loader.py
│
├── data/
└── requirements.txt
```

If `agent.py` remains small, `cargo_profiles.py` can be merged into it. Keep the separation if the agent grows.

---

# Recommended Implementation Order

## Step 3.0 — Phase 2 Refinement

- [ ] Refactor thermal scoring into reusable functions.
- [ ] Add cumulative thermal exposure.
- [ ] Return richer route metrics.
- [ ] Ensure existing Phase 2 routing still works.

## Step 3.1 — Cargo Profiles

- [ ] Create structured cargo profiles.
- [ ] Add Pharmaceuticals.
- [ ] Add Frozen Goods.
- [ ] Add Produce.
- [ ] Add Standard Freight.
- [ ] Add Custom Cargo.
- [ ] Validate cargo constraints.

## Step 3.2 — Cargo Thermal Model

- [ ] Separate road/ambient/cargo temperatures.
- [ ] Implement simplified thermal response.
- [ ] Calculate time outside safe range.
- [ ] Calculate thermal exposure score.

## Step 3.3 — Decision Engine

- [ ] Create `decision_engine.py`.
- [ ] Compare candidate routes.
- [ ] Implement cargo-aware scoring.
- [ ] Implement `should_reroute()`.
- [ ] Implement trade-off evaluation.
- [ ] Add delivery deadline constraints.

## Step 3.4 — AI Agent

- [ ] Create structured agent input.
- [ ] Create tool/function interfaces.
- [ ] Allow agent to select deterministic actions.
- [ ] Generate natural-language explanations.
- [ ] Keep numerical calculations outside the LLM.

## Step 4.1 — Delivery Simulator

- [ ] Create `monitor.py`.
- [ ] Implement trip state machine.
- [ ] Simulate GPS movement.
- [ ] Simulate vehicle telemetry.
- [ ] Simulate cargo temperature.

## Step 4.2 — SSE

- [ ] Implement SSE endpoint.
- [ ] Emit telemetry events.
- [ ] Emit incident events.
- [ ] Emit risk events.
- [ ] Emit reroute events.
- [ ] Emit completion events.

## Step 4.3 — Autonomous Rerouting

- [ ] Add thermal incidents.
- [ ] Recalculate route during trip.
- [ ] Evaluate alternatives.
- [ ] Trigger reroute only when justified.
- [ ] Update driver/operator UI.

## Step 5.1 — Delivery Window Optimization

- [ ] Add time-dependent thermal simulation.
- [ ] Evaluate different departure times.
- [ ] Respect delivery deadlines.
- [ ] Allow the agent to recommend waiting.

## Step 6.1 — Evaluation

- [ ] Implement reproducible scenarios.
- [ ] Compare against routing baselines.
- [ ] Measure thermal exposure reduction.
- [ ] Measure ETA trade-offs.
- [ ] Test no-alternative situations.
- [ ] Test deadline constraints.

## Step 6.2 — Demo Polish

- [ ] Improve agent reasoning panel.
- [ ] Improve live delivery HUD.
- [ ] Add animated reroute notification.
- [ ] Show before/after route metrics.
- [ ] Show why the agent acted.
- [ ] Add scenario selector for hackathon demo.

---

# Final User Experience

The final demo should tell this story:

```text
1. Select cargo
        ↓
2. Select origin + destination
        ↓
3. Agent analyzes cargo requirements
        ↓
4. Generate multiple routes
        ↓
5. Evaluate ETA + thermal exposure + risk
        ↓
6. Agent selects route
        ↓
7. Explain decision
        ↓
8. Start delivery
        ↓
9. Live vehicle monitoring
        ↓
10. Thermal incident occurs
        ↓
11. Agent detects increasing risk
        ↓
12. Generate alternative routes
        ↓
13. Compare risk vs ETA
        ↓
14. Agent reroutes automatically
        ↓
15. Continue monitoring
        ↓
16. Deliver safely
```

---

# The Core Innovation

Cargo-Guard should evolve through these three levels:

```text
PHASE 1
"What is the fastest route?"
        ↓
PHASE 2
"What is the fastest/safest route given current heat?"
        ↓
PHASE 3+
"What is the best action for THIS cargo,
at THIS time, given current and expected
conditions?"
```

The final system is not simply:

> **AI that explains a route.**

It is:

> **An autonomous logistics agent that continuously evaluates cargo risk, environmental conditions, delivery constraints, and route alternatives, then takes or recommends the best action.**
