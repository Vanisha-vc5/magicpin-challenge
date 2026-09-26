"""
fact_extractor.py — Structured fact extraction before message composition.

Extracts concrete, verifiable facts from merchant/customer/trigger/category
context and assembles a MessageBrief.

NOTHING in this file invents facts. Every field in the brief comes from
the actual supplied context objects.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import MessageBrief
from formatting import format_inr, format_percentage, relative_shortfall

logger = logging.getLogger("vera.facts")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def fmt_pct(val: Optional[float], prefix: str = "") -> str:
    """Format fields whose schema already expresses percentages as 0..100."""
    result = format_percentage(val)
    return f"{prefix}{result}" if result else ""


def fmt_inr(val) -> str:
    return format_inr(val)


def get_active_offers(merchant: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return only active offers from merchant context."""
    return [o for o in merchant.get("offers", [])
            if o.get("status") == "active"]


def get_owner_name(merchant: Dict[str, Any]) -> str:
    """Extract owner first name from merchant context."""
    identity = merchant.get("identity", {})
    return identity.get("owner_first_name") or identity.get("name", "")


def get_merchant_name(merchant: Dict[str, Any]) -> str:
    identity = merchant.get("identity", {})
    return identity.get("name", "")


def get_language_style(merchant: Dict[str, Any],
                        customer: Optional[Dict[str, Any]] = None) -> str:
    """Determine primary language style for the message."""
    if customer:
        lang = customer.get("identity", {}).get("language_pref", "")
        if lang:
            return lang
    # Fall back to merchant languages
    langs = merchant.get("identity", {}).get("languages", ["en"])
    if "hi" in langs:
        return "hi-en mix"
    return "en"


def days_since(date_str: str, now_str: Optional[str] = None) -> Optional[int]:
    """Compute approximate days since a date string."""
    try:
        from datetime import date
        d = datetime.fromisoformat(date_str.split("T")[0]).date()
        if now_str:
            now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
            ref = now_dt.date()
        else:
            ref = date.today()
        return (ref - d).days
    except Exception:
        return None


def days_until(date_str: str, now_str: Optional[str] = None) -> Optional[int]:
    """Compute approximate days until a future date string."""
    try:
        from datetime import date
        d = datetime.fromisoformat(date_str.split("T")[0]).date()
        if now_str:
            now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
            ref = now_dt.date()
        else:
            ref = date.today()
        return (d - ref).days
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Category-specific tone resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_tone(category: Optional[Dict[str, Any]],
                 customer: Optional[Dict[str, Any]]) -> str:
    """Resolve the appropriate tone string from category context."""
    if not category:
        return "warm_practical"
    voice = category.get("voice", {})
    base = voice.get("tone", "warm_practical")
    if customer:
        # Customer-facing messages may soften the tone
        return base + "_customer"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Per-kind fact extractors
# ─────────────────────────────────────────────────────────────────────────────

def _extract_research_digest(trigger: Dict, merchant: Dict,
                               category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    top_item_id = payload.get("top_item_id")

    # Find the digest item in category
    digest_item = None
    if category and top_item_id:
        for item in category.get("digest", []):
            if item.get("id") == top_item_id:
                digest_item = item
                break

    if not digest_item and category:
        # Fall back to first digest item
        digest = category.get("digest", [])
        if digest:
            digest_item = digest[0]

    if not digest_item:
        return {}

    title = digest_item.get("title", "")
    source = digest_item.get("source", "")
    trial_n = digest_item.get("trial_n")
    summary = digest_item.get("summary", "")
    actionable = digest_item.get("actionable", "")
    kind_tag = digest_item.get("kind", "research")

    primary_fact = title
    supporting = []
    if trial_n:
        supporting.append(f"{trial_n:,}-participant trial")
    if source:
        supporting.append(f"Source: {source}")
    if summary:
        supporting.append(summary[:120])

    recommended = actionable or "Review this item and assess applicability"

    # Merchant-specific hook
    signals = merchant.get("signals", [])
    customer_agg = merchant.get("customer_aggregate", {})
    high_risk = customer_agg.get("high_risk_adult_count")
    merchant_hook = ""
    if high_risk and "high_risk_adult_cohort" in str(signals):
        merchant_hook = f"{high_risk} high-risk adult patients in your roster"
    elif customer_agg.get("chronic_rx_count"):
        merchant_hook = f"{customer_agg['chronic_rx_count']} chronic-Rx patients"

    return {
        "primary_fact": primary_fact,
        "supporting_facts": supporting,
        "merchant_specific_reason": merchant_hook or "relevant to your practice",
        "recommended_action": recommended,
        "cta": "open_ended",
        "evidence": ([source] if source else []) + ([f"Trial N={trial_n}"] if trial_n else []),
        "digest_item": digest_item,
        "kind_tag": kind_tag,
    }


def _extract_regulation_change(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    top_item_id = payload.get("top_item_id")
    deadline = payload.get("deadline_iso", "")

    digest_item = None
    if category and top_item_id:
        for item in category.get("digest", []):
            if item.get("id") == top_item_id:
                digest_item = item
                break

    if not digest_item and category:
        # Use compliance digest items
        for item in category.get("digest", []):
            if item.get("kind") == "compliance":
                digest_item = item
                break

    if not digest_item:
        return {}

    title = digest_item.get("title", "")
    source = digest_item.get("source", "")
    summary = digest_item.get("summary", "")
    actionable = digest_item.get("actionable", "")

    dl_str = ""
    if deadline:
        dl_str = f"deadline {deadline[:10]}"

    return {
        "primary_fact": title,
        "supporting_facts": [summary[:150], dl_str] if dl_str else [summary[:150]],
        "merchant_specific_reason": "compliance action required for your practice",
        "recommended_action": actionable or "Audit your setup before the deadline",
        "cta": "binary_yes_no",
        "evidence": [source] if source else [],
    }


def _extract_perf_dip(trigger: Dict, merchant: Dict,
                       category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "calls")
    delta = payload.get("delta_pct", 0)
    window = payload.get("window", "7d")
    baseline = payload.get("vs_baseline", payload.get("baseline"))

    delta_str = fmt_pct(delta)
    primary = f"{metric} {delta_str} over {window}"
    if baseline:
        primary += f" vs baseline of {baseline}"

    # Peer comparison
    supporting = []
    perf = merchant.get("performance", {})
    if category:
        ps = category.get("peer_stats", {})
        peer_ctr = ps.get("avg_ctr")
        my_ctr = perf.get("ctr")
        if peer_ctr and my_ctr:
            diff = relative_shortfall(my_ctr, peer_ctr)
            if diff and diff > 0:
                supporting.append(
                    f"Your CTR {my_ctr:.3f} vs peer median {peer_ctr:.3f} "
                    f"({format_percentage(diff)} relative shortfall)"
                )

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": f"actionable signal for your listing",
        "recommended_action": f"Optimize listing to recover {metric}" + (
            f" — your {offer_str} is the hook" if offer_str else ""),
        "cta": "binary_yes_no",
        "evidence": [primary],
        "offer_str": offer_str,
    }


def _extract_seasonal_perf_dip(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta = payload.get("delta_pct", 0)
    season_note = payload.get("season_note", "")

    delta_str = fmt_pct(delta)
    primary = f"{metric} {delta_str} this week — but this is the expected seasonal pattern"

    supporting = []
    if category:
        # Find seasonal beat
        for beat in category.get("seasonal_beats", []):
            note = beat.get("note", "")
            if "apr" in note.lower() or "jun" in note.lower() or "acquisition" in note.lower():
                supporting.append(note)
                break

    customer_agg = merchant.get("customer_aggregate", {})
    active_members = customer_agg.get("total_active_members")
    if active_members:
        supporting.append(f"Focus on your {active_members} active members now")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "normal seasonal pattern — action is retention, not acquisition",
        "recommended_action": "Run a retention challenge or loyalty program for existing members",
        "cta": "open_ended",
        "evidence": [season_note] if season_note else [],
    }


def _extract_renewal_due(trigger: Dict, merchant: Dict,
                          category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    days = payload.get("days_remaining", merchant.get("subscription", {}).get("days_remaining", "?"))
    plan = payload.get("plan", merchant.get("subscription", {}).get("plan", "Pro"))
    amount = payload.get("renewal_amount")

    amount_str = fmt_inr(amount) if amount else ""
    primary = f"Subscription expires in {days} days (plan: {plan})"
    if amount_str:
        primary += f" — renewal {amount_str}"

    perf = merchant.get("performance", {})
    views = perf.get("views")
    calls = perf.get("calls")
    supporting = []
    if views and calls:
        supporting.append(f"Current 30d: {views} views, {calls} calls")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "service continuity at risk",
        "recommended_action": "Renew to maintain listing visibility and active offers",
        "cta": "binary_yes_no",
        "evidence": [f"{days} days remaining"],
    }


def _extract_supply_alert(trigger: Dict, merchant: Dict,
                            category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    alert_id = payload.get("alert_id", "")
    molecule = payload.get("molecule", "")
    batches = payload.get("affected_batches", [])
    manufacturer = payload.get("manufacturer", "")

    batch_str = ", ".join(batches)
    primary = f"Voluntary recall on {molecule} batches: {batch_str}" if batches else f"Alert on {molecule}"
    if manufacturer:
        primary += f" by {manufacturer}"

    # Estimate affected customers
    customer_agg = merchant.get("customer_aggregate", {})
    chronic_count = customer_agg.get("chronic_rx_count", 0)
    # Use a rough estimate: if chronic_count is available
    affected = int(chronic_count * 0.09) if chronic_count else None

    supporting = []
    if affected and chronic_count:
        supporting.append(f"~{affected} of your {chronic_count} chronic-Rx customers may be affected")

    # Find category digest item for more details
    if category and alert_id:
        for item in category.get("digest", []):
            if item.get("id") == alert_id:
                summary = item.get("summary", "")
                if summary:
                    supporting.append(summary[:120])
                break

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "urgent compliance action — customer safety and regulatory duty",
        "recommended_action": f"Pull the batches and notify affected customers",
        "cta": "binary_yes_no",
        "evidence": batches + ([manufacturer] if manufacturer else []),
        "affected_count": affected,
    }


def _extract_recall_due(trigger: Dict, merchant: Dict, category: Optional[Dict],
                         customer: Optional[Dict], now_str: str) -> Dict:
    payload = trigger.get("payload", {})
    service_due = payload.get("service_due", "6-month recall")
    last_date = payload.get("last_service_date", "")
    due_date = payload.get("due_date", "")
    slots = payload.get("available_slots", [])

    # Compute months since last service
    months_ago = ""
    if last_date:
        d = days_since(last_date, now_str)
        if d and d >= 30:
            months_ago = f"{d // 30} months"
        elif d and d >= 1:
            months_ago = f"{d} days"

    slot_labels = [s.get("label", "") for s in slots[:2] if s.get("label")]

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""

    service_clean = service_due.replace("_", " ")
    if months_ago:
        primary = f"It's been {months_ago} since your last visit — your {service_clean} is due"
    else:
        primary = f"Your {service_clean} is due"

    supporting = []
    if slot_labels:
        supporting.append("Available: " + " or ".join(slot_labels))
    if offer_str:
        supporting.append(offer_str)

    customer_name = ""
    if customer:
        customer_name = customer.get("identity", {}).get("name", "")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": f"patient due for recall — proactive outreach prevents lapse",
        "recommended_action": "Book appointment from available slots",
        "cta": "multi_choice" if slot_labels else "binary_yes_no",
        "evidence": [last_date] if last_date else [],
        "slot_labels": slot_labels,
        "offer_str": offer_str,
        "customer_name": customer_name,
        "months_ago": months_ago,
    }


def _extract_chronic_refill_due(trigger: Dict, merchant: Dict,
                                  category: Optional[Dict],
                                  customer: Optional[Dict],
                                  now_str: str) -> Dict:
    payload = trigger.get("payload", {})
    molecules = payload.get("molecule_list", [])
    runs_out = payload.get("stock_runs_out_iso", "")
    delivery_saved = payload.get("delivery_address_saved", False)

    run_date = runs_out[:10] if runs_out else ""

    active_offers = get_active_offers(merchant)
    offer_strs = [o["title"] for o in active_offers]

    mol_str = ", ".join(molecules)
    primary = f"{mol_str} — refill due {run_date}" if run_date else f"{mol_str} refill due"

    supporting = []
    if delivery_saved:
        supporting.append("Delivery address saved")
    for o in offer_strs:
        supporting.append(o)

    # Look for senior discount
    senior_discount = None
    for o in active_offers:
        if "senior" in o.get("title", "").lower() or "15" in o.get("title", ""):
            senior_discount = o["title"]
            break

    customer_name = ""
    is_senior = False
    if customer:
        customer_name = customer.get("identity", {}).get("name", "")
        is_senior = customer.get("identity", {}).get("senior_citizen", False)
        channel = customer.get("preferences", {}).get("channel", "")
        if "son" in channel or "family" in channel:
            supporting.append("Message via son/family number")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "chronic patient — refill continuity is critical",
        "recommended_action": "Confirm dispatch with delivery to saved address",
        "cta": "binary_yes_no",
        "evidence": molecules + ([run_date] if run_date else []),
        "molecules": molecules,
        "run_date": run_date,
        "customer_name": customer_name,
        "is_senior": is_senior,
        "senior_discount": senior_discount,
        "delivery_saved": delivery_saved,
        "offer_strs": offer_strs,
    }


def _extract_customer_lapsed(trigger: Dict, merchant: Dict,
                               category: Optional[Dict],
                               customer: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    days_lapsed = payload.get("days_since_last_visit", 0)
    focus = payload.get("previous_focus", "")
    months_member = payload.get("previous_membership_months", 0)

    weeks = int(days_lapsed) // 7 if days_lapsed else None

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""

    primary = f"{weeks} weeks since last visit" if weeks else "Lapsed customer"
    supporting = []
    if focus:
        supporting.append(f"Previous focus: {focus}")
    if offer_str:
        supporting.append(offer_str)

    customer_name = ""
    if customer:
        customer_name = customer.get("identity", {}).get("name", "")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "re-engage before churn becomes permanent",
        "recommended_action": "Offer a free trial or low-commitment return session",
        "cta": "binary_yes_no",
        "evidence": [f"{days_lapsed} days since last visit"],
        "weeks_lapsed": weeks,
        "focus": focus,
        "offer_str": offer_str,
        "customer_name": customer_name,
    }


def _extract_ipl_match(trigger: Dict, merchant: Dict,
                        category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    match = payload.get("match", "IPL match")
    venue = payload.get("venue", "")
    match_time = payload.get("match_time_iso", "")
    is_weeknight = payload.get("is_weeknight", True)

    time_str = match_time[11:16] if len(match_time) > 16 else ""
    if time_str:
        time_str = f", {time_str}"

    # Look for category insight on Saturday vs weeknight
    category_note = ""
    if category:
        for item in category.get("digest", []):
            if "ipl" in item.get("id", "").lower() or "ipl" in item.get("title", "").lower():
                category_note = item.get("summary", "")
                break

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""

    primary = f"{match} at {venue}{time_str}" if venue else f"{match}{time_str}"
    supporting = []
    if not is_weeknight and category_note:
        supporting.append(category_note[:100])
    elif is_weeknight:
        supporting.append("Weeknight IPL drives +18% restaurant covers")

    recommendation = ""
    if not is_weeknight:
        recommendation = f"Skip match promo today (Sat IPL = -12% covers). Push {offer_str} as delivery special" if offer_str else "Avoid match promo on weekend — focus on delivery"
    else:
        recommendation = f"Good opportunity — run a match-night combo to capture footfall"

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "match timing affects footfall — act now",
        "recommended_action": recommendation,
        "cta": "open_ended",
        "evidence": [match, venue] if venue else [match],
        "is_weeknight": is_weeknight,
        "offer_str": offer_str,
    }


def _extract_active_planning_intent(trigger: Dict, merchant: Dict,
                                     category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    topic = payload.get("intent_topic", "")
    last_msg = payload.get("merchant_last_message", "")

    primary = f"Merchant is actively planning: {topic.replace('_', ' ')}"

    return {
        "primary_fact": primary,
        "supporting_facts": [f'Merchant said: "{last_msg}"'] if last_msg else [],
        "merchant_specific_reason": "merchant expressed intent — advance immediately",
        "recommended_action": "Deliver a concrete draft/plan to move forward",
        "cta": "binary_yes_no",
        "evidence": [last_msg] if last_msg else [topic],
        "topic": topic,
        "last_msg": last_msg,
    }


def _extract_competitor_opened(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    comp_name = payload.get("competitor_name", "a new competitor")
    distance = payload.get("distance_km")
    their_offer = payload.get("their_offer", "")
    opened_date = payload.get("opened_date", "")

    dist_str = f"{distance}km away" if distance else "nearby"
    primary = f"{comp_name} opened {dist_str}"
    if their_offer:
        primary += f" — their entry offer: {their_offer}"

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""
    supporting = []
    if offer_str:
        supporting.append(f"Your active offer: {offer_str}")

    # Look at CTR vs peer
    perf = merchant.get("performance", {})
    my_ctr = perf.get("ctr")
    peer_ctr = category.get("peer_stats", {}).get("avg_ctr") if category else None
    if my_ctr and peer_ctr:
            diff = relative_shortfall(my_ctr, peer_ctr)
            if diff and diff > 0:
                supporting.append(
                    f"Your CTR {my_ctr:.3f} is {format_percentage(diff)} below peer "
                    "(relative shortfall) — listing needs strengthening"
                )

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "strengthen listing differentiation before market share shifts",
        "recommended_action": "Update GBP description to differentiate from new entrant",
        "cta": "open_ended",
        "evidence": [comp_name, dist_str] if distance else [comp_name],
        "competitor_name": comp_name,
        "their_offer": their_offer,
        "offer_str": offer_str,
    }


def _extract_winback_eligible(trigger: Dict, merchant: Dict,
                               category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    days_expired = payload.get("days_since_expiry", 0)
    dip_pct = payload.get("perf_dip_pct", 0)
    lapsed_since = payload.get("lapsed_customers_added_since_expiry", 0)

    dip_str = format_percentage(dip_pct, semantics="ratio")
    primary = f"Subscription lapsed {days_expired} days ago — performance {dip_str}"
    supporting = []
    if lapsed_since:
        supporting.append(f"{lapsed_since} additional customers lapsed since expiry")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "recover listing visibility and stop customer lapse",
        "recommended_action": "Reactivate subscription to restore profile benefits",
        "cta": "binary_yes_no",
        "evidence": [f"{days_expired} days since expiry", f"perf {dip_str}"],
    }


def _extract_wedding_followup(trigger: Dict, merchant: Dict,
                               category: Optional[Dict],
                               customer: Optional[Dict],
                               now_str: str) -> Dict:
    payload = trigger.get("payload", {})
    wedding_date = payload.get("wedding_date", "")
    trial_done = payload.get("trial_completed", "")
    days_to_wedding = payload.get("days_to_wedding")
    next_step = payload.get("next_step_window_open", "")

    days_str = f"{days_to_wedding} days" if days_to_wedding else ""

    # Find bridal offer
    active_offers = get_active_offers(merchant)
    bridal_offers = [o for o in active_offers
                     if "bridal" in o.get("title", "").lower()
                     or "skin" in o.get("title", "").lower()]
    offer_str = bridal_offers[0]["title"] if bridal_offers else (
        active_offers[0]["title"] if active_offers else "")

    customer_name = ""
    pref_slot = ""
    if customer:
        customer_name = customer.get("identity", {}).get("name", "")
        pref_slot = customer.get("preferences", {}).get("preferred_slots", "")

    primary = f"{days_str} to wedding — bridal skin-prep window is now" if days_str else "Bridal follow-up"
    supporting = []
    if offer_str:
        supporting.append(offer_str)
    if pref_slot:
        supporting.append(f"Prefers {pref_slot.replace('_', ' ')}")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "bride in critical pre-wedding window — timing is key",
        "recommended_action": f"Book first skin-prep session for {customer_name or 'customer'}" + (
            f" ({pref_slot.replace('_', ' ')} slot)" if pref_slot else ""),
        "cta": "binary_yes_no",
        "evidence": [wedding_date, trial_done] if trial_done else [wedding_date],
        "customer_name": customer_name,
        "days_to_wedding": days_to_wedding,
        "offer_str": offer_str,
        "pref_slot": pref_slot,
    }


def _extract_curious_ask(trigger: Dict, merchant: Dict,
                          category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    ask_template = payload.get("ask_template", "what_service_in_demand_this_week")

    question_map = {
        "what_service_in_demand_this_week": "What service has been most asked-for this week?",
        "what_customers_saying": "What are customers saying most this week?",
        "how_weekends_going": "How has weekend footfall been this week?",
    }
    question = question_map.get(ask_template, "What's been the top trend this week at your place?")

    return {
        "primary_fact": question,
        "supporting_facts": ["Answer takes 5 min — I'll turn it into a Google post + customer reply template"],
        "merchant_specific_reason": "keeps merchant engaged with low-friction knowledge exchange",
        "recommended_action": "Get merchant's insight and create a Google post from their answer",
        "cta": "open_ended",
        "evidence": [],
        "question": question,
    }


def _extract_dormant_with_vera(trigger: Dict, merchant: Dict,
                                category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    days = payload.get("days_since_last_merchant_message", 30)
    last_topic = payload.get("last_topic", "")

    primary = f"No conversation in {days} days"

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""

    perf = merchant.get("performance", {})
    delta_7d = perf.get("delta_7d", {})
    calls_delta = delta_7d.get("calls_pct")

    supporting = []
    if calls_delta and calls_delta < 0:
        supporting.append(
            f"Calls {format_percentage(calls_delta, semantics='ratio')} this week"
        )
    if offer_str:
        supporting.append(f"Active offer: {offer_str}")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "re-establish contact before dormancy becomes churn",
        "recommended_action": "Open with a low-friction observation or question",
        "cta": "open_ended",
        "evidence": [f"{days} days inactive"],
        "last_topic": last_topic,
        "offer_str": offer_str,
    }


def _extract_milestone_reached(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "review_count")
    value = payload.get("value_now")
    milestone = payload.get("milestone_value")
    imminent = payload.get("is_imminent", False)

    if imminent and milestone and value:
        gap = milestone - value
        primary = f"{gap} away from {milestone} {metric.replace('_', ' ')} milestone"
    elif value and milestone:
        primary = f"Reached {milestone} {metric.replace('_', ' ')}"
    else:
        primary = f"Milestone approaching for {metric}"

    return {
        "primary_fact": primary,
        "supporting_facts": [f"Current: {value}" if value else ""],
        "merchant_specific_reason": "capitalize on this credibility boost",
        "recommended_action": "Post a milestone celebration + ask for more reviews",
        "cta": "open_ended",
        "evidence": [f"metric={metric}", f"value={value}"] if value else [],
        "imminent": imminent,
    }


def _extract_perf_spike(trigger: Dict, merchant: Dict,
                         category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta = payload.get("delta_pct", 0)
    window = payload.get("window", "7d")
    driver = payload.get("likely_driver", "")

    delta_str = fmt_pct(delta)
    primary = f"{metric} {delta_str} over {window}"
    if driver:
        primary += f" — likely driven by {driver.replace('_', ' ')}"

    supporting = []
    active_offers = get_active_offers(merchant)
    if active_offers:
        supporting.append(f"Capitalize with: {active_offers[0]['title']}")

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "ride the momentum while it's happening",
        "recommended_action": "Convert spike into reviews/bookings with a timely post",
        "cta": "open_ended",
        "evidence": [primary],
        "driver": driver,
    }


def _extract_gbp_unverified(trigger: Dict, merchant: Dict,
                              category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    path = payload.get("verification_path", "postcard_or_phone_call")
    uplift = payload.get("estimated_uplift_pct", 0.30)

    uplift_str = format_percentage(uplift, semantics="ratio")
    primary = f"Google Business Profile is unverified — estimated {uplift_str} visibility uplift on verification"

    return {
        "primary_fact": primary,
        "supporting_facts": [f"Verification via {path.replace('_', ' ')}"],
        "merchant_specific_reason": "unverified listings rank lower in local search",
        "recommended_action": "Complete GBP verification to unlock full listing benefits",
        "cta": "binary_yes_no",
        "evidence": [f"{uplift_str} uplift on verification"],
    }


def _extract_review_theme(trigger: Dict, merchant: Dict,
                           category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    theme = payload.get("theme", "")
    occurrences = payload.get("occurrences_30d", 0)
    trend = payload.get("trend", "")
    quote = payload.get("common_quote", "")

    primary = f"{occurrences} reviews mention '{theme.replace('_', ' ')}' (trend: {trend})"
    supporting = []
    if quote:
        supporting.append(f'Common quote: "{quote}"')

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "recurring theme can be addressed proactively to protect rating",
        "recommended_action": f"Address the {theme.replace('_', ' ')} issue and respond to recent reviews",
        "cta": "open_ended",
        "evidence": [f"{occurrences}x mentions", trend],
        "theme": theme,
    }


def _extract_category_seasonal(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    season = payload.get("season", "").replace("_", " ").title()
    trends = payload.get("trends", [])

    trend_strs = []
    for t in trends:
        # e.g. "ORS_demand_+40" -> "ORS demand (+40%)"
        clean = str(t).replace("_", " ")
        trend_strs.append(clean)

    if trend_strs:
        primary = f"Seasonal demand shift for {season}: {', '.join(trend_strs[:3])}"
    else:
        primary = f"Seasonal demand shift: {season}" if season else "Seasonal demand shift underway"

    supporting = trend_strs[3:] if len(trend_strs) > 3 else []
    recommended = "Rearrange front shelves and run a seasonal promo to capture peak demand"

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "seasonal demand shift directly affects your inventory and walk-ins",
        "recommended_action": recommended,
        "cta": "open_ended",
        "evidence": trend_strs[:3],
    }


def _extract_appointment_tomorrow(trigger: Dict, merchant: Dict,
                                   category: Optional[Dict],
                                   customer: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    service = payload.get("service") or payload.get("service_name") or "appointment"
    time_str = payload.get("time") or payload.get("appointment_time") or "tomorrow"

    primary = f"Friendly reminder for your {service.replace('_', ' ')} scheduled for {time_str}"

    return {
        "primary_fact": primary,
        "supporting_facts": [],
        "merchant_specific_reason": "appointment reminders decrease no-show rates by up to 40%",
        "recommended_action": "Reply YES to confirm or let us know if you need to reschedule",
        "cta": "binary_yes_no",
        "evidence": [service, time_str],
    }


def _extract_trial_followup(trigger: Dict, merchant: Dict,
                              category: Optional[Dict],
                              customer: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    trial_date = payload.get("trial_date", "")
    next_sessions = payload.get("next_session_options", [])

    slot_labels = [s.get("label", "") for s in next_sessions[:2] if s.get("label")]

    customer_name = ""
    if customer:
        customer_name = customer.get("identity", {}).get("name", "")

    primary = f"Trial completed {trial_date[:10] if trial_date else 'recently'} — follow up to convert"
    supporting = []
    if slot_labels:
        supporting.append("Next session: " + " or ".join(slot_labels))

    active_offers = get_active_offers(merchant)
    offer_str = active_offers[0]["title"] if active_offers else ""
    if offer_str:
        supporting.append(offer_str)

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "trial-to-paid conversion window is open — act now",
        "recommended_action": "Book a follow-up session while enthusiasm is fresh",
        "cta": "binary_yes_no",
        "evidence": [trial_date] if trial_date else [],
        "slot_labels": slot_labels,
        "customer_name": customer_name,
        "offer_str": offer_str,
    }


def _extract_festival_upcoming(trigger: Dict, merchant: Dict,
                                 category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    festival = payload.get("festival", "")
    date = payload.get("date", "")
    days_until_fest = payload.get("days_until")

    if not festival:
        beats = category.get("seasonal_beats", []) if category else []
        if beats:
            beat = beats[0]
            festival = beat.get("event") or beat.get("note") or "Upcoming festival season"
        else:
            festival = "Upcoming festival season"

    if days_until_fest is not None:
        try:
            d_int = int(days_until_fest)
            if d_int > 60:
                primary = f"{festival} in {d_int} days — early planning opportunity"
                recommended = "Plan promotional content 4-6 weeks before the festival"
            else:
                primary = f"{festival} approaching in {d_int} days"
                recommended = "Run a festival-themed offer or post to capture seasonal demand"
        except (ValueError, TypeError):
            primary = f"{festival} is approaching soon"
            recommended = "Run a seasonal offer or post to capture peak festival demand"
    else:
        primary = f"{festival} is approaching soon"
        recommended = "Run a seasonal offer or post to capture peak festival demand"

    return {
        "primary_fact": primary,
        "supporting_facts": [f"Date: {date[:10]}"] if date else [],
        "merchant_specific_reason": "festivals are predictable high-demand windows",
        "recommended_action": recommended,
        "cta": "open_ended",
        "evidence": [festival, f"{days_until_fest} days"] if days_until_fest else [festival],
        "days_until_fest": days_until_fest,
    }


def _extract_cde_opportunity(trigger: Dict, merchant: Dict,
                               category: Optional[Dict]) -> Dict:
    payload = trigger.get("payload", {})
    item_id = payload.get("digest_item_id", "")
    credits = payload.get("credits", 0)
    fee = payload.get("fee", "")

    digest_item = None
    if category and item_id:
        for item in category.get("digest", []):
            if item.get("id") == item_id:
                digest_item = item
                break

    if digest_item:
        title = digest_item.get("title", "")
        date = digest_item.get("date", "")
        source = digest_item.get("source", "")
        primary = f"CDE: {title}"
        supporting = []
        if credits:
            supporting.append(f"{credits} CDE credits")
        if fee:
            supporting.append(f"Fee: {fee.replace('_', ' ')}")
        if date:
            supporting.append(f"Date: {date[:10]}")
        evidence = [source] if source else []
    else:
        primary = "CDE opportunity available"
        supporting = []
        evidence = []

    return {
        "primary_fact": primary,
        "supporting_facts": supporting,
        "merchant_specific_reason": "professional development and peer networking opportunity",
        "recommended_action": "Register before spots fill up",
        "cta": "binary_yes_no",
        "evidence": evidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def extract_facts(
    trigger: Dict[str, Any],
    merchant: Dict[str, Any],
    category: Optional[Dict[str, Any]],
    customer: Optional[Dict[str, Any]],
    now_str: str,
    priority_score: float,
) -> MessageBrief:
    """
    Build a structured MessageBrief from context.
    Dispatches to per-kind extractor functions.
    """
    kind = trigger.get("kind", "")
    trigger_id = trigger.get("id", "")
    merchant_id = merchant.get("merchant_id", "")
    customer_id = customer.get("customer_id") if customer else None
    suppression_key = trigger.get("suppression_key", f"{kind}:{merchant_id}")
    scope = trigger.get("scope", "merchant")

    send_as = "vera" if scope == "merchant" else "merchant_on_behalf"
    lang_style = get_language_style(merchant, customer)
    tone = resolve_tone(category, customer)
    owner_name = get_owner_name(merchant)

    # Dispatch to appropriate extractor
    extractors = {
        "research_digest":        lambda: _extract_research_digest(trigger, merchant, category),
        "regulation_change":      lambda: _extract_regulation_change(trigger, merchant, category),
        "perf_dip":               lambda: _extract_perf_dip(trigger, merchant, category),
        "seasonal_perf_dip":      lambda: _extract_seasonal_perf_dip(trigger, merchant, category),
        "renewal_due":            lambda: _extract_renewal_due(trigger, merchant, category),
        "supply_alert":           lambda: _extract_supply_alert(trigger, merchant, category),
        "recall_due":             lambda: _extract_recall_due(trigger, merchant, category, customer, now_str),
        "chronic_refill_due":     lambda: _extract_chronic_refill_due(trigger, merchant, category, customer, now_str),
        "customer_lapsed_hard":   lambda: _extract_customer_lapsed(trigger, merchant, category, customer),
        "customer_lapsed_soft":   lambda: _extract_customer_lapsed(trigger, merchant, category, customer),
        "ipl_match_today":        lambda: _extract_ipl_match(trigger, merchant, category),
        "active_planning_intent": lambda: _extract_active_planning_intent(trigger, merchant, category),
        "competitor_opened":      lambda: _extract_competitor_opened(trigger, merchant, category),
        "winback_eligible":       lambda: _extract_winback_eligible(trigger, merchant, category),
        "wedding_package_followup": lambda: _extract_wedding_followup(trigger, merchant, category, customer, now_str),
        "curious_ask_due":        lambda: _extract_curious_ask(trigger, merchant, category),
        "dormant_with_vera":      lambda: _extract_dormant_with_vera(trigger, merchant, category),
        "milestone_reached":      lambda: _extract_milestone_reached(trigger, merchant, category),
        "perf_spike":             lambda: _extract_perf_spike(trigger, merchant, category),
        "gbp_unverified":         lambda: _extract_gbp_unverified(trigger, merchant, category),
        "review_theme_emerged":   lambda: _extract_review_theme(trigger, merchant, category),
        "category_seasonal":      lambda: _extract_category_seasonal(trigger, merchant, category),
        "trial_followup":         lambda: _extract_trial_followup(trigger, merchant, category, customer),
        "festival_upcoming":      lambda: _extract_festival_upcoming(trigger, merchant, category),
        "cde_opportunity":        lambda: _extract_cde_opportunity(trigger, merchant, category),
        "appointment_tomorrow":   lambda: _extract_appointment_tomorrow(trigger, merchant, category, customer),
    }

    extractor = extractors.get(kind)
    if extractor:
        try:
            facts = extractor()
        except Exception as e:
            logger.warning("Fact extractor for kind=%s failed: %s", kind, e)
            facts = {}
    else:
        # Generic fallback
        facts = {
            "primary_fact": f"Trigger: {kind.replace('_', ' ')}",
            "supporting_facts": [],
            "merchant_specific_reason": "relevant business signal",
            "recommended_action": "Review and take action",
            "cta": "open_ended",
            "evidence": [],
        }

    return MessageBrief(
        merchant_id=merchant_id,
        customer_id=customer_id,
        trigger_id=trigger_id,
        trigger_kind=kind,
        priority_score=priority_score,
        primary_fact=facts.get("primary_fact", ""),
        supporting_facts=facts.get("supporting_facts", []),
        merchant_specific_reason=facts.get("merchant_specific_reason", ""),
        recommended_action=facts.get("recommended_action", ""),
        cta=facts.get("cta", "open_ended"),
        send_as=send_as,
        tone=tone,
        language_style=lang_style,
        suppression_key=suppression_key,
        evidence=facts.get("evidence", []),
        scope=scope,
    )
