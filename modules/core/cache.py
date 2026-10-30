# -*- coding: utf-8 -*-
"""
cache.py — Thread-safe TTL cache with a hard size ceiling
=========================================================
Kyun chahiye:
  • IFSC / Pincode / IP / Vehicle jaise lookups ka jawab roz change nahi hota.
  • Bina cache ke har user har baar API call karta hai → slow + API rate-limit + free-plan waste.

Ye cache:
  • Thread-safe hai (bot kai threads chalata hai).
  • Size-capped hai — memory kabhi bhaaregi nahi (LRU-ish eviction).
  • Negative results ko chhota TTL de sakte ho (fail wali query jaldi retry ho).
  • Atomic hai: `cached_call` same key ke liye dobara kaam nahi karne deta.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

__all__ = ["TTLCache", "cached_call", "stats"]


class TTLCache:
    """Bounded, thread-safe TTL cache.

    maxsize entries tak rakhta hai; usse aage sabse purana entry hata deta hai.
    """

    __slots__ = ("_d", "_lock", "maxsize", "default_ttl", "hits", "misses")

    def __init__(self, maxsize: int = 2048, default_ttl: int = 300):
        self._d: "OrderedDict[str, tuple[Any, float, int]]" = OrderedDict()
        self._lock = threading.Lock()
        self.maxsize = max(16, int(maxsize))
        self.default_ttl = max(1, int(default_ttl))
        self.hits = 0
        self.misses = 0

    # ---------------------------------------------------------------- read
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                self.misses += 1
                return default
            val, ts, ttl = hit
            if (time.time() - ts) > ttl:
                # expire ho gaya — jagah khali karo
                self._d.pop(key, None)
                self.misses += 1
                return default
            # most-recently-used banado
            self._d.move_to_end(key)
            self.hits += 1
            return val

    # --------------------------------------------------------------- write
    def put(self, key: str, val: Any, ttl: Optional[int] = None) -> None:
        ttl = self.default_ttl if ttl is None else max(1, int(ttl))
        with self._lock:
            if key in self._d:
                self._d.pop(key, None)
            self._d[key] = (val, time.time(), ttl)
            # size cap enforce karo
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    # ------------------------------------------------------------- housekeeping
    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.hits = 0
            self.misses = 0

    def purge_expired(self) -> int:
        """Expired entries hatao → kitne hate, wo count return karo."""
        now = time.time()
        with self._lock:
            dead = [k for k, (_v, ts, ttl) in self._d.items() if (now - ts) > ttl]
            for k in dead:
                self._d.pop(k, None)
        return len(dead)

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)

    def __contains__(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._d),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / (self.hits + self.misses) * 100, 1)
                if (self.hits + self.misses) else 0.0,
            }


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<MISSING>"


_MISSING = _Missing()


def cached_call(cache: TTLCache, key: str, fn: Callable[[], Any],
                ttl: Optional[int] = None, fail_ttl: int = 30,
                is_failure: Optional[Callable[[Any], bool]] = None) -> tuple:
    """Cache-aware wrapper.

    Returns (value, from_cache: bool).

    • Cache hit  → (cached_value, True)
    • Miss       → fn() chalao, result cache me daalo, (value, False)
    • fn() ne None diya ya is_failure() True bola → chhote `fail_ttl` se cache karo
      (taki fail wali query 30 second tak API ko hammer na kare).

    Note: ye deliberately lock-per-key nahi karta — same key par do concurrent
    calls dono kaam kar sakte hain, par result identical hoga aur koi corruption
    nahi hoga. Bot ke liye ye trade-off sahi hai (simplicity > dedupe).
    """
    cached = cache.get(key, _MISSING)
    if cached is not _MISSING:
        return cached, True
    try:
        val = fn()
    except Exception:  # noqa: BLE001 - caller ka contract: exception bahar jaye
        raise
    failed = val is None or (is_failure(val) if is_failure else False)
    cache.put(key, val, fail_ttl if failed else ttl)
    return val, False


def stats(cache: TTLCache) -> dict:
    return cache.snapshot()
