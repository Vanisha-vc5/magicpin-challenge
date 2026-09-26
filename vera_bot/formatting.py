"""Deterministic formatting helpers for context facts."""
from __future__ import annotations

from typing import Optional


def normalize_percentage(value, semantics: str = "percentage") -> Optional[float]:
    """Normalize only when the source schema explicitly says it is a ratio."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if semantics == "ratio":
        number *= 100
    elif semantics != "percentage":
        raise ValueError(f"Unknown percentage semantics: {semantics}")
    return round(number, 6)


def format_percentage(value, semantics: str = "percentage") -> str:
    normalized = normalize_percentage(value, semantics)
    if normalized is None:
        return ""
    if normalized.is_integer():
        return f"{int(normalized)}%"
    return f"{normalized:.1f}%"


def format_inr(value) -> str:
    """Format INR while retaining the Unicode rupee sign."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"₹{int(number):,}"
    return f"₹{number:,.2f}"


def relative_shortfall(actual, peer) -> Optional[float]:
    """Return actual's shortfall relative to peer, as a percentage."""
    try:
        actual_value = float(actual)
        peer_value = float(peer)
    except (TypeError, ValueError):
        return None
    if peer_value <= 0:
        return None
    return round(max(0.0, (peer_value - actual_value) / peer_value * 100), 6)


ALLOWED_CTAS = {
    "binary_yes_no", "multi_choice", "multi_choice_slot", "single_choice",
    "open_ended", "none",
}


def resolve_cta(requested: str, body: str) -> str:
    """Resolve CTA metadata from the actual question in the final body."""
    cta = requested if requested in ALLOWED_CTAS else "open_ended"
    if cta == "open_ended" and "want me to" in body.lower():
        return "binary_yes_no"
    return cta


def align_cta_body(body: str, cta: str) -> str:
    """Ensure a binary CTA has an explicit, single low-friction response."""
    if cta != "binary_yes_no" or "reply yes" in body.lower():
        return body
    if body.rstrip().endswith("?"):
        return body.rstrip()[:-1] + "? Reply YES to proceed."
    return body.rstrip() + " Reply YES to proceed."
