import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent / "vera_bot"))

from app import _ctx, _sup, app
from context_store import ContextStore
from decision_engine import DecisionEngine
from fact_extractor import extract_facts
from suppression import SuppressionStore


ROOT = Path(__file__).parent / "expanded"


@pytest.fixture(autouse=True)
def clear_api_state():
    _ctx.clear()
    _sup.clear()
    yield
    _ctx.clear()
    _sup.clear()


def load(relative):
    with open(ROOT / relative, encoding="utf-8") as handle:
        return json.load(handle)


def test_live_unicode_is_utf8_on_wire():
    merchant = load("merchants/m_001_drmeera_dentist_delhi.json")
    merchant["offers"] = [{"title": "Dental Cleaning @ ₹299", "status": "active"}]
    category = load("categories/dentists.json")
    trigger = {
        "id": "unicode-regression",
        "kind": "perf_dip",
        "merchant_id": merchant["merchant_id"],
        "payload": {"metric": "calls", "delta_pct": -50, "window": "7d"},
        "suppression_key": "unicode-regression-key",
    }
    brief = extract_facts(trigger, merchant, category, None, "2026-09-26T00:00:00Z", 5)
    assert "₹299" in brief.recommended_action

    client = TestClient(app)
    for scope, context_id, payload in [
        ("category", "dentists", category),
        ("merchant", merchant["merchant_id"], merchant),
        ("trigger", trigger["id"], trigger),
    ]:
        response = client.post(
            "/v1/context",
            json={"scope": scope, "context_id": context_id, "version": 1, "payload": payload},
        )
        assert response.status_code == 200

    response = client.post(
        "/v1/tick",
        json={"now": "2026-09-26T00:00:00Z", "available_triggers": [trigger["id"]]},
    )
    assert response.status_code == 200
    assert "₹299" in response.text
    assert "?299" not in response.text
    assert "-50%" in response.text
    assert b"\xe2\x82\xb9" in response.content


def test_customer_recall_with_consent_is_eligible():
    category = load("categories/dentists.json")
    merchant = load("merchants/m_001_drmeera_dentist_delhi.json")
    customer = load("customers/c_001_priya_for_m001.json")
    trigger = load("triggers/trg_003_recall_due_priya.json")

    context = ContextStore()
    suppression = SuppressionStore()
    for scope, context_id, payload in [
        ("category", category["slug"], category),
        ("merchant", merchant["merchant_id"], merchant),
        ("customer", customer["customer_id"], customer),
        ("trigger", trigger["id"], trigger),
    ]:
        assert context.push(scope, context_id, 1, payload)[0]

    ranked = DecisionEngine(context, suppression).rank_triggers(
        [trigger["id"]], "2026-06-01T10:00:00Z"
    )
    assert len(ranked) == 1
    assert ranked[0][-1]["customer_id"] == customer["customer_id"]


def test_customer_recall_with_opt_out_is_rejected():
    category = load("categories/dentists.json")
    merchant = load("merchants/m_001_drmeera_dentist_delhi.json")
    customer = load("customers/c_001_priya_for_m001.json")
    customer["preferences"]["reminder_opt_in"] = False
    trigger = load("triggers/trg_003_recall_due_priya.json")

    context = ContextStore()
    suppression = SuppressionStore()
    for scope, context_id, payload in [
        ("category", category["slug"], category),
        ("merchant", merchant["merchant_id"], merchant),
        ("customer", customer["customer_id"], customer),
        ("trigger", trigger["id"], trigger),
    ]:
        context.push(scope, context_id, 1, payload)

    assert DecisionEngine(context, suppression).rank_triggers(
        [trigger["id"]], "2026-06-01T10:00:00Z"
    ) == []


def test_supplied_keys_are_preserved_and_distinct():
    merchant = load("merchants/m_001_drmeera_dentist_delhi.json")
    category = load("categories/dentists.json")
    context = ContextStore()
    suppression = SuppressionStore()
    context.push("category", "dentists", 1, category)
    context.push("merchant", merchant["merchant_id"], 1, merchant)

    triggers = []
    for suffix in ("one", "two"):
        trigger = {
            "id": f"distinct-{suffix}",
            "kind": "perf_dip",
            "merchant_id": merchant["merchant_id"],
            "payload": {"metric": "calls", "delta_pct": -50},
            "suppression_key": f"perf_dip:{merchant['merchant_id']}:calls:{suffix}",
        }
        context.push("trigger", trigger["id"], 1, trigger)
        triggers.append(trigger)

    ranked = DecisionEngine(context, suppression).rank_triggers(
        [t["id"] for t in triggers], "2026-06-01T10:00:00Z"
    )
    assert {item[2]["suppression_key"] for item in ranked} == {
        triggers[0]["suppression_key"], triggers[1]["suppression_key"]
    }
    suppression.mark_sent(triggers[0]["suppression_key"])
    ranked_again = DecisionEngine(context, suppression).rank_triggers(
        [t["id"] for t in triggers], "2026-06-01T10:00:00Z"
    )
    assert [item[1] for item in ranked_again] == [triggers[1]["id"]]
