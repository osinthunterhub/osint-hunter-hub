# -*- coding: utf-8 -*-
"""
limiter.py — Per-user rate limiting (abuse / API-burn protection)
================================================================
Problem pehle:
  Bot par KOI rate limit nahi tha. Ek banda /start spam karke ya IFSC tool ko
  500 baar chala ke:
    • aapki API key ki daily quota kha jata tha
    • upstream (razorpay / postalpincode / ip-api) aapka IP block kar deta tha
    • Render free plan ka CPU limit hit ho jata → bot sab ke liye slow

Ab:
  Har (user, action) ke liye alag sliding-window counter.
  Limit cross hone par `check_limit()` ek saaf Hinglish message deta hai —
  bot crash nahi hota, user ko pata chalta hai kitni der rukna hai.

Design:
  • In-memory + thread-safe (bot single-process hai, isliye DB ki zaroorat nahi).
  • Auto-cleanup: purane buckets background me khud hat jaate hain (memory leak nahi).
  • Admin / VIP ko bypass milta hai (bot.py `bypass=True` bhejta hai).
  • Env se configure ho sakta hai, warna sensible defaults.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple

__all__ = ["RateLimiter", "limiter", "check_limit", "reset_limit", "limiter_stats"]

# ---------------- defaults (env se override) ----------------
# v78: pehle ye raw int() the. Render par ek bhi khaali/galat value
# (RATE_LIMIT_WINDOW=) = ImportError = bot ek baar bhi start nahi hota tha,
# aur crash "mystery" lagta tha. Ab safeconf se: kachra -> default + WARN.
from .safeconf import env_int as _env_int

_DEFAULT_LIMIT = _env_int("RATE_LIMIT_DEFAULT", 20, 1, 5000)      # per window
_DEFAULT_WINDOW = _env_int("RATE_LIMIT_WINDOW", 60, 5, 86400)     # seconds
_BURST_LIMIT = _env_int("RATE_LIMIT_BURST", 6, 1, 500)            # heavy tools
_CLEANUP_EVERY = 300                                                  # seconds


def _env_limit(action: str) -> Tuple[int, int]:
    """Per-action limit: RATE_LIMIT_<ACTION> = 'limit:window'"""
    raw = (os.environ.get(f"RATE_LIMIT_{action.upper()}") or "").strip()
    if raw and ":" in raw:
        try:
            a, b = raw.split(":", 1)
            return max(1, int(a)), max(1, int(b))
        except ValueError:
            pass
    return _DEFAULT_LIMIT, _DEFAULT_WINDOW


class RateLimiter:
    """Sliding-window rate limiter, keyed by (uid, action)."""

    def __init__(self, default_limit: int = _DEFAULT_LIMIT,
                 default_window: int = _DEFAULT_WINDOW,
                 max_keys: int = 20000):
        self._buckets: Dict[Tuple[int, str], deque] = {}
        self._lock = threading.Lock()
        self.default_limit = default_limit
        self.default_window = default_window
        self.max_keys = max_keys
        self._last_cleanup = time.time()
        self.blocked = 0
        self.allowed = 0

    # ------------------------------------------------------------- public
    def allow(self, uid: int, action: str = "any", *,
              limit: Optional[int] = None, window: Optional[int] = None,
              bypass: bool = False) -> Tuple[bool, float, int, int]:
        """Returns (allowed, retry_after_seconds, limit, window).

        bypass=True → hamesha allow (admin/VIP), par counter phir bhi update hota hai
        taaki stats sahi rahein.
        """
        if limit is None or window is None:
            el, ew = _env_limit(action)
            limit = limit if limit is not None else el
            window = window if window is not None else ew
        limit = max(1, int(limit))
        window = max(1, int(window))

        now = time.time()
        key = (int(uid), action)
        with self._lock:
            self._maybe_cleanup(now)
            dq = self._buckets.get(key)
            if dq is None:
                dq = deque()
                self._buckets[key] = dq
            # window se bahar wale timestamps nikalo
            cutoff = now - window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                self.blocked += 1
                retry = max(0.0, window - (now - dq[0]))
                return (True if bypass else False), retry, limit, window
            dq.append(now)
            self.allowed += 1
            return True, 0.0, limit, window

    def peek(self, uid: int, action: str = "any",
             limit: Optional[int] = None, window: Optional[int] = None) -> Tuple[int, int]:
        """Bina consume kiye batao: (used, limit)."""
        if limit is None or window is None:
            el, ew = _env_limit(action)
            limit = limit if limit is not None else el
            window = window if window is not None else ew
        now = time.time()
        with self._lock:
            dq = self._buckets.get((int(uid), action))
            if not dq:
                return 0, int(limit)
            cutoff = now - int(window)
            used = sum(1 for t in dq if t >= cutoff)
            return used, int(limit)

    def reset(self, uid: int, action: Optional[str] = None) -> None:
        """Kisi user ka counter saaf karo (admin /ban hataane ke baad useful)."""
        with self._lock:
            if action is None:
                for k in [k for k in self._buckets if k[0] == int(uid)]:
                    self._buckets.pop(k, None)
            else:
                self._buckets.pop((int(uid), action), None)

    def clear(self) -> None:
        """Sab buckets + counters saaf karo (admin reset / test isolation)."""
        with self._lock:
            self._buckets.clear()
            self.blocked = 0
            self.allowed = 0

    # ---------------------------------------------------------- internals
    def _maybe_cleanup(self, now: float) -> None:
        """Purane buckets hatao — lock ke andar hi call hota hai."""
        if now - self._last_cleanup < _CLEANUP_EVERY:
            return
        self._last_cleanup = now
        cutoff = now - max(self.default_window, 900)
        dead = [k for k, dq in self._buckets.items() if not dq or dq[-1] < cutoff]
        for k in dead:
            self._buckets.pop(k, None)
        # safety: agar phir bhi bahut zyada keys hain, sabse purani hatao
        if len(self._buckets) > self.max_keys:
            for k in sorted(self._buckets, key=lambda x: (self._buckets[x][-1]
                                                          if self._buckets[x] else 0))[:self.max_keys // 4]:
                self._buckets.pop(k, None)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active_buckets": len(self._buckets),
                "allowed": self.allowed,
                "blocked": self.blocked,
                "default_limit": self.default_limit,
                "default_window": self.default_window,
            }


# ---------------------------------------------------------------- singleton
limiter = RateLimiter()


def _fmt_wait(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 60:
        m = s // 60
        return f"{m} minute" + ("s" if m > 1 else "")
    return f"{max(s, 1)} second" + ("s" if s > 1 else "")


def check_limit(uid: int, action: str = "any", *, heavy: bool = False,
                bypass: bool = False, tool_name: str = "",
                limit: Optional[int] = None, window: Optional[int] = None) -> Optional[str]:
    # v78: `int('')`/`int(None)` se ye function khud crash ho jata tha — aur ye
    # HAR tool se pehle chalta hai, yani ek bad value = poora tool fail. Ab
    # uid/action safe karke limit check hamesha chalta hai.
    try:
        uid = int(uid)
    except Exception:                                    # noqa: BLE001
        uid = 0
    action = str(action if isinstance(action, str) else (action or "any"))
    """Rate-limit check karke message do.

    Args:
      heavy=True  → chhota burst budget (CPU-mehnge tools ke liye)
      bypass=True → admin/VIP: counter update hota hai par block nahi hota
      limit/window → explicit override (bot.py ka TOOL_RATE_LIMITS yahi bhejta hai)

    Returns:
      None  → allowed, aage badho
      str   → block message (user ko bhej do) aur ruk jao
    """
    if limit is None or window is None:
        _l = _BURST_LIMIT if heavy else None
        _w = 60 if heavy else None
        limit = limit if limit is not None else _l
        window = window if window is not None else _w
    allowed, retry, lim, win = limiter.allow(uid, action, limit=limit, window=window, bypass=bypass)
    if allowed:
        return None
    label = tool_name or action.upper()
    return (
        f"⏳ <b>{label} thoda slow karo bhai!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Aapne {lim} baar / {win} second me limit poori kar di.\n"
        f"🕐 <b>{_fmt_wait(retry)}</b> baad dobara try karo.\n\n"
        "💡 <i>Ye limit isliye hai taaki bot sabke liye fast rahe "
        "aur API block na ho.</i>\n"
        "👑 <i>VIP par ye limit nahi hoti — /premium dekho.</i>"
    )


def reset_limit(uid: int, action: Optional[str] = None) -> None:
    limiter.reset(uid, action)


def limiter_stats() -> dict:
    return limiter.snapshot()
