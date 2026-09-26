"""
composer.py — Message composer for Vera Bot.

Takes a structured MessageBrief + all contexts and produces the final
WhatsApp message body, CTA, template params, and rationale.

Architecture:
  MessageBrief → CategoryStrategy → MessageComposer → body + metadata

Category-specific strategies use context dynamically — no hardcoded
merchant IDs or trigger IDs. All wording is derived from the brief.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from models import MessageBrief

logger = logging.getLogger("vera.composer")


# ─────────────────────────────────────────────────────────────────────────────
# Category Strategy base and concrete implementations
# ─────────────────────────────────────────────────────────────────────────────

class CategoryStrategy:
    """
    Base category strategy. Provides tone, salutation, and vocabulary checks.
    Subclasses override for category-specific behavior.
    """

    def __init__(self, category: Optional[Dict[str, Any]]):
        self.cat = category or {}
        self.voice = self.cat.get("voice", {})
        self.tone = self.voice.get("tone", "warm_practical")
        self.vocab_taboo = set(t.lower() for t in self.voice.get("vocab_taboo", []))
        self.vocab_allowed = self.voice.get("vocab_allowed", [])
        self.peer_stats = self.cat.get("peer_stats", {})

    def salute(self, merchant: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> str:
        """Generate opening salutation."""
        if customer:
            name = customer.get("identity", {}).get("name", "")
            return f"Hi {name}" if name else "Hi"
        owner = merchant.get("identity", {}).get("owner_first_name") or \
                merchant.get("identity", {}).get("name", "")
        return owner if owner else "Hi"

    def check_taboos(self, text: str) -> str:
        """Remove/replace any taboo vocabulary."""
        for taboo in self.vocab_taboo:
            # Replace taboo words case-insensitively
            pattern = re.compile(re.escape(taboo), re.IGNORECASE)
            text = pattern.sub("[*]", text)
        return text

    def format_cta_line(self, cta: str, recommended_action: str) -> str:
        """Format the final CTA line."""
        if cta == "binary_yes_no":
            action_short = recommended_action.split("—")[0].strip()[:60]
            return f"Want me to {action_short.lower()}? Reply YES to proceed."
        elif cta in ("multi_choice", "multi_choice_slot"):
            return "Reply 1 for first slot, 2 for second, or suggest a time that works."
        elif cta == "none":
            return ""
        else:  # open_ended
            action_short = recommended_action.split("—")[0].strip()[:60]
            return f"Want me to {action_short.lower()}?"


class DentistStrategy(CategoryStrategy):
    def salute(self, merchant, customer):
        if customer:
            name = customer.get("identity", {}).get("name", "")
            merchant_name = merchant.get("identity", {}).get("name", "")
            return f"Hi {name}, {merchant_name} here 🦷" if name else f"Hi, {merchant_name} here 🦷"
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        if owner:
            return f"Dr. {owner}"
        return "Dr."

    def format_cta_line(self, cta, recommended_action):
        if cta in ("multi_choice", "multi_choice_slot"):
            return "Reply 1 for first slot, 2 for second, or tell us a time that works."
        elif cta == "binary_yes_no":
            return "Reply YES to proceed."
        else:
            action_short = recommended_action.split("—")[0].strip()[:60]
            return f"Want me to {action_short.lower()}?"


class SalonStrategy(CategoryStrategy):
    def salute(self, merchant, customer):
        if customer:
            name = customer.get("identity", {}).get("name", "")
            salon_name = merchant.get("identity", {}).get("name", "")
            owner = merchant.get("identity", {}).get("owner_first_name", "")
            from_str = f"{owner} from {salon_name}" if owner else salon_name
            emoji = "💍" if "bridal" in str(customer).lower() else "✨"
            return f"Hi {name} {emoji} {from_str} here." if name else f"Hi {emoji} {from_str} here."
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        return f"Hi {owner}" if owner else "Hi"


class RestaurantStrategy(CategoryStrategy):
    def salute(self, merchant, customer):
        if customer:
            name = customer.get("identity", {}).get("name", "")
            return f"Hi {name}" if name else "Hi"
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        return f"Hi {owner}" if owner else "Hi"


class GymStrategy(CategoryStrategy):
    def salute(self, merchant, customer):
        if customer:
            name = customer.get("identity", {}).get("name", "")
            gym_name = merchant.get("identity", {}).get("name", "")
            owner = merchant.get("identity", {}).get("owner_first_name", "")
            from_str = f"{owner} from {gym_name}" if owner else gym_name
            return f"Hi {name} 👋 {from_str} here." if name else f"Hi 👋 {from_str} here."
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        return f"Hi {owner}" if owner else "Hi"


class PharmacyStrategy(CategoryStrategy):
    def salute(self, merchant, customer):
        if customer:
            # Check if senior citizen
            is_senior = customer.get("identity", {}).get("senior_citizen", False)
            channel = customer.get("preferences", {}).get("channel", "")
            via_family = "son" in channel or "family" in channel
            if is_senior and via_family:
                return "Namaste —"
            name = customer.get("identity", {}).get("name", "")
            pharmacy_name = merchant.get("identity", {}).get("name", "")
            return f"Hi {name}" if name else f"{pharmacy_name} here"
        owner = merchant.get("identity", {}).get("owner_first_name", "")
        return f"Hi {owner}" if owner else "Hi"


# ─────────────────────────────────────────────────────────────────────────────
# Strategy factory
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGY_MAP = {
    "dentists":    DentistStrategy,
    "salons":      SalonStrategy,
    "restaurants": RestaurantStrategy,
    "gyms":        GymStrategy,
    "pharmacies":  PharmacyStrategy,
}


def get_strategy(category: Optional[Dict[str, Any]]) -> CategoryStrategy:
    """Return the appropriate CategoryStrategy for the given category context."""
    if not category:
        return CategoryStrategy(None)
    slug = category.get("slug", "")
    cls = _STRATEGY_MAP.get(slug, CategoryStrategy)
    return cls(category)


# ─────────────────────────────────────────────────────────────────────────────
# Peer benchmark formatter
# ─────────────────────────────────────────────────────────────────────────────

def format_peer_benchmark(merchant: Dict, category: Optional[Dict]) -> Optional[str]:
    """
    Return a peer benchmark string if there is a meaningful gap.
    Returns None if no useful comparison.
    """
    if not category:
        return None

    ps = category.get("peer_stats", {})
    perf = merchant.get("performance", {})

    my_ctr = perf.get("ctr")
    peer_ctr = ps.get("avg_ctr")
    if my_ctr and peer_ctr:
        diff_pct = int(abs(peer_ctr - my_ctr) / peer_ctr * 100)
        if diff_pct >= 15:
            direction = "below" if my_ctr < peer_ctr else "above"
            return f"CTR {my_ctr:.3f} vs peer median {peer_ctr:.3f} ({diff_pct}% {direction} peer)"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-kind message builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_research_digest_message(brief: MessageBrief, strategy: CategoryStrategy,
                                    merchant: Dict, customer: Optional[Dict],
                                    category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    supporting = brief.supporting_facts
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    # Source from evidence
    source = next((e for e in brief.evidence if "JIDA" in e or "DCI" in e
                   or "Salon" in e or "magicpin" in e or "IDA" in e
                   or "ICMR" in e or "Google" in e or "Practo" in e
                   or "Dentsply" in e), "")

    body_parts = [f"{salutation},"]
    body_parts.append(primary + ".")

    # Add merchant-specific hook
    if brief.merchant_specific_reason and "relevant to your" not in primary.lower():
        body_parts.append(brief.merchant_specific_reason.capitalize() + ".")

    # Add top supporting fact
    if supporting:
        body_parts.append(supporting[0])

    # Add source at end if available
    cta_block = cta_line if cta_line else ""
    source_suffix = f" — {source}" if source else ""

    body = " ".join(body_parts)
    if cta_block:
        body += f" {cta_block}"
    if source_suffix:
        body += source_suffix

    return body


def _build_regulation_change_message(brief: MessageBrief, strategy: CategoryStrategy,
                                      merchant: Dict, customer: Optional[Dict],
                                      category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = " ".join(brief.supporting_facts[:1]) if brief.supporting_facts else ""

    body = f"{salutation}, heads-up: {primary}."
    if supporting:
        body += f" {supporting}"
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_perf_dip_message(brief: MessageBrief, strategy: CategoryStrategy,
                              merchant: Dict, customer: Optional[Dict],
                              category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    peer_str = format_peer_benchmark(merchant, category)
    supporting = (brief.supporting_facts[0] if brief.supporting_facts else "") or (peer_str or "")

    body = f"{salutation}, quick check — {primary}."
    if supporting:
        body += f" {supporting}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_seasonal_perf_dip_message(brief: MessageBrief, strategy: CategoryStrategy,
                                      merchant: Dict, customer: Optional[Dict],
                                      category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = " ".join(brief.supporting_facts[:2])

    body = f"{salutation}, {primary}."
    if supporting:
        body += f" {supporting}."
    body += f" Action: {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_renewal_due_message(brief: MessageBrief, strategy: CategoryStrategy,
                                merchant: Dict, customer: Optional[Dict],
                                category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, heads-up — {primary}."
    if brief.supporting_facts:
        body += f" {brief.supporting_facts[0]}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_supply_alert_message(brief: MessageBrief, strategy: CategoryStrategy,
                                 merchant: Dict, customer: Optional[Dict],
                                 category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, urgent: {primary}."
    for fact in brief.supporting_facts[:2]:
        if fact:
            body += f" {fact}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_recall_due_message(brief: MessageBrief, strategy: CategoryStrategy,
                               merchant: Dict, customer: Optional[Dict],
                               category: Optional[Dict]) -> str:
    # Customer-facing message sent as merchant_on_behalf
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action

    # Get slot info
    slots_text = ""
    for f in brief.supporting_facts:
        if "Available" in f or "slot" in f.lower():
            slots_text = f
            break

    offer_text = ""
    for f in brief.supporting_facts:
        if "₹" in f or "cleaning" in f.lower() or "fluoride" in f.lower():
            offer_text = f
            break

    lang = brief.language_style
    hi_en = "hi-en" in lang.lower() or "hi" in lang.lower()

    body = f"{salutation} {primary}."
    if slots_text and hi_en:
        body += f" Apke liye {slots_text.replace('Available: ', '')} ready hain."
    elif slots_text:
        body += f" {slots_text}."
    if offer_text:
        body += f" {offer_text}."

    if brief.cta in ("multi_choice", "multi_choice_slot"):
        body += " Reply 1 for first slot, 2 for second, or tell us a time that works."
    else:
        body += " Reply YES to book."

    return body


def _build_chronic_refill_message(brief: MessageBrief, strategy: CategoryStrategy,
                                   merchant: Dict, customer: Optional[Dict],
                                   category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    lang = brief.language_style
    hi_mix = "hi" in lang.lower()

    body = f"{salutation} {primary}."
    for fact in brief.supporting_facts:
        if fact and ("delivery" in fact.lower() or "Senior" in fact or "₹" in fact):
            body += f" {fact}."
    if hi_mix and "Namaste" in salutation:
        body += " Reply CONFIRM to dispatch, ya call karein koi badlav ho to."
    else:
        body += " Reply CONFIRM to dispatch, or call if any dosage changes."

    return body


def _build_lapsed_customer_message(brief: MessageBrief, strategy: CategoryStrategy,
                                    merchant: Dict, customer: Optional[Dict],
                                    category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    focus_str = ""
    for f in brief.supporting_facts:
        if "focus" in f.lower() or "weight" in f.lower() or "Previous" in f:
            focus_str = f.replace("Previous focus: ", "").replace("_", " ")
            break

    offer_str = ""
    for f in brief.supporting_facts:
        if "₹" in f:
            offer_str = f
            break

    body = f"{salutation} {primary}, no judgment."
    if focus_str:
        body += f" We've got something that fits {focus_str} goals well."
    if offer_str:
        body += f" {offer_str}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_ipl_match_message(brief: MessageBrief, strategy: CategoryStrategy,
                              merchant: Dict, customer: Optional[Dict],
                              category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = brief.supporting_facts[0] if brief.supporting_facts else ""

    body = f"{salutation} {primary}."
    if supporting:
        body += f" {supporting}"
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_active_planning_message(brief: MessageBrief, strategy: CategoryStrategy,
                                    merchant: Dict, customer: Optional[Dict],
                                    category: Optional[Dict]) -> str:
    # Direct response to merchant's planning intent — action mode
    owner = merchant.get("identity", {}).get("owner_first_name", "") or \
            merchant.get("identity", {}).get("name", "")
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    last_msg = ""
    for f in brief.supporting_facts:
        if "said" in f.lower():
            last_msg = f
            break

    body = f"{owner}, here's a concrete plan for {brief.primary_fact.replace('Merchant is actively planning: ', '')}:"
    body += f"\n\n{action}."
    if cta_line:
        body += f"\n\n{cta_line}"

    return body


def _build_competitor_opened_message(brief: MessageBrief, strategy: CategoryStrategy,
                                      merchant: Dict, customer: Optional[Dict],
                                      category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = brief.supporting_facts[0] if brief.supporting_facts else ""

    body = f"{salutation}, heads-up — {primary}."
    if supporting:
        body += f" {supporting}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_winback_message(brief: MessageBrief, strategy: CategoryStrategy,
                            merchant: Dict, customer: Optional[Dict],
                            category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = " ".join(brief.supporting_facts[:2])

    body = f"{salutation}, quick check-in — {primary}."
    if supporting:
        body += f" {supporting}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_wedding_followup_message(brief: MessageBrief, strategy: CategoryStrategy,
                                     merchant: Dict, customer: Optional[Dict],
                                     category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    offer_str = ""
    pref_slot = ""
    for f in brief.supporting_facts:
        if "₹" in f:
            offer_str = f
        if "prefers" in f.lower() or "slot" in f.lower():
            pref_slot = f

    body = f"{salutation} {primary}."
    if offer_str:
        body += f" {offer_str}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_curious_ask_message(brief: MessageBrief, strategy: CategoryStrategy,
                                merchant: Dict, customer: Optional[Dict],
                                category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    question = brief.primary_fact
    deliverable = brief.supporting_facts[0] if brief.supporting_facts else \
                  "I'll turn the answer into a Google post."

    body = f"{salutation} Quick check — {question} {deliverable}"

    return body


def _build_dormant_message(brief: MessageBrief, strategy: CategoryStrategy,
                            merchant: Dict, customer: Optional[Dict],
                            category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    supporting = brief.supporting_facts[0] if brief.supporting_facts else ""

    body = f"{salutation}, checking in — {primary}."
    if supporting:
        body += f" {supporting}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_milestone_message(brief: MessageBrief, strategy: CategoryStrategy,
                              merchant: Dict, customer: Optional[Dict],
                              category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, great news — {primary}. {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_perf_spike_message(brief: MessageBrief, strategy: CategoryStrategy,
                               merchant: Dict, customer: Optional[Dict],
                               category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, spotted — {primary}. {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_gbp_unverified_message(brief: MessageBrief, strategy: CategoryStrategy,
                                   merchant: Dict, customer: Optional[Dict],
                                   category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, quick fix available — {primary}. {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_review_theme_message(brief: MessageBrief, strategy: CategoryStrategy,
                                 merchant: Dict, customer: Optional[Dict],
                                 category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    quote = ""
    for f in brief.supporting_facts:
        if "quote" in f.lower() or '"' in f:
            quote = f
            break

    body = f"{salutation}, pattern spotted — {primary}."
    if quote:
        body += f" {quote}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


def _build_appointment_tomorrow_message(brief: MessageBrief, strategy: CategoryStrategy,
                                         merchant: Dict, customer: Optional[Dict],
                                         category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    body = f"{salutation} {primary}."
    body += " Reply YES to confirm or let us know if you need to reschedule."
    return body


def _build_category_seasonal_message(brief: MessageBrief, strategy: CategoryStrategy,
                                      merchant: Dict, customer: Optional[Dict],
                                      category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, {primary}."
    for f in brief.supporting_facts:
        if f:
            body += f" Also noting: {f}."
    body += f" {action}."
    if cta_line:
        body += f" {cta_line}"
    return body


def _build_festival_upcoming_message(brief: MessageBrief, strategy: CategoryStrategy,
                                      merchant: Dict, customer: Optional[Dict],
                                      category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, {primary}. {action}."
    if cta_line:
        body += f" {cta_line}"
    return body


def _build_generic_message(brief: MessageBrief, strategy: CategoryStrategy,
                            merchant: Dict, customer: Optional[Dict],
                            category: Optional[Dict]) -> str:
    salutation = strategy.salute(merchant, customer)
    primary = brief.primary_fact
    action = brief.recommended_action
    cta_line = strategy.format_cta_line(brief.cta, action)

    body = f"{salutation}, {primary}. {action}."
    if cta_line:
        body += f" {cta_line}"

    return body


# ─────────────────────────────────────────────────────────────────────────────
# Main Composer
# ─────────────────────────────────────────────────────────────────────────────

_BUILDERS = {
    "research_digest":         _build_research_digest_message,
    "regulation_change":       _build_regulation_change_message,
    "perf_dip":                _build_perf_dip_message,
    "seasonal_perf_dip":       _build_seasonal_perf_dip_message,
    "renewal_due":             _build_renewal_due_message,
    "supply_alert":            _build_supply_alert_message,
    "recall_due":              _build_recall_due_message,
    "chronic_refill_due":      _build_chronic_refill_message,
    "customer_lapsed_hard":    _build_lapsed_customer_message,
    "customer_lapsed_soft":    _build_lapsed_customer_message,
    "ipl_match_today":         _build_ipl_match_message,
    "active_planning_intent":  _build_active_planning_message,
    "competitor_opened":       _build_competitor_opened_message,
    "winback_eligible":        _build_winback_message,
    "wedding_package_followup":_build_wedding_followup_message,
    "curious_ask_due":         _build_curious_ask_message,
    "dormant_with_vera":       _build_dormant_message,
    "milestone_reached":       _build_milestone_message,
    "perf_spike":              _build_perf_spike_message,
    "gbp_unverified":          _build_gbp_unverified_message,
    "review_theme_emerged":    _build_review_theme_message,
    "category_seasonal":       _build_category_seasonal_message,
    "trial_followup":          _build_recall_due_message,  # same shape
    "festival_upcoming":       _build_festival_upcoming_message,
    "cde_opportunity":         _build_research_digest_message,
    "appointment_tomorrow":    _build_appointment_tomorrow_message,
}


def compose_message(
    brief: MessageBrief,
    merchant: Dict[str, Any],
    category: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compose the final message and return a dict with:
      body, cta, send_as, template_name, template_params, suppression_key, rationale
    """
    strategy = get_strategy(category)

    # Select builder
    builder = _BUILDERS.get(brief.trigger_kind, _build_generic_message)

    try:
        body = builder(brief, strategy, merchant, customer, category)
    except Exception as e:
        logger.warning("Builder for %s failed: %s — using generic", brief.trigger_kind, e)
        body = _build_generic_message(brief, strategy, merchant, customer, category)

    # Strip any taboo words
    body = strategy.check_taboos(body)

    # Enforce single CTA (no multiple "Reply X" lines)
    body = _enforce_single_cta(body)

    # Build template params (for first outbound WhatsApp template)
    salutation = strategy.salute(merchant, customer)
    template_params = [
        salutation,
        brief.primary_fact[:60],
        brief.recommended_action[:80],
    ]

    # Template name by scope and kind
    if brief.scope == "customer":
        template_name = f"merchant_{brief.trigger_kind}_v1"
    else:
        template_name = f"vera_{brief.trigger_kind}_v1"

    # Rationale
    rationale = (
        f"Selected '{brief.trigger_kind}' (priority={brief.priority_score:.1f}): "
        f"{brief.primary_fact[:80]}. "
        f"Merchant-specific: {brief.merchant_specific_reason}. "
        f"Evidence: {'; '.join(brief.evidence[:2])}"
    )

    return {
        "body": body.strip(),
        "cta": brief.cta,
        "send_as": brief.send_as,
        "template_name": template_name,
        "template_params": template_params,
        "suppression_key": brief.suppression_key,
        "rationale": rationale,
    }


def _enforce_single_cta(body: str) -> str:
    """
    Ensure there's only one clear CTA in the message.
    Remove duplicate 'Reply X' or 'Want me to...' phrases.
    """
    # If there are multiple "Want me to" phrases, keep only the last
    want_me = [m.start() for m in re.finditer(r'Want me to', body, re.IGNORECASE)]
    if len(want_me) > 1:
        # Keep everything up to first occurrence, then jump to last
        first = want_me[0]
        last = want_me[-1]
        before = body[:first].rstrip()
        after_last = body[last:]
        body = before + " " + after_last

    return body


def generate_rationale(brief: MessageBrief) -> str:
    """Generate a concise rationale explaining the decision."""
    return (
        f"Trigger '{brief.trigger_kind}' selected (priority={brief.priority_score:.1f}). "
        f"Primary signal: {brief.primary_fact}. "
        f"Merchant-specific reasoning: {brief.merchant_specific_reason}. "
        f"Evidence basis: {'; '.join(brief.evidence[:3]) if brief.evidence else 'trigger payload facts'}."
    )
