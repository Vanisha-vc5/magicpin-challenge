import json
import sys
import io
from pathlib import Path
from starlette.testclient import TestClient

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "vera_bot"))

from app import app, _ctx, _sup

client = TestClient(app)

def test_healthz():
    print("\n--- Testing GET /v1/healthz ---")
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200, f"healthz returned {resp.status_code}"
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "contexts_loaded" in data
    print("[PASS] healthz:", data)

def test_metadata():
    print("\n--- Testing GET /v1/metadata ---")
    resp = client.get("/v1/metadata")
    assert resp.status_code == 200, f"metadata returned {resp.status_code}"
    data = resp.json()
    assert "team_name" in data
    assert "model" in data
    assert "approach" in data
    print("[PASS] metadata:", data)

def test_context_push():
    print("\n--- Testing POST /v1/context ---")
    
    # 1. Push category
    with open("expanded/categories/dentists.json", "r", encoding="utf-8") as f:
        dentist_cat = json.load(f)
    resp = client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": dentist_cat,
        "delivered_at": "2026-04-29T10:00:00Z"
    })
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    print("[PASS] Push category dentists v1")

    # 2. Push merchant
    with open("expanded/merchants/m_001_drmeera_dentist_delhi.json", "r", encoding="utf-8") as f:
        meera = json.load(f)
    resp = client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": "m_001_drmeera_dentist_delhi",
        "version": 1,
        "payload": meera,
        "delivered_at": "2026-04-29T10:00:00Z"
    })
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    print("[PASS] Push merchant m_001 v1")

    # 3. Idempotent check: same version -> 409
    resp = client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": "m_001_drmeera_dentist_delhi",
        "version": 1,
        "payload": meera,
        "delivered_at": "2026-04-29T10:05:00Z"
    })
    assert resp.status_code == 409
    assert resp.json()["accepted"] is False
    assert resp.json()["reason"] == "stale_version"
    print("[PASS] Re-push same version -> 409 stale_version")

    # 4. Version bump -> 200
    meera_v2 = dict(meera)
    meera_v2["performance"]["views"] = 3000
    resp = client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": "m_001_drmeera_dentist_delhi",
        "version": 2,
        "payload": meera_v2,
        "delivered_at": "2026-04-29T10:10:00Z"
    })
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    print("[PASS] Version bump v2 -> 200 accepted")

    # 5. Push customer
    with open("expanded/customers/c_001_priya_for_m001.json", "r", encoding="utf-8") as f:
        priya = json.load(f)
    resp = client.post("/v1/context", json={
        "scope": "customer",
        "context_id": "c_001_priya_for_m001",
        "version": 1,
        "payload": priya,
        "delivered_at": "2026-04-29T10:00:00Z"
    })
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    print("[PASS] Push customer c_001 v1")

    # 6. Push trigger
    with open("expanded/triggers/trg_001_research_digest_dentists.json", "r", encoding="utf-8") as f:
        trg1 = json.load(f)
    resp = client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": "trg_001_research_digest_dentists",
        "version": 1,
        "payload": trg1,
        "delivered_at": "2026-04-29T10:00:00Z"
    })
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    print("[PASS] Push trigger trg_001 v1")

def test_tick():
    print("\n--- Testing POST /v1/tick ---")
    resp = client.post("/v1/tick", json={
        "now": "2026-04-29T10:00:00Z",
        "available_triggers": ["trg_001_research_digest_dentists"]
    })
    assert resp.status_code == 200
    data = resp.json()
    actions = data.get("actions", [])
    assert len(actions) == 1, f"Expected 1 action, got {len(actions)}"
    act = actions[0]
    assert act["merchant_id"] == "m_001_drmeera_dentist_delhi"
    assert act["send_as"] == "vera"
    assert len(act["body"]) > 20
    assert act["cta"] in ["binary_yes_no", "single_choice", "multi_choice", "open_ended", "none"]
    assert len(act["rationale"]) > 10
    print("[PASS] tick returned valid action:")
    print("       body:", act["body"])
    print("       cta:", act["cta"])
    print("       suppression_key:", act["suppression_key"])

    # Test suppression: second tick with same trigger should yield 0 actions
    resp2 = client.post("/v1/tick", json={
        "now": "2026-04-29T10:05:00Z",
        "available_triggers": ["trg_001_research_digest_dentists"]
    })
    assert resp2.status_code == 200
    actions2 = resp2.json().get("actions", [])
    assert len(actions2) == 0, f"Expected 0 actions due to suppression, got {len(actions2)}"
    print("[PASS] Suppression confirmed: subsequent tick returned 0 actions")

def test_replies():
    print("\n--- Testing POST /v1/reply ---")

    # 1. Auto-reply detection
    resp = client.post("/v1/reply", json={
        "conversation_id": "conv_test_auto",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "customer_id": None,
        "from_role": "merchant",
        "message": "Thank you for contacting us! Our team will respond shortly.",
        "received_at": "2026-04-29T10:15:00Z",
        "turn_number": 2
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end", f"Expected end on auto-reply, got {data['action']}"
    print("[PASS] Auto-reply test: correctly returned action='end'")

    # 2. Intent transition to ACTION mode
    resp = client.post("/v1/reply", json={
        "conversation_id": "conv_test_intent",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "customer_id": None,
        "from_role": "merchant",
        "message": "Ok lets do it. Whats next?",
        "received_at": "2026-04-29T10:16:00Z",
        "turn_number": 2
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "send", f"Expected send on commitment, got {data['action']}"
    body = data.get("body", "").lower()
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    assert any(w in body for w in actioning), f"No action word in: {body}"
    assert not any(w in body for w in qualifying), f"Qualifying word in: {body}"
    print("[PASS] Intent transition: switched to action mode without qualifying:", data["body"])

    # 3. Hostile handling
    resp = client.post("/v1/reply", json={
        "conversation_id": "conv_test_hostile",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "customer_id": None,
        "from_role": "merchant",
        "message": "Stop messaging me. This is useless spam.",
        "received_at": "2026-04-29T10:17:00Z",
        "turn_number": 2
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "end", f"Expected end on hostile, got {data['action']}"
    print("[PASS] Hostile handling: gracefully ended and suppressed merchant")

    # 4. Wait request
    resp = client.post("/v1/reply", json={
        "conversation_id": "conv_test_wait",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "customer_id": None,
        "from_role": "merchant",
        "message": "I'm busy right now, let's talk tomorrow",
        "received_at": "2026-04-29T10:18:00Z",
        "turn_number": 2
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "wait"
    assert data["wait_seconds"] == 86400
    print("[PASS] Wait request: action='wait', wait_seconds=86400")

def main():
    test_healthz()
    test_metadata()
    test_context_push()
    test_tick()
    test_replies()
    print("\n==========================================")
    print("ALL API ENDPOINT TESTS PASSED!")
    print("==========================================")

if __name__ == "__main__":
    main()
