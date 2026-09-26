"""
models.py — Pydantic data models for Vera Bot.

All API request/response schemas and internal data structures.
"""
from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# API Request Models
# ─────────────────────────────────────────────

class ContextPushRequest(BaseModel):
    scope: Literal["category", "merchant", "customer", "trigger"]
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


# ─────────────────────────────────────────────
# API Response Models
# ─────────────────────────────────────────────

class ContextAck(BaseModel):
    accepted: bool
    ack_id: Optional[str] = None
    stored_at: Optional[str] = None
    reason: Optional[str] = None
    current_version: Optional[int] = None
    details: Optional[str] = None


class TickAction(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: str  # "vera" | "merchant_on_behalf"
    trigger_id: str
    template_name: str
    template_params: List[str] = Field(default_factory=list)
    body: str
    cta: str  # "open_ended" | "binary_yes_no" | "multi_choice_slot" | "none"
    suppression_key: str
    rationale: str


class TickResponse(BaseModel):
    actions: List[TickAction] = Field(default_factory=list)


class ReplyResponse(BaseModel):
    action: Literal["send", "wait", "end"]
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None
    rationale: str


class HealthzResponse(BaseModel):
    status: str
    uptime_seconds: int
    contexts_loaded: Dict[str, int]


class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str


# ─────────────────────────────────────────────
# Internal data types
# ─────────────────────────────────────────────

class MessageBrief(BaseModel):
    """Structured brief produced before composing the message.
    Represents a fully-deterministic description of what to say and why.
    """
    merchant_id: str
    customer_id: Optional[str] = None
    trigger_id: str
    trigger_kind: str
    priority_score: float
    primary_fact: str
    supporting_facts: List[str] = Field(default_factory=list)
    merchant_specific_reason: str
    recommended_action: str
    cta: str
    send_as: str
    tone: str
    language_style: str  # "en", "hi-en mix", "hi", etc.
    suppression_key: str
    evidence: List[str] = Field(default_factory=list)
    scope: str  # "merchant" | "customer"


class ConversationState(BaseModel):
    """Per-conversation persistent state."""
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    status: str = "active"  # active | waiting | ended
    turn_count: int = 0
    auto_reply_count: int = 0
    last_outbound_body: Optional[str] = None
    last_cta: Optional[str] = None
    pending_action: Optional[str] = None  # describes what was offered/asked
    merchant_rejected: bool = False
    merchant_accepted: bool = False
    message_history: List[Dict[str, Any]] = Field(default_factory=list)
    wait_until: Optional[str] = None
    suppressed: bool = False
