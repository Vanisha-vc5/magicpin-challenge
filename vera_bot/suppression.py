"""
suppression.py — Suppression and conversation state management.

Maintains:
  1. Suppression registry: suppression_key → bool (sent/blocked)
  2. Conversation registry: conversation_id → ConversationState
  3. Merchant-level opt-out registry: merchant_id → bool
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from models import ConversationState


class SuppressionStore:
    """
    Tracks sent suppression keys to prevent duplicate outreach.
    Also tracks per-merchant opt-outs.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sent_keys: Set[str] = set()
        self._merchant_optout: Set[str] = set()
        self._conversations: Dict[str, ConversationState] = {}

    # ─────────────────────────────────────────
    # Suppression Keys
    # ─────────────────────────────────────────

    def is_suppressed(self, suppression_key: str) -> bool:
        with self._lock:
            return suppression_key in self._sent_keys

    def mark_sent(self, suppression_key: str):
        with self._lock:
            self._sent_keys.add(suppression_key)

    def clear_key(self, suppression_key: str):
        with self._lock:
            self._sent_keys.discard(suppression_key)

    def clear(self):
        """Clear transient suppression and conversation state."""
        with self._lock:
            self._sent_keys.clear()
            self._merchant_optout.clear()
            self._conversations.clear()

    # ─────────────────────────────────────────
    # Merchant Opt-Out
    # ─────────────────────────────────────────

    def merchant_opted_out(self, merchant_id: str) -> bool:
        with self._lock:
            return merchant_id in self._merchant_optout

    def suppress_merchant(self, merchant_id: str):
        with self._lock:
            self._merchant_optout.add(merchant_id)

    # ─────────────────────────────────────────
    # Conversations
    # ─────────────────────────────────────────

    def get_conversation(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._conversations.get(conversation_id)

    def create_conversation(self, conversation_id: str, merchant_id: str,
                            customer_id: Optional[str] = None,
                            trigger_id: Optional[str] = None) -> ConversationState:
        with self._lock:
            state = ConversationState(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
            )
            self._conversations[conversation_id] = state
            return state

    def update_conversation(self, conversation_id: str, **kwargs):
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                for k, v in kwargs.items():
                    if hasattr(state, k):
                        setattr(state, k, v)

    def add_message_to_conversation(self, conversation_id: str,
                                    role: str, body: str):
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.message_history.append({
                    "role": role,
                    "body": body,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                state.turn_count += 1

    def end_conversation(self, conversation_id: str):
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.status = "ended"

    def is_conversation_ended(self, conversation_id: str) -> bool:
        with self._lock:
            state = self._conversations.get(conversation_id)
            return state.status == "ended" if state else False

    def get_last_outbound(self, conversation_id: str) -> Optional[str]:
        with self._lock:
            state = self._conversations.get(conversation_id)
            return state.last_outbound_body if state else None

    def get_auto_reply_count(self, conversation_id: str) -> int:
        with self._lock:
            state = self._conversations.get(conversation_id)
            return state.auto_reply_count if state else 0

    def increment_auto_reply(self, conversation_id: str):
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.auto_reply_count += 1

    def reset_auto_reply(self, conversation_id: str):
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.auto_reply_count = 0

    def get_all_active_conversations(self) -> List[ConversationState]:
        with self._lock:
            return [s for s in self._conversations.values()
                    if s.status == "active"]
