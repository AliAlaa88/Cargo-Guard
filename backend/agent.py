"""
agent.py
================================================================================
OpenAI AI Agent for Cold-Chain Logistics.

Responsibilities (3.7):
  • Understand cargo requirements via natural language → CargoProfile.
  • Interpret structured route results.
  • Decide which deterministic tool to invoke (decision_engine functions).
  • Explain decisions in plain language.
  • Communicate alerts and orchestrate rerouting / departure-window optimisation.

The AI does NOT calculate: shortest paths, thermal integrals, route costs,
safety thresholds, or alpha values. Those stay in the deterministic layer.
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

load_dotenv(Path(__file__).parent / ".env")

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
OPENAI_BASE_URL  = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def _get_openai_client() -> OpenAI | None:
    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("your_openai_api_key"):
        return None
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


# ── Tool schemas for OpenAI function-calling ──────────────────────────────────

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "lookup_cargo_guidelines",
            "description": "Search the cold-chain knowledge base for temperature bounds, sensitivity, and reefer rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Cargo keyword (e.g. 'vaccines', 'ice cream')."}
                },
                "required": ["query"],
            },
        },
    }
]

# ── System prompts ────────────────────────────────────────────────────────────

CARGO_PARSE_SYSTEM_PROMPT = """You are Cargo-Guard AI, an expert cold-chain logistics engineer.
Your task is to parse user shipment requirements into a validated CargoProfile.

Workflow:
1. Call `lookup_cargo_guidelines` first to fetch official cold-chain rules for the cargo.
2. Synthesise a CargoProfile JSON object from the tool result and user instructions.
3. Output a clean JSON object with these fields:
   - name, category, safe_min_c, safe_max_c, ambient_trigger_c,
     thermal_sensitivity (0-1), risk_tolerance (strict|moderate|lenient),
     max_allowed_exposure, time_priority (0-1), safety_priority (0-1),
     max_eta_increase_pct, routing_alpha (0-1), description
"""

ROUTE_EXPLAIN_SYSTEM_PROMPT = """You are Cargo-Guard AI, an autonomous cold-chain logistics agent.

You will receive:
- cargo_profile: the validated cargo specification
- route_decision: output from the deterministic decision engine (select_best_route)
- all_routes: the full list of candidate routes with thermal metrics

Your job is to write a concise, plain-language explanation of:
1. Which route was selected and why.
2. The key trade-offs vs. the alternatives (ETA, thermal exposure, risk level).
3. Any warnings or operator alerts.

Rules:
- Output ONLY the direct final explanation. Do NOT include any internal thoughts, analysis sections, or "Here's a thinking process".
- Do NOT invent numbers. Use only the values given in the structured data.
- Keep the explanation under 4 sentences.
- Be specific: mention route IDs, exact ETA deltas, exposure percentages, and risk levels.
- If no feasible route exists, clearly state that and recommend operator intervention.
"""

REROUTE_EXPLAIN_SYSTEM_PROMPT = """You are Cargo-Guard AI monitoring an active cold-chain delivery.

You will receive:
- cargo_profile: the active cargo specification
- reroute_decision: output from the deterministic should_reroute() engine
- current_route: live telemetry of the current route
- alternative_route: the evaluated alternative

Your job is to write a concise alert message:
1. State the action: REROUTE / CONTINUE / OPERATOR_REQUIRED.
2. Give the key reason (ETA impact, exposure reduction, risk level change).
3. Mention urgency level.
4. If OPERATOR_REQUIRED, clearly say why human judgment is needed.

Rules:
- Output ONLY the direct final alert message. Do NOT include any reasoning steps, meta-commentary, or "Here's a thinking process".
- Use only the numbers provided in the structured data. Maximum 3 sentences.
"""


# ── Cargo parsing ─────────────────────────────────────────────────────────────

def parse_cargo_input(user_input: str) -> CargoProfile:
    """
    Parse natural-language cargo requirements into a validated CargoProfile.
    Uses OpenAI agent with tool-calling when available, else deterministic fallback.
    """
    client = _get_openai_client()

    if client is not None:
        try:
            messages = [
                {"role": "system", "content": CARGO_PARSE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Parse this cargo request: \"{user_input}\""},
            ]

            response = client.chat.completions.create(
                model=OPENAI_MODEL_NAME,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.1,
            )

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if tool_calls:
                messages.append(response_message)
                for tc in tool_calls:
                    if tc.function.name == "lookup_cargo_guidelines":
                        args = json.loads(tc.function.arguments)
                        result = lookup_cargo_guidelines(args.get("query", ""))
                        messages.append({
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "name": tc.function.name,
                            "content": json.dumps(result),
                        })

                final = client.chat.completions.create(
                    model=OPENAI_MODEL_NAME,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                profile_dict = json.loads(final.choices[0].message.content or "{}")
            else:
                profile_dict = json.loads(response_message.content or "{}")

            if "cargo_profile" in profile_dict:
                profile_dict = profile_dict["cargo_profile"]
            return CargoProfile(**profile_dict)

        except Exception as exc:
            print(f"[Agent] OpenAI call failed ({exc}), falling back to deterministic parser.")

    return _deterministic_fallback_parser(user_input)


def _clean_llm_response(text: str) -> str:
    """Strip chain-of-thought thinking tags and reasoning blocks from LLM output."""
    import re
    if not text:
        return ""
    # Strip <think>...</think> and <thought>...</thought> XML blocks
    cleaned = re.sub(r"<(think|thought)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    
    # Strip any trailing chain of thought starting with common markers
    markers = [
        r"Here(?:'s| is) (?:a |the )?thinking process:?",
        r"\*\*Thinking Process:?\*\*",
        r"Thinking Process:?",
        r"Thought Process:?",
        r"\b(?:1\.|2\.)\s*\*\*Analyze",
        r"\bREROUTE\s*/\s*CONTINUE\s*/\s*OPERATOR_REQUIRED",
    ]
    for pattern in markers:
        parts = re.split(rf"(?i)\n*\s*(?:{pattern})", cleaned, maxsplit=1)
        if len(parts) > 1 and parts[0].strip():
            cleaned = parts[0].strip()

    # If the text has multiple paragraphs and later paragraphs look like prompt rules/data analysis
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    if len(paragraphs) > 1:
        # Keep only the actual alert sentences (usually first paragraph)
        valid_paras = []
        for p in paragraphs:
            if re.search(r"(?i)(?:Analyze the Data|Rules:|cargo_profile:|reroute_decision:)", p):
                break
            valid_paras.append(p)
        if valid_paras:
            cleaned = "\n\n".join(valid_paras)

    return cleaned.strip() if cleaned.strip() else text.strip()


# ── AI route explanation ──────────────────────────────────────────────────────

def explain_route_decision(
    cargo_profile: CargoProfile,
    route_decision: dict,
    all_routes: list[dict],
) -> str:
    """
    Ask the AI agent to explain the deterministic route decision in natural language.

    The AI does NOT compute anything — it reads the structured decision output
    and produces a human-readable explanation.

    Parameters
    ----------
    cargo_profile:
        The active cargo profile.
    route_decision:
        Output of decision_engine.select_best_route() serialised to dict.
    all_routes:
        All candidate routes from routing.find_routes() (with thermal metrics).

    Returns
    -------
    str: plain-language explanation, or the deterministic reason if AI unavailable.
    """
    client = _get_openai_client()

    if client is None:
        # Fallback: return the deterministic reason string directly
        return route_decision.get("reason", "Route selected by deterministic engine.")

    try:
        user_content = json.dumps({
            "cargo_profile": cargo_profile.model_dump(),
            "route_decision": route_decision,
            "all_routes": all_routes,
        }, indent=2)

        response = client.chat.completions.create(
            model=OPENAI_MODEL_NAME,
            messages=[
                {"role": "system", "content": ROUTE_EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        raw_content = response.choices[0].message.content or ""
        return _clean_llm_response(raw_content)

    except Exception as exc:
        print(f"[Agent] explain_route_decision failed ({exc})")
        return route_decision.get("reason", "Route selected by deterministic engine.")


# ── AI reroute explanation ────────────────────────────────────────────────────

def explain_reroute_decision(
    cargo_profile: CargoProfile,
    reroute_decision: dict,
    current_route: dict,
    alternative_route: dict,
) -> str:
    """
    Ask the AI agent to produce a plain-language reroute alert.

    The AI reads the deterministic RerouteDecision and explains it — it does
    not produce the REROUTE/CONTINUE verdict itself.

    Returns
    -------
    str: natural-language reroute alert, or the deterministic reason as fallback.
    """
    client = _get_openai_client()

    if client is None:
        return reroute_decision.get("reason", "Reroute evaluated by deterministic engine.")

    try:
        user_content = json.dumps({
            "cargo_profile": cargo_profile.model_dump(),
            "reroute_decision": reroute_decision,
            "current_route": current_route,
            "alternative_route": alternative_route,
        }, indent=2)

        response = client.chat.completions.create(
            model=OPENAI_MODEL_NAME,
            messages=[
                {"role": "system", "content": REROUTE_EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=250,
        )
        raw_content = response.choices[0].message.content or ""
        return _clean_llm_response(raw_content)

    except Exception as exc:
        print(f"[Agent] explain_reroute_decision failed ({exc})")
        return reroute_decision.get("reason", "Reroute evaluated by deterministic engine.")


# ── Deterministic fallback cargo parser ───────────────────────────────────────

def _deterministic_fallback_parser(user_input: str) -> CargoProfile:
    """Rule-based parser that queries the JSON knowledge base when OpenAI is offline."""
    lookup = lookup_cargo_guidelines(user_input)
    matches = lookup.get("matches", [])

    if matches:
        m = matches[0]
        return CargoProfile(
            name=f"Custom ({m.get('name')})",
            category=m.get("category", "custom"),
            safe_min_c=m.get("safe_min_c", 2.0),
            safe_max_c=m.get("safe_max_c", 8.0),
            ambient_trigger_c=m.get("ambient_trigger_c", 22.0),
            thermal_sensitivity=m.get("thermal_sensitivity", 0.8),
            risk_tolerance=m.get("risk_tolerance", "strict"),
            max_allowed_exposure=m.get("max_allowed_exposure", 20.0),
            time_priority=m.get("time_priority", 0.3),
            safety_priority=m.get("safety_priority", 0.9),
            max_eta_increase_pct=m.get("max_eta_increase_pct", 20.0),
            routing_alpha=m.get("routing_alpha", 0.20),
            description=f"Auto-configured from '{user_input}'. {m.get('description', '')}",
        )

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
        description=f"Custom cargo profile generated for: {user_input}",
    )
