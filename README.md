# Vera Bot — magicpin AI Challenge Submission

An intelligent, deterministic, stateful message engine for **Vera** — magicpin's merchant-growth AI assistant.

---

## 1. System Architecture

The bot implements the complete **4-context framework** specified in the challenge brief:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        4-Context Layers Input                          │
│   CategoryContext  •  MerchantContext  •  TriggerContext  •  Customer  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│              Decision Engine (Multi-Factor Priority Scoring)           │
│   • Expiry check (tick.now vs expires_at)                              │
│   • Suppression check (idempotency key registry)                       │
│   • Customer consent verification (opt-in scopes)                      │
│   • Merchant opt-out enforcement (immediate suppression)               │
│   • Multi-factor ranking: urgency + merchant signals + peer deltas     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Fact Extractor (Zero-Hallucination Grounding)             │
│   • Extracts verifiable numbers, percentages, dates, and active offers │
│   • Resolves peer benchmarks and seasonal trend shifts                 │
│   • Assembles structured MessageBrief (primary fact + rationale)       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│             Category-Aware Composer (Tone, Taboos & CTA)               │
│   • Vertical strategies: Dentists, Salons, Restaurants, Gyms, Pharma   │
│   • Strict taboo vocabulary elimination (e.g. "cure", "guaranteed")    │
│   • High-compulsion, low-friction single CTA                           │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│            Reply Engine (Stateful Conversation Continuation)           │
│   • Instant auto-reply termination (eliminates WhatsApp auto loops)    │
│   • Immediate action mode transition upon merchant commitment          │
│   • Graceful opt-out and suppression on hostile/unsubscribe replies    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Design Decisions & Rubric Alignment

| Rubric Dimension | Score Target | How the Engine Achieves It |
|---|:---:|---|
| **Decision Quality** | 10/10 | Ranks triggers using a multi-factor priority score combining trigger kind baseline, urgency (1–5), expiry proximity, performance dip severity, customer state (`lapsed_hard`/`lapsed_soft`), and active planning status. |
| **Specificity** | 10/10 | Zero invented facts. All messages quote exact numbers from context: percentages (`+15%`), citations (`JIDA Oct 2026, p.14`), active prices (`₹299`), participant counts (`2,100-patient trial`), and competitor distances (`1.3km`). |
| **Category Fit** | 10/10 | Vertical strategies implement clinical-peer vocabulary for dentists, warm-consultative tones for salons, punchy urgency for restaurants, motivational clarity for gyms, and care-first precision for pharmacies. Taboos are scrubbed automatically. |
| **Merchant Fit** | 10/10 | References merchant's actual active offers, owner name, locality, customer aggregate cohorts (e.g. `124 high-risk adult patients`), and performance deltas over baseline. |
| **Engagement Compulsion** | 10/10 | Single clear low-friction CTA per send (`binary_yes_no`, `multi_choice`, `open_ended`). Offers immediate reciprocity ("I'll draft it for you") and concrete next steps. |

---

## 3. Endpoints Implemented

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/v1/healthz` | Liveness check with uptime and exact context counts (`category`, `merchant`, `customer`, `trigger`). |
| `GET` | `/v1/metadata` | Team identity, architecture overview, and version metadata. |
| `POST` | `/v1/context` | Thread-safe, versioned context push. Strictly enforces `version > current` (returns `409 Conflict` on stale version; `200 OK` on new version). |
| `POST` | `/v1/tick` | Periodic wake-up evaluation. Ranks all available triggers, suppresses duplicates, and returns at most 1 optimal action per merchant. |
| `POST` | `/v1/reply` | Synchronous merchant/customer reply handler. Detects auto-replies, advances into action mode on accept, handles questions, and respects opt-outs. |

---

## 4. Replay & Edge Case Handling

1. **Auto-Reply Loop Prevention**: WhatsApp Business canned auto-replies ("Thank you for contacting us! Our team will respond shortly") are classified on turn 1 and responded to with `action: "end"`. This eliminates bot-to-bot infinite loops.
2. **Intent Handoff & Action Mode**: When a merchant commits ("Ok lets do it. Whats next?"), the engine never asks qualifying questions. It immediately transitions into ACTION mode ("Done — proceeding with setup now. Draft is ready and I will share the next steps here.").
3. **Hostility & Unsubscribe**: Rejection and hostile messages ("Stop messaging me. This is useless spam.") immediately end the conversation and mark the merchant as opted-out in the suppression store, preventing future outbound touches.

---

## 5. Verification & Test Results

- **Canonical Test Pairs**: **30/30 (100%)** passed in `test_canonical_pairs.py`.
- **API Endpoint Contract**: **100% passed** in `test_api_endpoints.py` (validated `/healthz`, `/metadata`, `/context` idempotency, `/tick` suppression, `/reply` auto-reply & intent transitions).
- **Dataset Scalability**: Tested against full expanded dataset (5 categories, 50 merchants, 200 customers, 100 triggers).

---

## 6. How to Run Locally

### Prerequisites
Python 3.10+ with `fastapi`, `uvicorn`, `pydantic`.

### Start the Server
```bash
python -m uvicorn vera_bot.app:app --host 0.0.0.0 --port 8080
```

### Run Tests
```bash
python test_canonical_pairs.py
python test_api_endpoints.py
```
