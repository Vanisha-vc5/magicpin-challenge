"""
bot.py — Vera Message Engine (magicpin AI Challenge)

Exposes the canonical `compose` function:
    compose(category, merchant, trigger, customer?) -> dict

Returns:
    {
        "body": str,
        "cta": str,
        "send_as": "vera" | "merchant_on_behalf",
        "suppression_key": str,
        "rationale": str
    }
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure vera_bot is in sys.path
sys.path.insert(0, str(Path(__file__).parent / "vera_bot"))

from fact_extractor import extract_facts
from composer import compose_message


def compose(
    category: Optional[Dict[str, Any]],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministic composition function matching the challenge specification.
    Combines the 4 context layers into a high-compulsion message, single CTA,
    send-as identity, suppression key, and decision rationale.
    """
    now_str = trigger.get("expires_at", "2026-04-29T10:00:00Z")
    
    # 1. Structured fact extraction (grounded, zero hallucinations)
    brief = extract_facts(
        trigger=trigger,
        merchant=merchant,
        category=category,
        customer=customer,
        now_str=now_str,
        priority_score=5.0,
    )

    # 2. Category-aware composition (tone, vocabulary, taboos, single CTA)
    composed = compose_message(
        brief=brief,
        merchant=merchant,
        category=category,
        customer=customer,
    )

    return {
        "body": composed["body"],
        "cta": composed["cta"],
        "send_as": composed["send_as"],
        "suppression_key": composed["suppression_key"],
        "rationale": composed["rationale"],
    }
