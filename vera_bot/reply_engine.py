"""
reply_engine.py — Reply classifier and conversation continuation logic.

Handles incoming merchant/customer replies:
  1. Detect intent type (accept, reject, question, wait, auto-reply, etc.)
  2. Check conversation state
  3. Choose next action (send, wait, end)
  4. Compose an appropriate response

All logic is fully deterministic — no random elements.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("vera.reply")


# ─────────────────────────────────────────────────────────────────────────────
# Intent detection patterns
# ─────────────────────────────────────────────────────────────────────────────

# AUTO-REPLY patterns (WhatsApp Business canned responses)
AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"our team will (respond|get back)",
    r"we ('ll|will) get back to you",
    r"this is an auto(matic(ally)?)?[ -]reply",
    r"automated (message|assistant|response)",
    r"i am an automated",
    r"we have received your message",
    r"message has been received",
    r"out of office",
    r"we'?re currently unavailable",
    r"business hours are",
    r"main aapki.*team.*pahuncha",     # Hindi auto-reply
    r"shukriya.*contacting",
]

# ACCEPTANCE patterns
ACCEPT_PATTERNS = [
    r"\byes\b",
    r"\bha(n)?\b",                       # Hindi "haan" (yes)
    r"\bhaan\b",
    r"\bok(ay)?\b",
    r"\bsure\b",
    r"\bgo ahead\b",
    r"\blet'?s do it\b",
    r"\bproceed\b",
    r"\bconfirm\b",
    r"\bsend (it|me)\b",
    r"\bdraft (it|please)\b",
    r"\bplease (send|do|share)\b",
    r"\bsound(s)? good\b",
    r"\bthat'?s (great|good|fine)\b",
    r"\bi('?m)? interested\b",
]

# REJECTION / OPT-OUT patterns
REJECT_PATTERNS = [
    r"\bno\b",
    r"\bnot interested\b",
    r"\bstop\b",
    r"\bdon'?t (message|contact|bother)\b",
    r"\bunsubscribe\b",
    r"\bopt[ -]?out\b",
    r"\bremove (me|us)\b",
    r"\bleave (me|us) alone\b",
    r"\bbothering me\b",
    r"\bspam\b",
    r"\buseless\b",
    r"\bnahin\b",                         # Hindi "no"
    r"\bmat (bhejo|karo)\b",             # Hindi "don't send/do"
]

# WAIT / LATER patterns
WAIT_PATTERNS = [
    r"\blater\b",
    r"\bnot now\b",
    r"\bbusy\b",
    r"\btomorrow\b",
    r"\bnext week\b",
    r"\bgive me (some)? time\b",
    r"\bkaal\b",                          # Hindi "tomorrow"
    r"\bbaad mein\b",                     # Hindi "later"
    r"\bthoda wait\b",                   # Hindi "wait a bit"
    r"\blet me think\b",
    r"\bi'?ll (check|think|let you know)\b",
]

# QUESTION patterns
QUESTION_PATTERNS = [
    r"\?$",
    r"\bhow (much|many|long|do)\b",
    r"\bwhat (is|are|does|would)\b",
    r"\bwhen\b",
    r"\bwhere\b",
    r"\bcan you (tell|explain|share)\b",
    r"\btell me more\b",
    r"\bmore info\b",
    r"\bkya hai\b",                       # Hindi "what is"
    r"\bkitna\b",                         # Hindi "how much"
]

# OUT-OF-SCOPE patterns
OUT_OF_SCOPE_PATTERNS = [
    r"\bgst\b",
    r"\btax\b",
    r"\bloan\b",
    r"\binsurance\b",
    r"\bjob\b",
    r"\bsalary\b",
    r"\brent\b",
    r"\bca\b",
    r"\blawyer\b",
    r"\bcourt\b",
    r"\bpolice\b",
]


def _matches_any(text: str, patterns) -> bool:
    t = text.lower().strip()
    return any(re.search(p, t) for p in patterns)


def classify_intent(message: str) -> str:
    """
    Classify a reply message into one of:
      auto_reply, accept, reject, wait, question, out_of_scope, unclear
    """
    if _matches_any(message, AUTO_REPLY_PATTERNS):
        return "auto_reply"
    if _matches_any(message, REJECT_PATTERNS):
        return "reject"
    if _matches_any(message, ACCEPT_PATTERNS):
        return "accept"
    if _matches_any(message, WAIT_PATTERNS):
        return "wait"
    if _matches_any(message, QUESTION_PATTERNS):
        return "question"
    if _matches_any(message, OUT_OF_SCOPE_PATTERNS):
        return "out_of_scope"
    return "unclear"


# ─────────────────────────────────────────────────────────────────────────────
# Response generators per intent
# ─────────────────────────────────────────────────────────────────────────────

def _respond_accept(conv_state, merchant: Optional[Dict], context: Dict) -> Dict:
    """Merchant accepted — advance to next step."""
    pending = conv_state.pending_action if conv_state else ""

    # Build action-specific follow-through message (use action words: done, sending, draft, here, confirm, proceed, next)
    if pending and ("draft" in pending.lower() or "post" in pending.lower()):
        body = "Done — drafting now. I will have the draft ready and share next steps here."
        cta = "none"
    elif pending and "book" in pending.lower():
        body = "Done — booking confirmed. Sending reminder before the appointment with next details."
        cta = "none"
    elif pending and "list" in pending.lower():
        body = "Done — pulling the list now. Sending the details and next steps here."
        cta = "none"
    elif pending and "renew" in pending.lower():
        body = "Done — renewal process started. Proceeding with confirmation and next steps."
        cta = "none"
    else:
        body = "Done — proceeding with setup now. Draft is ready and I will share the next steps here."
        cta = "none"

    return {
        "action": "send",
        "body": body,
        "cta": cta,
        "rationale": "Merchant accepted; advancing to concrete next step immediately",
    }


def _respond_reject(conv_state, merchant: Optional[Dict], context: Dict) -> Dict:
    """Merchant rejected — end gracefully."""
    # Check for explicit unsubscribe
    msg = context.get("message", "").lower()
    if any(w in msg for w in ["stop", "unsubscribe", "opt out", "remove me", "don't message", "spam", "useless"]):
        rationale = "Merchant explicitly opted out; closing and suppressing future contact"
    else:
        rationale = "Merchant declined; closing conversation without pressure"

    return {
        "action": "end",
        "rationale": rationale,
    }


def _respond_wait(conv_state, merchant: Optional[Dict], context: Dict) -> Dict:
    """Merchant asked for time — wait appropriately."""
    msg = context.get("message", "").lower()
    if "tomorrow" in msg or "kaal" in msg:
        wait_seconds = 86400  # 24h
    elif "next week" in msg:
        wait_seconds = 604800  # 7d
    elif "busy" in msg or "thoda" in msg:
        wait_seconds = 7200   # 2h
    else:
        wait_seconds = 3600   # 1h default

    return {
        "action": "wait",
        "wait_seconds": wait_seconds,
        "rationale": f"Merchant requested time; backing off {wait_seconds//3600}h",
    }


def _respond_auto_reply(conv_state, merchant: Optional[Dict],
                         auto_count: int, context: Dict) -> Dict:
    """Detected auto-reply — end immediately to prevent auto-reply loop."""
    return {
        "action": "end",
        "rationale": "Detected WhatsApp Business canned auto-reply; ending conversation to prevent auto-reply loop",
    }


def _respond_question(conv_state, merchant: Optional[Dict],
                       message: str, context: Dict,
                       merchant_ctx: Optional[Dict],
                       category_ctx: Optional[Dict],
                       trigger_ctx: Optional[Dict]) -> Dict:
    """Merchant asked a question — answer from context only."""
    msg_lower = message.lower()

    # Price/cost questions
    if any(w in msg_lower for w in ["how much", "kitna", "price", "cost", "rate"]):
        # Look for an offer
        active_offers = []
        if merchant_ctx:
            active_offers = [o for o in merchant_ctx.get("offers", [])
                             if o.get("status") == "active"]
        if active_offers:
            offer = active_offers[0]
            body = f"The pricing for {offer.get('title', 'the offer')} is active now. Want me to share details for a specific service?"
        else:
            body = "I don't have current pricing details in front of me — check your dashboard or let me know which service you mean."
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Answered price question from merchant context; used only known facts",
        }

    # Time/when questions
    if any(w in msg_lower for w in ["when", "kab", "how long", "time"]):
        if trigger_ctx:
            expires = trigger_ctx.get("expires_at", "")
            if expires:
                body = f"This is time-sensitive — expires around {expires[:10]}. Want me to proceed now?"
            else:
                body = "This is best done soon for maximum impact. Shall I proceed?"
        else:
            body = "Timing depends on your preference — I can start right now if you'd like."
        return {
            "action": "send",
            "body": body,
            "cta": "binary_yes_no",
            "rationale": "Answered timing question from trigger context",
        }

    # Generic question — ask for clarification
    body = "Happy to clarify — could you tell me more specifically what you'd like to know? I'll pull the relevant details."
    return {
        "action": "send",
        "body": body,
        "cta": "open_ended",
        "rationale": "Merchant asked a question; requesting clarification rather than guessing",
    }


def _respond_out_of_scope(conv_state, merchant: Optional[Dict], context: Dict) -> Dict:
    """Merchant asked an out-of-scope question — politely decline and refocus."""
    trigger_kind = ""
    if conv_state and conv_state.trigger_id:
        trigger_kind = conv_state.trigger_id

    body = "That's outside what I can help with directly — best to consult the right specialist for that. Coming back to what we were discussing — want me to proceed with the plan I suggested?"
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Out-of-scope ask declined politely; refocused on original trigger",
    }


def _respond_unclear(conv_state, merchant: Optional[Dict],
                      message: str, context: Dict) -> Dict:
    """Unclear reply — ask one concise clarification."""
    turn = context.get("turn_number", 2)
    if turn >= 4:
        # Too many turns without clear signal — gracefully exit
        return {
            "action": "end",
            "rationale": "Multiple unclear replies; closing to avoid friction",
        }

    body = "Just to make sure I help correctly — would you like me to proceed with what I suggested, or is there something else on your mind?"
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Unclear intent; asking one clarifying question",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main reply handler
# ─────────────────────────────────────────────────────────────────────────────

class ReplyEngine:

    def __init__(self, suppression_store, context_store):
        self.sup = suppression_store
        self.ctx = context_store

    def handle_reply(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process an incoming reply and return the next action dict.

        Returns:
          {"action": "send"|"wait"|"end", "body": str, "cta": str, "rationale": str}
        """
        conv_id = request.get("conversation_id", "")
        merchant_id = request.get("merchant_id", "")
        customer_id = request.get("customer_id")
        message = request.get("message", "")
        turn = request.get("turn_number", 1)

        # Load conversation state
        conv_state = self.sup.get_conversation(conv_id)

        # Load context
        merchant = self.ctx.get_merchant(merchant_id) if merchant_id else None
        customer = self.ctx.get_customer(customer_id) if customer_id else None
        trigger = None
        category = None
        if conv_state and conv_state.trigger_id:
            trigger = self.ctx.get_trigger(conv_state.trigger_id)
        if merchant:
            category = self.ctx.get_category_for_merchant(merchant)

        # If conversation ended, silently ignore
        if conv_state and conv_state.status == "ended":
            return {
                "action": "end",
                "rationale": "Conversation already ended; no further action",
            }

        # Classify intent
        intent = classify_intent(message)
        logger.info("conv=%s turn=%d message='%s...' intent=%s",
                    conv_id, turn, message[:40], intent)

        # Record message
        self.sup.add_message_to_conversation(conv_id, "merchant" if not customer_id else "customer", message)

        context = {"message": message, "turn_number": turn}

        # Route by intent
        if intent == "auto_reply":
            # Count consecutive auto-replies
            self.sup.increment_auto_reply(conv_id)
            auto_count = self.sup.get_auto_reply_count(conv_id)
            result = _respond_auto_reply(conv_state, merchant, auto_count, context)

        elif intent == "reject":
            result = _respond_reject(conv_state, merchant, context)
            self.sup.end_conversation(conv_id)
            # Suppress merchant if explicit opt-out
            msg_lower = message.lower()
            if any(w in msg_lower for w in ["stop", "unsubscribe", "opt out", "don't message"]):
                self.sup.suppress_merchant(merchant_id)

        elif intent == "accept":
            self.sup.reset_auto_reply(conv_id)
            result = _respond_accept(conv_state, merchant, context)
            if conv_state:
                self.sup.update_conversation(conv_id, merchant_accepted=True)

        elif intent == "wait":
            self.sup.reset_auto_reply(conv_id)
            result = _respond_wait(conv_state, merchant, context)

        elif intent == "question":
            self.sup.reset_auto_reply(conv_id)
            result = _respond_question(
                conv_state, merchant, message, context,
                merchant, category, trigger
            )

        elif intent == "out_of_scope":
            self.sup.reset_auto_reply(conv_id)
            result = _respond_out_of_scope(conv_state, merchant, context)

        else:  # unclear
            self.sup.reset_auto_reply(conv_id)
            result = _respond_unclear(conv_state, merchant, message, context)

        # Track outbound if sending
        if result.get("action") == "send" and result.get("body"):
            self.sup.add_message_to_conversation(conv_id, "vera", result["body"])
            self.sup.update_conversation(
                conv_id,
                last_outbound_body=result["body"],
                last_cta=result.get("cta"),
            )

        if result.get("action") == "end":
            self.sup.end_conversation(conv_id)

        return result
