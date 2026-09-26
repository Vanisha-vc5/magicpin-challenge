"""
decision_engine.py — The core trigger ranking and eligibility engine.

This module answers: "What is the single most important thing Vera
should do for this merchant/customer right now?"

Architecture:
  1. Expiry check  (tick.now vs trigger.expires_at)
  2. Suppression check
  3. Consent check  (for customer-scoped triggers)
  4. Merchant opt-out check
  5. Score each eligible trigger on a multi-factor priority formula
  6. Return the top-ranked trigger(s)

The scoring is generalized — it uses semantic fields from the dataset,
NOT hardcoded trigger IDs or merchant IDs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("vera.decision")

# ─────────────────────────────────────────────────────────────────────────────
# Trigger-kind base priority weights (higher = more urgent baseline)
# These are starting points; the full score accounts for merchant context.
# ─────────────────────────────────────────────────────────────────────────────

KIND_BASE_PRIORITY = {
    # Operational / compliance — highest baseline
    "supply_alert":            9.0,
    "regulation_change":       8.0,
    "renewal_due":             7.5,
    "gbp_unverified":          6.5,
    # Intent already expressed — must continue conversation
    "active_planning_intent":  8.5,
    # Customer-scoped — high because personal
    "chronic_refill_due":      8.0,
    "recall_due":              7.0,
    "appointment_tomorrow":    7.5,
    "customer_lapsed_hard":    6.0,
    "customer_lapsed_soft":    5.0,
    "trial_followup":          6.5,
    "wedding_package_followup": 6.0,
    # Merchant operational alerts
    "perf_dip":                7.0,
    "seasonal_perf_dip":       4.0,  # lower — expected, not urgent
    "review_theme_emerged":    6.5,
    "competitor_opened":       5.5,
    "winback_eligible":        5.0,
    "dormant_with_vera":       4.5,
    # Informational / growth
    "milestone_reached":       4.0,
    "perf_spike":              3.5,
    "ipl_match_today":         6.0,
    "festival_upcoming":       3.0,
    "category_seasonal":       4.0,
    # Knowledge / engagement
    "research_digest":         3.5,
    "cde_opportunity":         3.0,
    "curious_ask_due":         2.5,
}

DEFAULT_KIND_PRIORITY = 3.0


def parse_iso(ts_str: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp. Returns None on failure."""
    if not ts_str:
        return None
    try:
        # Python 3.11+ fromisoformat handles Z; for older, replace
        s = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_expired(trigger: Dict[str, Any], now_str: str) -> bool:
    """Return True if trigger.expires_at < tick.now."""
    expires_at = trigger.get("expires_at")
    if not expires_at:
        return False  # no expiry = never expires
    exp_dt = parse_iso(expires_at)
    now_dt = parse_iso(now_str)
    if exp_dt is None or now_dt is None:
        return False
    return exp_dt <= now_dt


def customer_has_consent(customer: Optional[Dict[str, Any]],
                          trigger_kind: str) -> bool:
    """
    Check if customer consent covers the trigger kind.
    Returns True if no customer needed (merchant scope).
    """
    if customer is None:
        return True  # merchant-scoped trigger — no customer consent needed

    consent = customer.get("consent", {})
    if not consent:
        return False

    opted_in_at = consent.get("opted_in_at")
    if not opted_in_at:
        return False

    scope = consent.get("scope", [])
    if not scope:
        return False

    # Map trigger kinds to consent scope labels
    # Allow if any relevant consent term appears in scope list
    consent_map = {
        "recall_due":              ["recall_reminders"],
        "appointment_tomorrow":    ["appointment_reminders"],
        "chronic_refill_due":      ["refill_reminders", "delivery_notifications"],
        "customer_lapsed_soft":    ["promotional_offers", "winback_offers"],
        "customer_lapsed_hard":    ["winback_offers", "promotional_offers"],
        "trial_followup":          ["appointment_reminders", "program_updates", "kids_program_updates"],
        "wedding_package_followup":["bridal_package_followup", "appointment_reminders"],
    }

    needed = consent_map.get(trigger_kind, ["promotional_offers"])
    return any(s in scope for s in needed)


def compute_trigger_priority(
    trigger: Dict[str, Any],
    merchant: Optional[Dict[str, Any]],
    category: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]],
    now_str: str,
) -> float:
    """
    Compute a priority score for a trigger given full context.
    Higher = more important to act on.

    Factors:
      - trigger urgency (1-5)
      - kind base priority
      - merchant signals (active conversation, dip severity, etc.)
      - freshness (days until expiry)
      - customer state urgency
      - existing conversation continuity
    """
    kind = trigger.get("kind", "")
    urgency = float(trigger.get("urgency", 1))
    base = KIND_BASE_PRIORITY.get(kind, DEFAULT_KIND_PRIORITY)

    score = base + urgency  # base_priority + urgency (1-5)

    # ── Merchant signals boost ─────────────────────────────────────
    if merchant:
        signals = merchant.get("signals", [])
        sig_set = set(str(s).lower() for s in signals)

        # Boost for existing active conversations (continuity)
        if any("engaged_in_last" in s for s in sig_set):
            score += 1.5

        # Boost for severe performance dip
        if "perf_dip_severe" in sig_set:
            score += 1.0

        # Boost for renewal pressure
        sub = merchant.get("subscription", {})
        days_remaining = sub.get("days_remaining", 9999)
        if sub.get("status") in ("active", "trial") and days_remaining is not None:
            if int(days_remaining) <= 7:
                score += 2.0
            elif int(days_remaining) <= 14:
                score += 1.0

        # Reduce for dormant merchants that keep ignoring
        conv_history = merchant.get("conversation_history", [])
        if conv_history:
            no_replies = sum(
                1 for c in conv_history
                if c.get("engagement") == "merchant_no_reply"
            )
            if no_replies >= 3:
                score -= 1.0

    # ── Customer state boost ─────────────────────────────────────
    if customer:
        state = customer.get("state", "")
        if state == "lapsed_hard":
            score += 1.0
        elif state == "lapsed_soft":
            score += 0.5
        elif state == "new":
            score += 0.5

    # ── Freshness: trigger expiring soon gets a boost ─────────────
    expires_at = trigger.get("expires_at")
    if expires_at:
        exp_dt = parse_iso(expires_at)
        now_dt = parse_iso(now_str)
        if exp_dt and now_dt:
            days_remaining = (exp_dt - now_dt).total_seconds() / 86400
            if days_remaining <= 1:
                score += 2.0
            elif days_remaining <= 3:
                score += 1.0
            elif days_remaining > 180:
                score -= 1.0  # far future, low urgency

    # ── Seasonal perf dip: if it's truly seasonal (expected), reduce ──
    if kind == "seasonal_perf_dip":
        payload = trigger.get("payload", {})
        if payload.get("is_expected_seasonal"):
            score -= 1.5  # reframe, don't alarm

    # ── Active planning — always high; merchant already engaged ────
    if kind == "active_planning_intent":
        score += 2.0

    return score


class DecisionEngine:
    """
    Given a list of available trigger IDs and the current stores,
    returns an ordered list of (trigger_id, trigger, merchant, category,
    customer) tuples that are eligible for action this tick.
    """

    def __init__(self, context_store, suppression_store):
        self.ctx = context_store
        self.sup = suppression_store

    def rank_triggers(
        self,
        available_trigger_ids: List[str],
        now_str: str,
    ) -> List[Tuple[float, str, Dict, Dict, Dict, Optional[Dict]]]:
        """
        Returns list of:
          (score, trigger_id, trigger, merchant, category, customer)
        Sorted descending by score. Only eligible triggers are included.
        """
        candidates = []

        for tid in available_trigger_ids:
            trigger = self.ctx.get_trigger(tid)
            if not trigger:
                logger.debug("Trigger %s not found in store — skip", tid)
                continue

            # ── 1. Expiry check ───────────────────────────────────
            if is_expired(trigger, now_str):
                logger.debug("Trigger %s expired — skip", tid)
                continue

            # ── 2. Suppression check ──────────────────────────────
            sup_key = trigger.get("suppression_key", "")
            if sup_key and self.sup.is_suppressed(sup_key):
                logger.debug("Trigger %s suppressed (%s) — skip", tid, sup_key)
                continue

            # ── 3. Merchant resolution ────────────────────────────
            merchant_id = trigger.get("merchant_id")
            if not merchant_id:
                logger.debug("Trigger %s has no merchant_id — skip", tid)
                continue

            merchant = self.ctx.get_merchant(merchant_id)
            if not merchant:
                logger.debug("Merchant %s not found — skip", tid)
                continue

            # ── 4. Merchant opt-out check ─────────────────────────
            if self.sup.merchant_opted_out(merchant_id):
                logger.debug("Merchant %s opted out — skip", tid)
                continue

            # ── 5. Category resolution ────────────────────────────
            category = self.ctx.get_category_for_merchant(merchant)
            # category may be None for some generated merchants — continue anyway

            # ── 6. Customer resolution + consent check ────────────
            customer_id = trigger.get("customer_id")
            customer = None
            if customer_id:
                customer = self.ctx.get_customer(customer_id)
                if not customer:
                    logger.debug("Customer %s not found — skip", tid)
                    continue

                # Consent check
                trigger_kind = trigger.get("kind", "")
                if not customer_has_consent(customer, trigger_kind):
                    logger.debug("Customer %s lacks consent for %s — skip",
                                 customer_id, trigger_kind)
                    continue

                # Customer opt_in / reminder_opt_in flag
                prefs = customer.get("preferences", {})
                if prefs.get("reminder_opt_in") is False:
                    logger.debug("Customer %s reminder_opt_in=False — skip", customer_id)
                    continue

            # ── 7. Score ──────────────────────────────────────────
            score = compute_trigger_priority(
                trigger, merchant, category, customer, now_str
            )

            candidates.append((score, tid, trigger, merchant, category, customer))

        # Sort descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates
