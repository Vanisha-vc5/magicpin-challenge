"""
app.py — Vera Bot FastAPI Application

Implements all 5 required endpoints:
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/context
  POST /v1/tick
  POST /v1/reply

Start with: uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from context_store import ContextStore
from decision_engine import DecisionEngine
from fact_extractor import extract_facts
from composer import compose_message
from reply_engine import ReplyEngine
from suppression import SuppressionStore
from models import (
    ContextAck,
    ContextPushRequest,
    TickAction,
    TickRequest,
    TickResponse,
    ReplyRequest,
    ReplyResponse,
    HealthzResponse,
    MetadataResponse,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("vera.app")

# ─────────────────────────────────────────────
# Global State (singletons)
# ─────────────────────────────────────────────

_start_time = time.time()
_ctx = ContextStore()
_sup = SuppressionStore()
_engine = DecisionEngine(_ctx, _sup)
_reply_engine = ReplyEngine(_sup, _ctx)

# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title="Vera Bot — Magicpin AI Challenge",
    version="1.0.0",
    description="Merchant engagement AI for Magicpin Vera Challenge",
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _generate_conversation_id(merchant_id: str, trigger_id: str) -> str:
    """Generate a deterministic conversation ID from merchant + trigger."""
    return f"conv_{merchant_id[:8]}_{trigger_id[:8]}_{uuid.uuid4().hex[:6]}"


# ─────────────────────────────────────────────
# Error handling
# ─────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc)},
    )


# ─────────────────────────────────────────────
# GET /v1/healthz
# ─────────────────────────────────────────────

@app.get("/v1/healthz", response_model=HealthzResponse, tags=["System"])
async def healthz():
    """Health check endpoint."""
    uptime = int(time.time() - _start_time)
    return HealthzResponse(
        status="ok",
        uptime_seconds=uptime,
        contexts_loaded=_ctx.counts(),
    )


# ─────────────────────────────────────────────
# GET /v1/metadata
# ─────────────────────────────────────────────

@app.get("/v1/metadata", response_model=MetadataResponse, tags=["System"])
async def metadata():
    """Bot metadata endpoint."""
    return MetadataResponse(
        team_name="Vera Challenge Team",
        team_members=["Candidate"],
        model="rule-based + fact-extractor (no external LLM call)",
        approach=(
            "4-context composition framework: CategoryContext (voice, catalog, digest), "
            "MerchantContext (perf, signals, history), TriggerContext (kind, urgency, payload), "
            "CustomerContext (state, consent, preferences). "
            "Decision engine ranks eligible triggers by multi-factor priority score. "
            "Fact extractor builds structured MessageBrief from context. "
            "Category strategy composer renders final WhatsApp message."
        ),
        contact_email="vera@challenge.test",
        version="1.0.0",
        submitted_at=_now_iso(),
    )


# ─────────────────────────────────────────────
# POST /v1/context
# ─────────────────────────────────────────────

@app.post("/v1/context", response_model=ContextAck, tags=["Context"])
async def push_context(req: ContextPushRequest):
    """
    Accept and store a context update.
    Versioning: accept only if incoming version > stored version.
    """
    accepted, reason, current_version = _ctx.push(
        req.scope, req.context_id, req.version, req.payload
    )

    if accepted:
        logger.info("Context stored: scope=%s id=%s v=%d",
                    req.scope, req.context_id, req.version)
        return ContextAck(
            accepted=True,
            ack_id=uuid.uuid4().hex,
            stored_at=_now_iso(),
            current_version=req.version,
        )
    else:
        if reason == "invalid_scope":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope: '{req.scope}'. Must be one of: category, merchant, customer, trigger."
            )
        # Stale version — return 409 but still a valid response body
        logger.warning("Stale context rejected: scope=%s id=%s v_in=%d v_cur=%s",
                       req.scope, req.context_id, req.version, current_version)
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
                "details": f"Incoming version {req.version} <= stored version {current_version}",
            }
        )


# ─────────────────────────────────────────────
# POST /v1/tick
# ─────────────────────────────────────────────

@app.post("/v1/tick", response_model=TickResponse, tags=["Engagement"])
async def tick(req: TickRequest):
    """
    Process a tick event with a list of available trigger IDs.
    Returns at most 1 action per merchant (highest-priority trigger).
    """
    now_str = req.now or _now_iso()
    available = req.available_triggers

    logger.info("Tick received: now=%s triggers=%d", now_str, len(available))

    if not available:
        return TickResponse(actions=[])

    # Rank all eligible triggers
    ranked = _engine.rank_triggers(available, now_str)

    if not ranked:
        logger.info("Tick: no eligible triggers after filtering")
        return TickResponse(actions=[])

    # Select one action per merchant (highest-ranked trigger per merchant)
    # This prevents message flood for a single merchant
    seen_merchants: set = set()
    selected_actions: List[TickAction] = []
    MAX_ACTIONS = 10  # cap per tick

    for score, tid, trigger, merchant, category, customer in ranked:
        if len(selected_actions) >= MAX_ACTIONS:
            break

        merchant_id = merchant.get("merchant_id", "")
        if merchant_id in seen_merchants:
            continue  # skip — already have an action for this merchant
        seen_merchants.add(merchant_id)

        # Extract facts → MessageBrief
        try:
            brief = extract_facts(
                trigger=trigger,
                merchant=merchant,
                category=category,
                customer=customer,
                now_str=now_str,
                priority_score=score,
            )
        except Exception as e:
            logger.error("Fact extraction failed for trigger %s: %s", tid, e)
            continue

        # Compose message
        try:
            composed = compose_message(
                brief=brief,
                merchant=merchant,
                category=category,
                customer=customer,
            )
        except Exception as e:
            logger.error("Composition failed for trigger %s: %s", tid, e)
            continue

        # Skip empty messages
        if not composed.get("body"):
            logger.warning("Empty body for trigger %s — skip", tid)
            continue

        # Generate conversation ID
        conv_id = _generate_conversation_id(merchant_id, tid)

        # Create conversation state
        _sup.create_conversation(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=brief.customer_id,
            trigger_id=tid,
        )
        _sup.update_conversation(
            conv_id,
            pending_action=brief.recommended_action,
            last_outbound_body=composed["body"],
            last_cta=composed["cta"],
        )

        # Mark suppression
        if composed.get("suppression_key"):
            _sup.mark_sent(composed["suppression_key"])

        logger.info("Action created: conv=%s merchant=%s trigger=%s kind=%s score=%.1f",
                    conv_id, merchant_id, tid, trigger.get("kind"), score)

        action = TickAction(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=brief.customer_id,
            send_as=composed["send_as"],
            trigger_id=tid,
            template_name=composed["template_name"],
            template_params=composed["template_params"],
            body=composed["body"],
            cta=composed["cta"],
            suppression_key=composed["suppression_key"],
            rationale=composed["rationale"],
        )
        selected_actions.append(action)

    logger.info("Tick complete: %d actions selected", len(selected_actions))
    return TickResponse(actions=selected_actions)


# ─────────────────────────────────────────────
# POST /v1/reply
# ─────────────────────────────────────────────

@app.post("/v1/reply", response_model=ReplyResponse, tags=["Engagement"])
async def reply(req: ReplyRequest):
    """
    Handle an incoming merchant/customer reply.
    Returns the next action: send, wait, or end.
    """
    conv_id = req.conversation_id
    merchant_id = req.merchant_id
    customer_id = req.customer_id
    message = req.message
    turn = req.turn_number

    logger.info("Reply: conv=%s turn=%d role=%s msg='%s'",
                conv_id, turn, req.from_role, message[:60])

    # Ensure conversation state exists
    conv = _sup.get_conversation(conv_id)
    if not conv:
        # Create a minimal conversation state
        _sup.create_conversation(
            conversation_id=conv_id,
            merchant_id=merchant_id or "",
            customer_id=customer_id,
        )
        conv = _sup.get_conversation(conv_id)

    # Process reply
    result = _reply_engine.handle_reply({
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "message": message,
        "from_role": req.from_role,
        "turn_number": turn,
    })

    # Build response
    action = result.get("action", "end")
    body = result.get("body")
    cta = result.get("cta", "open_ended")
    wait_seconds = result.get("wait_seconds")
    rationale = result.get("rationale", "")

    return ReplyResponse(
        action=action,
        body=body,
        cta=cta if action == "send" else None,
        wait_seconds=wait_seconds if action == "wait" else None,
        rationale=rationale,
    )


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
