import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vera_bot"))

from composer import compose_message
from fact_extractor import extract_facts
from formatting import format_inr, format_percentage, relative_shortfall


MERCHANT = {
    "merchant_id": "m_test",
    "identity": {
        "name": "Dr. Meera's Dental Clinic",
        "owner_first_name": "Meera",
        "languages": ["en", "hi"],
    },
    "performance": {"calls": 18, "ctr": 0.021},
    "offers": [{"title": "Dental Cleaning @ ₹299", "status": "active"}],
}
CATEGORY = {
    "slug": "dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": []},
    "peer_stats": {"avg_ctr": 0.030},
}


def make_perf(delta, offer_title="Dental Cleaning @ ₹299", status="active"):
    merchant = dict(MERCHANT)
    merchant["offers"] = ([{"title": offer_title, "status": status}]
                           if offer_title else [])
    trigger = {
        "id": f"t_perf_{delta}",
        "kind": "perf_dip",
        "scope": "merchant",
        "payload": {"metric": "calls", "current": 9, "baseline": 18,
                     "delta_pct": delta, "window": "7d"},
    }
    brief = extract_facts(trigger, merchant, CATEGORY, None,
                          "2026-09-26T10:00:00Z", 5.0)
    return brief, compose_message(brief, merchant, CATEGORY, None)


def test_percentage_semantics_are_explicit():
    assert format_percentage(-50) == "-50%"
    assert format_percentage(50) == "50%"
    assert format_percentage(0) == "0%"
    assert format_percentage(None) == ""
    assert format_percentage(0.5) == "0.5%"
    assert format_percentage(0.5, semantics="ratio") == "50%"
    assert relative_shortfall(0.021, 0.030) == 30.0
    assert relative_shortfall(0.030, 0.030) == 0.0
    assert relative_shortfall(0.040, 0.030) == 0.0


def test_perf_dip_uses_percentage_points_and_one_yes_no_cta():
    brief, result = make_perf(-50)
    assert brief.primary_fact == "calls -50% over 7d vs baseline of 18"
    assert "-5000%" not in result["body"]
    assert "-50%" in result["body"]
    assert "₹299" in result["body"]
    assert "?299" not in result["body"]
    assert result["cta"] == "binary_yes_no"
    assert "Reply YES" in result["body"]
    assert result["body"].lower().count("optimize listing to recover calls") == 1
    assert "30% relative shortfall" in result["body"]


def test_other_delta_values_do_not_double_convert():
    for delta, expected in [(50, "50%"), (-5, "-5%"), (0, "0%")]:
        brief, result = make_perf(delta)
        assert expected in brief.primary_fact
        assert "5000%" not in result["body"]
        assert "500%" not in result["body"]


def test_delta_falls_back_to_current_and_baseline_only_when_missing():
    merchant = dict(MERCHANT)
    merchant["offers"] = []
    category = dict(CATEGORY)
    category["offer_catalog"] = []
    trigger = {
        "id": "derived-delta",
        "kind": "perf_dip",
        "merchant_id": merchant["merchant_id"],
        "payload": {"metric": "calls", "current": 9, "baseline": 18},
    }
    brief = extract_facts(trigger, merchant, category, None,
                          "2026-09-26T10:00:00Z", 5.0)
    assert "-50%" in brief.primary_fact


def test_category_catalog_offer_is_used_without_invention():
    merchant = dict(MERCHANT)
    merchant["offers"] = []
    category = dict(CATEGORY)
    category["offer_catalog"] = [{"title": "Dental Cleaning ₹299"}]
    brief, result = make_perf(-50, "")
    brief = extract_facts(
        {"id": "catalog-offer", "kind": "perf_dip",
         "payload": {"metric": "calls", "delta_pct": -50}},
        merchant, category, None, "2026-09-26T10:00:00Z", 5.0,
    )
    result = compose_message(brief, merchant, category, None)
    assert "Dental Cleaning ₹299" in result["body"]


def test_hinglish_and_unicode_offer_are_preserved():
    merchant = dict(MERCHANT)
    merchant["offers"] = [{"title": "दांत सफाई ₹999 - weekend offer", "status": "active"}]
    brief = extract_facts(
        {"id": "unicode-offer", "kind": "perf_dip",
         "payload": {"metric": "calls", "delta_pct": 25}},
        merchant, CATEGORY, None, "2026-09-26T10:00:00Z", 5.0,
    )
    result = compose_message(brief, merchant, CATEGORY, None)
    assert "दांत सफाई ₹999 - weekend offer" in result["body"]
    assert "â" not in result["body"]
    assert "25%" in result["body"]


def test_offers_and_currency_are_grounded():
    assert format_inr(299) == "₹299"
    assert format_inr(999) == "₹999"
    _, active = make_perf(-50, "Dental Cleaning @ ₹999")
    assert "₹999" in active["body"]
    _, expired = make_perf(-50, "Dental Cleaning @ ₹999", "expired")
    assert "₹999" not in expired["body"]
    _, missing = make_perf(-50, "")
    assert "₹" not in missing["body"]


def test_unicode_content_survives_composition():
    merchant = dict(MERCHANT)
    merchant["offers"] = [{"title": "दांत सफाई @ ₹299", "status": "active"}]
    brief, result = make_perf(-5, "दांत सफाई @ ₹299")
    assert "दांत सफाई @ ₹299" in brief.recommended_action
    assert "दांत सफाई @ ₹299" in result["body"]
    assert "?299" not in result["body"]


def test_ten_unseen_combinations_remain_grounded():
    cases = [
        (-99, "Dental Cleaning @ ₹999", "active"),
        (-12, "Deep Cleaning @ ₹1,299", "active"),
        (1, "Dental Cleaning @ ₹299", "active"),
        (7, "", "active"),
        (0, "Dental Cleaning @ ₹299", "expired"),
        (-50, "", "active"),
        (50, "दांत सफाई @ ₹299", "active"),
        (-5, "Dental Cleaning @ ₹999", "expired"),
        (23, "Dental Cleaning @ ₹2,000", "active"),
        (-1, "", "expired"),
    ]
    for delta, offer, status in cases:
        brief, result = make_perf(delta, offer, status)
        assert result["body"]
        assert result["cta"] == "binary_yes_no"
        assert "?299" not in result["body"]
        assert f"{delta}%" in result["body"]
