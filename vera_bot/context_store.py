"""
context_store.py — Thread-safe in-memory context store with versioning.

Stores category, merchant, customer, and trigger contexts.
All reads/writes are O(1) by context_id.
Enforces: accept only if incoming version > stored version.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


class ContextStore:
    """
    Central in-memory store for all four context scopes.

    Versioning rules:
      version_in > stored  → accept, replace atomically
      version_in == stored → idempotent (treat as no-op, return stale)
      version_in < stored  → reject (stale)
    """

    VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

    def __init__(self):
        self._lock = threading.RLock()
        # (scope, context_id) → {"version": int, "payload": dict, "stored_at": str}
        self._data: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # ─────────────────────────────────────────
    # Write
    # ─────────────────────────────────────────

    def push(self, scope: str, context_id: str, version: int,
             payload: Dict[str, Any]) -> Tuple[bool, str, Optional[int]]:
        """
        Store a context. Returns (accepted, reason, current_version).

        accepted=True  → stored successfully
        accepted=False → stale_version (current >= incoming) or invalid_scope
        """
        if scope not in self.VALID_SCOPES:
            return False, "invalid_scope", None

        key = (scope, context_id)
        stored_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            existing = self._data.get(key)
            if existing is not None:
                cur_v = existing["version"]
                if version <= cur_v:
                    return False, "stale_version", cur_v
            self._data[key] = {
                "version": version,
                "payload": payload,
                "stored_at": stored_at,
            }
            return True, "ok", version

    # ─────────────────────────────────────────
    # Read
    # ─────────────────────────────────────────

    def get(self, scope: str, context_id: str) -> Optional[Dict[str, Any]]:
        """Return payload dict or None."""
        key = (scope, context_id)
        with self._lock:
            entry = self._data.get(key)
            return entry["payload"] if entry else None

    def get_version(self, scope: str, context_id: str) -> Optional[int]:
        key = (scope, context_id)
        with self._lock:
            entry = self._data.get(key)
            return entry["version"] if entry else None

    def get_entry(self, scope: str, context_id: str) -> Optional[Dict[str, Any]]:
        """Return full entry (version + payload + stored_at) or None."""
        key = (scope, context_id)
        with self._lock:
            return self._data.get(key)

    # ─────────────────────────────────────────
    # Bulk reads
    # ─────────────────────────────────────────

    def get_all(self, scope: str) -> Dict[str, Dict[str, Any]]:
        """Return {context_id: payload} for all stored entries of given scope."""
        with self._lock:
            return {
                cid: entry["payload"]
                for (s, cid), entry in self._data.items()
                if s == scope
            }

    def count(self, scope: str) -> int:
        with self._lock:
            return sum(1 for (s, _) in self._data if s == scope)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            result = {s: 0 for s in self.VALID_SCOPES}
            for (s, _) in self._data:
                result[s] = result.get(s, 0) + 1
            return result

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    def get_merchant(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        return self.get("merchant", merchant_id)

    def get_category_for_merchant(self, merchant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        slug = merchant.get("category_slug") or merchant.get("slug")
        if not slug:
            return None
        return self.get("category", slug)

    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        return self.get("customer", customer_id)

    def get_trigger(self, trigger_id: str) -> Optional[Dict[str, Any]]:
        return self.get("trigger", trigger_id)

    def clear(self):
        with self._lock:
            self._data.clear()
