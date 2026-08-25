"""
agent.py
──────────────────────────────────────────────────────────────
OpenAI AI Agent for Cold-Chain Logistics.

Features:
  • Natural Language Cargo Parser: Converts arbitrary user requirements
    (e.g., "Transporting Pfizer mRNA vaccines from Cranston to Pawtucket")
    into a strictly validated Pydantic CargoProfile.
  • Tool Calling: The AI agent calls `lookup_cargo_guidelines` to query
    the cold-chain guidelines database (data/cargo_profiles.json).
  • Structured Outputs: Uses Pydantic schemas for deterministic type safety.
  • Graceful Fallback: If OpenAI API key is missing or offline, uses local
    semantic keyword matching so routing always continues working.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from cargo_profiles import CargoProfile, lookup_cargo_guidelines

# ── Load environment variables ───────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def _get_openai_client() -> OpenAI | None:
    """Initialize OpenAI client if a valid key is present."""
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("your_openai_api_key"):
        return None
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


# ── Tool Definitions for OpenAI Function Calling ──────────────────────────────
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "lookup_cargo_guidelines",
            "description": "Search the cold-chain guidelines database for standard temperature bounds, sensitivity, risk tolerance, and reefer rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The product or cargo keyword to search (e.g., 'vaccines', 'insulin', 'ice cream', 'strawberries', 'electronics')."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are Cargo-Guard AI, an expert cold-chain logistics and thermal risk engineer.
Your task is to analyze user shipment requirements and generate a strictly validated CargoProfile.

Workflow:
1. Always call the `lookup_cargo_guidelines` tool first to check official temperature and sensitivity rules for the requested cargo.
2. Based on the tool's findings and any specific user instructions (e.g. custom deadlines, extra fragility, special reefer needs), synthesize the final CargoProfile.
3. If the user specifies custom temperature thresholds or deadlines, respect those while keeping safety limits rigorous.
4. Output a clean, JSON-compatible object matching the CargoProfile schema:
   - name: Clear display name
   - category: pharmaceutical | frozen_food | fresh_produce | perishable_food | industrial | ambient_freight | custom
   - safe_min_c: float (°C)
   - safe_max_c: float (°C)
   - ambient_trigger_c: float (°C above which road heat penalty activates)
   - thermal_sensitivity: float between 0.0 and 1.0
   - risk_tolerance: strict | moderate | lenient
   - max_allowed_exposure: float (maximum acceptable degree-minutes)
   - time_priority: float between 0.0 and 1.0
   - safety_priority: float between 0.0 and 1.0
   - max_eta_increase_pct: float (maximum acceptable detour delay %)
   - routing_alpha: float (cost multiplier per °C excess, typically 0.01 for dry freight to 0.30 for ultra-sensitive pharma)
   - description: 1-2 sentences summarizing handling instructions
"""


def parse_cargo_input(user_input: str) -> CargoProfile:
    """
    Parse a natural language cargo requirement into a validated CargoProfile.
    Uses OpenAI Agent with tool-calling when available, or a deterministic fallback.

    Parameters
    ----------
    user_input:
        Free-text description (e.g. "Transporting mRNA vaccines, prioritize lowest heat exposure").

    Returns
    -------
    CargoProfile:
        Pydantic-validated cargo profile instance.
    """
    client = _get_openai_client()

    # If client is configured, run OpenAI Agent with tool-calling
    if client is not None:
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Parse this cargo request and produce a CargoProfile: \"{user_input}\""}
            ]

            # Step 1: Initial model call with tools
            response = client.chat.completions.create(
                model=OPENAI_MODEL_NAME,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.1,
            )

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            # Step 2: Handle tool calling if requested by LLM
            if tool_calls:
                messages.append(response_message)
                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    if function_name == "lookup_cargo_guidelines":
                        args = json.loads(tool_call.function.arguments)
                        tool_result = lookup_cargo_guidelines(args.get("query", ""))
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": json.dumps(tool_result)
                        })

                # Step 3: Get final response with structured output
                final_response = client.chat.completions.create(
                    model=OPENAI_MODEL_NAME,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                final_json_str = final_response.choices[0].message.content or "{}"
                profile_dict = json.loads(final_json_str)

                # If wrapped in a key like {"cargo_profile": {...}}, unwrap it
                if "cargo_profile" in profile_dict:
                    profile_dict = profile_dict["cargo_profile"]

                return CargoProfile(**profile_dict)

            else:
                # If no tool was called, parse direct JSON
                content = response_message.content or "{}"
                profile_dict = json.loads(content)
                if "cargo_profile" in profile_dict:
                    profile_dict = profile_dict["cargo_profile"]
                return CargoProfile(**profile_dict)

        except Exception as exc:
            print(f"[Agent] OpenAI Agent call failed or key invalid ({exc}), falling back to deterministic parser.")

    # ── Deterministic Fallback Parser (Local Knowledge Base Search) ────────────
    return _deterministic_fallback_parser(user_input)


def _deterministic_fallback_parser(user_input: str) -> CargoProfile:
    """
    Deterministic rule-based parser that queries the JSON database directly
    when OpenAI is not connected or offline.
    """
    lookup = lookup_cargo_guidelines(user_input)
    matches = lookup.get("matches", [])

    if matches:
        best_match = matches[0]
        return CargoProfile(
            name=f"Custom ({best_match.get('name')})",
            category=best_match.get("category", "custom"),
            safe_min_c=best_match.get("safe_min_c", 2.0),
            safe_max_c=best_match.get("safe_max_c", 8.0),
            ambient_trigger_c=best_match.get("ambient_trigger_c", 22.0),
            thermal_sensitivity=best_match.get("thermal_sensitivity", 0.8),
            risk_tolerance=best_match.get("risk_tolerance", "strict"),
            max_allowed_exposure=best_match.get("max_allowed_exposure", 20.0),
            time_priority=best_match.get("time_priority", 0.3),
            safety_priority=best_match.get("safety_priority", 0.9),
            max_eta_increase_pct=best_match.get("max_eta_increase_pct", 20.0),
            routing_alpha=best_match.get("routing_alpha", 0.20),
            description=f"Auto-configured from '{user_input}'. {best_match.get('description', '')}"
        )

    # General default profile
    return CargoProfile(
        name="Custom Shipment",
        category="custom",
        safe_min_c=2.0,
        safe_max_c=15.0,
        ambient_trigger_c=25.0,
        thermal_sensitivity=0.6,
        risk_tolerance="moderate",
        max_allowed_exposure=30.0,
        time_priority=0.5,
        safety_priority=0.7,
        max_eta_increase_pct=15.0,
        routing_alpha=0.10,
        description=f"Custom cargo profile generated for: {user_input}"
    )
