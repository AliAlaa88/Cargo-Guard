"""
cargo_profiles.py
──────────────────────────────────────────────────────────────
Structured Pydantic models and Knowledge Base tool for
cargo-specific cold-chain thermal guidelines.

Provides:
  • CargoProfile: Validated Pydantic schema for routing parameters.
  • lookup_cargo_guidelines(): Tool callable by the AI Agent to query
    the JSON knowledge base.
  • Built-in profile loaders and helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

KNOWLEDGE_BASE_PATH = Path(__file__).parent / "data" / "cargo_profiles.json"


class CargoProfile(BaseModel):
    """
    Strictly validated cargo thermal specification used by the deterministic
    routing and decision engine.
    """
    name: str = Field(..., description="Display name of the cargo profile")
    category: str = Field(..., description="Cargo category (e.g. pharmaceutical, frozen_food, fresh_produce, ambient_freight, custom)")
    safe_min_c: float = Field(..., description="Minimum safe cargo internal temperature in °C")
    safe_max_c: float = Field(..., description="Maximum safe cargo internal temperature in °C")
    ambient_trigger_c: float = Field(..., description="Ambient road surface temperature in °C where heat penalty activates")
    thermal_sensitivity: float = Field(..., ge=0.0, le=1.0, description="Sensitivity index from 0.0 (impervious) to 1.0 (extreme)")
    risk_tolerance: Literal["strict", "moderate", "lenient"] = Field("moderate", description="Operational risk tolerance level")
    max_allowed_exposure: float = Field(..., ge=0.0, description="Maximum acceptable cumulative Degree-Minutes above threshold")
    time_priority: float = Field(..., ge=0.0, le=1.0, description="Weight given to shortest travel time (0.0 to 1.0)")
    safety_priority: float = Field(..., ge=0.0, le=1.0, description="Weight given to thermal safety (0.0 to 1.0)")
    max_eta_increase_pct: float = Field(15.0, ge=0.0, description="Maximum acceptable ETA increase % when detouring for safety")
    routing_alpha: float = Field(0.08, ge=0.0, le=1.0, description="Cost penalty multiplier per °C excess")
    description: str = Field("", description="Human-readable description or handling instructions")

    model_config = {"extra": "ignore"}


def lookup_cargo_guidelines(query: str) -> dict:
    """
    Tool function for the AI Agent:
    Search the cold-chain knowledge base JSON file for temperature rules,
    tolerances, and sensitivity guidelines matching a user's cargo query.

    Parameters
    ----------
    query:
        Keywords or description of the shipment (e.g. "mRNA vaccines", "ice cream", "fresh strawberries", "dry freight").

    Returns
    -------
    dict:
        Matching guideline records with safe temperature bands, sensitivity, and handling constraints.
    """
    if not KNOWLEDGE_BASE_PATH.exists():
        return {"error": "Cargo guidelines database not found", "matches": []}

    try:
        with KNOWLEDGE_BASE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return {"error": f"Failed to read guidelines: {exc}", "matches": []}

    guidelines = data.get("guidelines", [])
    query_lower = query.lower().strip()

    if not query_lower:
        return {"guidelines_count": len(guidelines), "matches": guidelines}

    matches = []
    for g in guidelines:
        name_match = query_lower in g.get("name", "").lower()
        cat_match = query_lower in g.get("category", "").lower()
        alias_match = any(query_lower in alias.lower() or alias.lower() in query_lower for alias in g.get("aliases", []))
        desc_match = query_lower in g.get("description", "").lower()

        if name_match or cat_match or alias_match or desc_match:
            matches.append(g)

    # If no direct match, return all available categories so LLM can choose the closest match
    if not matches:
        return {
            "message": f"No exact match for '{query}'. Available reference categories listed below.",
            "available_categories": [g.get("name") for g in guidelines],
            "matches": guidelines
        }

    return {"query": query, "match_count": len(matches), "matches": matches}


def get_all_builtin_profiles() -> list[dict]:
    """Return all pre-configured cargo profile templates."""
    if not KNOWLEDGE_BASE_PATH.exists():
        return []
    with KNOWLEDGE_BASE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("guidelines", [])


def get_profile_by_id(profile_id: str) -> CargoProfile | None:
    """Get a specific built-in CargoProfile by ID."""
    all_profiles = get_all_builtin_profiles()
    for p in all_profiles:
        if p.get("id") == profile_id:
            return CargoProfile(**p)
    return None
