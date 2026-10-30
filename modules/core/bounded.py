# -*- coding: utf-8 -*-
"""
bounded.py — 🪣 BOUNDED CACHE + CACHE REGISTRY (v76 FORTRESS)
=============================================================
v76 me mila sabse bada "chhupa hua" crash kaaran:

    bot.py / modules me kai cache aise the jo BADHTE hi jaate hain,
    kabhi saaf nahi hote:

        modules/osint_hub.py       -> _CACHE      (kabhi evict nahi hota)
        modules/username_hunter.py -> _CACHE      (kabhi evict nahi hota)
        modules/vehicle_tool.py    -> _CACHE      (kabhi evict nahi hota)
        modules/business_tools.py  -> _FONT_CACHE (bounded, par registry me nahi)

    Har naya IMEI / username / gaadi-number / website ek nayi ENTRY banata hai.
    Din bhar me hazaaron entry -> hafton me lakhon.
    Render free plan = 512 MB RAM -> ek din OOM KILL -> "bot crash ho gaya".

ISA HAL:
    BoundedCache = TTL + max-size + LRU, thread-safe, aur ek GLOBAL REGISTRY
    me registered. Iska matlab:

      1. Cache kabhi bada nahi ho sakta (maxsize se aage purani entry nikal jaati hai)
      2. Purani (expire ho chuki) entry apne aap hat jaati hai
      3. Memory watchdog jab bhi bole, REGISTRY ke saare cache ek saath saaf
         ho jaate hain (pehle bot.py sirf 5 global ko jaanta tha)

    Pehli baar: saare module-cache ek jagah se control hote hain.

USE:
    from modules.core.bounded import BoundedCache

    _CACHE = BoundedCache("osint", maxsize=512, default_ttl=300)

    _CACHE.put(key, value)          # daalo
    v = _CACHE.get(key)             # lo (expire ho gaya to None)
    _CACHE.clear()                  # saaf

    from modules.core.bounded import clear_all_caches, cache_report
    clear_all_caches()              # memory pressure me (automatic)
    cache_report()                  # /sys me dikhta hai
----------------------------------------------------------------------
Ye file kabhi crash nahi karti — har jagah try/except hai.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

__all__ = [
    "BoundedCache",
    "clear_all_caches",
    "cache_report",
    "registry",
    "prune_all_caches",
]

# =====================================================================
#  GLOBAL REGISTRY — saare BoundedCache yahan register hote hain
# =====================================================================
_LOCK = threading.RLock()
_REGISTRY: "List[BoundedCache]" = []

# sirf itne cache registry me rakhein (kisi anokhi bug se bachao)
MAX_REGISTRY = 200


def registry() -> "List[BoundedCache]":
    """Saare registered cache ki copy (safe)."""
    with _LOCK:
        return list(_REGISTRY)


def clear_all_caches() -> int:
    """Saare cache khaali kar do — memory watchdog isi ko call karta hai.

    Returns: kitne cache saaf kiye.
    """
    n = 0
    for c in registry():
        try:
            c.clear()
            n += 1
        except Exception:                                        # noqa: BLE001
            continue
    return n


def prune_all_caches() -> int:
    """Sirf EXPIRE ho chuki entry nikaalo (cache ka faayda bhi rahe).

    Returns: kitni entry nikali.
    """
    n = 0
    for c in registry():
        try:
            n += c.prune()
        except Exception:                                        # noqa: BLE001
            continue
    return n


def cache_report() -> Dict[str, Any]:
    """/sys admin card ke liye: har cache ka size/limit/hit-rate."""
    out: Dict[str, Any] = {"caches": [], "total_entries": 0, "total_limit": 0,
                           "evictions": 0, "clears": 0}
    try:
        for c in registry():
            s = c.stats()
            out["caches"].append(s)
            out["total_entries"] += int(s.get("size", 0))
            out["total_limit"] += int(s.get("maxsize", 0))
            out["evictions"] += int(s.get("evictions", 0))
            out["clears"] += int(s.get("clears", 0))
        out["caches"].sort(key=lambda d: -int(d.get("size", 0)))
    except Exception:                                            # noqa: BLE001
        pass
    return out


# =====================================================================
#  BOUNDED CACHE
# =====================================================================
class BoundedCache:
    """Thread-safe cache jo KABHI unbounded nahi badhta.

    ● maxsize   — isse zyada entry hone par sabse purani (LRU) nikal jaati hai
    ● ttl       — entry itni second baad apne aap expire
    ● prune()   — expire entry abhi nikaalo (janitor har 10 min karta hai)
    ● clear()   — sab khaali (memory pressure me watchdog karta hai)
    """

    __slots__ = ("name", "maxsize", "default_ttl", "_d", "_lock",
                 "_hits", "_miss", "_evict", "_clears", "_pruned")

    def __init__(self, name: str, maxsize: int = 512, default_ttl: float = 300.0):
        self.name = str(name or "cache")
        try:
            # caller ki baat maano (3 bole to 3) — sirf 0 se bachao
            self.maxsize = max(1, int(maxsize))
        except Exception:                                        # noqa: BLE001
            self.maxsize = 512
        try:
            self.default_ttl = max(1.0, float(default_ttl))
        except Exception:                                        # noqa: BLE001
            self.default_ttl = 300.0

        self._d: Dict[Any, Any] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._miss = 0
        self._evict = 0
        self._clears = 0
        self._pruned = 0

        # registry me daalo (kabhi crash na ho — bina iske bhi cache chalega)
        try:
            with _LOCK:
                if len(_REGISTRY) < MAX_REGISTRY:
                    _REGISTRY.append(self)
        except Exception:                                        # noqa: BLE001
            pass

    # ------------------------------------------------------------- basic
    def get(self, key: Any, default: Any = None) -> Any:
        try:
            with self._lock:
                hit = self._d.get(key)
                if hit is None:
                    self._miss += 1
                    return default
                val, exp = hit
                if exp and time.time() > exp:
                    # expire — nikaal do, cache chhota rahe
                    self._d.pop(key, None)
                    self._miss += 1
                    return default
                self._hits += 1
                return val
        except Exception:                                        # noqa: BLE001
            return default

    def put(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        try:
            t = self.default_ttl if ttl is None else float(ttl)
            if t <= 0:
                return
            exp = time.time() + t
            with self._lock:
                self._d[key] = (value, exp)
                self._evict_if_needed()
        except Exception:                                        # noqa: BLE001
            pass

    # purane code ke saath compatibility (kuch jagah `_cache_set` jaisa)
    set = put

    def __contains__(self, key: Any) -> bool:
        try:
            return self.get(key, None) is not None
        except Exception:                                        # noqa: BLE001
            return False

    def __len__(self) -> int:
        try:
            with self._lock:
                return len(self._d)
        except Exception:                                        # noqa: BLE001
            return 0

    # ------------------------------------------------------------- housekeeping
    def _evict_if_needed(self) -> None:
        """maxsize se aage: sabse purani expiry wali entry nikaalo (LRU-ish)."""
        try:
            if len(self._d) <= self.maxsize:
                return
            extra = len(self._d) - self.maxsize
            # chhoti list me sorted() fast hai; yahan maxsize chhota hota hai
            oldest = sorted(self._d.items(), key=lambda kv: kv[1][1])[:extra]
            for k, _v in oldest:
                self._d.pop(k, None)
                self._evict += 1
        except Exception:                                        # noqa: BLE001
            pass

    def prune(self) -> int:
        """Abhi expire ho chuki saari entry nikaalo. Returns: kitni nikali."""
        n = 0
        try:
            now = time.time()
            with self._lock:
                dead = [k for k, v in self._d.items() if v[1] and now > v[1]]
                for k in dead:
                    self._d.pop(k, None)
                    n += 1
                self._pruned += n
        except Exception:                                        # noqa: BLE001
            pass
        return n

    def clear(self) -> int:
        """Poora cache khaali. Returns: kitni entry thi."""
        try:
            with self._lock:
                n = len(self._d)
                self._d.clear()
                self._clears += 1
                return n
        except Exception:                                        # noqa: BLE001
            return 0

    # ------------------------------------------------------------- stats
    def stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                size = len(self._d)
            total = self._hits + self._miss
            return {
                "name": self.name,
                "size": size,
                "maxsize": self.maxsize,
                "pct": round(100.0 * size / max(1, self.maxsize), 1),
                "ttl": int(self.default_ttl),
                "hits": self._hits,
                "miss": self._miss,
                "hit_rate": round(100.0 * self._hits / max(1, total), 1),
                "evictions": self._evict,
                "pruned": self._pruned,
                "clears": self._clears,
            }
        except Exception:                                        # noqa: BLE001
            return {"name": self.name, "size": 0, "maxsize": self.maxsize}
