"""
Ultra-Fast Cache + Session for OSINT Hunter Hub
- Connection pooling
- In-memory TTL cache
- Performance metrics
"""
import os
import time
import threading
import functools
import fasthttp
from fasthttp import FastSession
from typing import Any, Dict, Optional

# Global session with connection pooling - reuse for speed
_session = None
_session_lock = threading.Lock()

def get_session():
    """v2.24.2 FIX: FastSession (fasthttp shim) — pooling+retry+timeout built-in.
    Pehle requests.Session() attr-gone crash karta tha (import-time AttributeError
    → NUMBER INFO module poora load hi nahi hota tha)."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = FastSession(default_timeout=25.0)
                s.headers.update({
                    'User-Agent': 'OSINT-Hunter-Hub/2.0 Fast',
                    'Accept': 'application/json',
                    'Connection': 'keep-alive'
                })
                _session = s
    return _session

# In-memory TTL cache
_cache: Dict[str, tuple] = {}  # key -> (value, expiry)
_cache_lock = threading.Lock()
_cache_hits = 0
_cache_miss = 0

def cache_get(key: str) -> Optional[Any]:
    global _cache_hits, _cache_miss
    with _cache_lock:
        if key in _cache:
            val, exp = _cache[key]
            if time.time() < exp:
                _cache_hits += 1
                return val
            else:
                del _cache[key]
        _cache_miss += 1
        return None

def cache_set(key: str, value: Any, ttl: int = 300):
    with _cache_lock:
        _cache[key] = (value, time.time() + ttl)
        # Cleanup old entries if too many
        if len(_cache) > 1000:
            now = time.time()
            expired = [k for k, (_, exp) in _cache.items() if exp < now]
            for k in expired[:100]:
                del _cache[k]

def cache_stats() -> Dict:
    with _cache_lock:
        total = _cache_hits + _cache_miss
        hit_rate = (_cache_hits / total * 100) if total > 0 else 0
        out = {
            "hits": _cache_hits,
            "miss": _cache_miss,
            "hit_rate": f"{hit_rate:.1f}%",
            "size": len(_cache)
        }
        try:
            from modules.database import cache_stats as _dcs
            out["persistent"] = _dcs()
        except Exception:
            out["persistent"] = {"total": 0, "live": 0, "hits": 0}
        return out

# Force join cache - 60 sec TTL per user (fast but secure)
_force_join_cache: Dict[int, tuple] = {}  # user_id -> (is_joined, expiry)
_force_join_lock = threading.Lock()

def force_join_cache_get(uid: int) -> Optional[bool]:
    with _force_join_lock:
        if uid in _force_join_cache:
            val, exp = _force_join_cache[uid]
            if time.time() < exp:
                return val
            else:
                del _force_join_cache[uid]
        return None

def force_join_cache_set(uid: int, is_joined: bool, ttl: int = 60):
    with _force_join_lock:
        _force_join_cache[uid] = (is_joined, time.time() + ttl)

def force_join_cache_clear(uid: int = None):
    with _force_join_lock:
        if uid is None:
            _force_join_cache.clear()
        else:
            _force_join_cache.pop(uid, None)

# v2.17 SPEED — gateway (utility-api) khud 30 din tak cache karta hai, isliye
# bot ko bhi lamba cache rakhna chahiye: repeat query = 0 network call.
# Pehle 300s (5 min) tha -> 5 min baad wapas gateway par jaata tha (waste).
BOT_CACHE_TTL = int(os.environ.get("BOT_CACHE_TTL", "43200"))      # 12 ghante
BOT_CACHE_TTL_NF = int(os.environ.get("BOT_CACHE_TTL_NF", "1800"))  # no-record 30 min

# API response cache - 5 min for same queries
def api_cache_key(tool: str, term: str) -> str:
    return f"{tool}:{term.lower().strip()}"

# v2.0 — persistent (DB) cache layer so restart/deploy ke baad bhi instant
_db_writes = 0

def _db_cache_get(dbkey: str):
    try:
        from modules.database import cache_get as _g
        return _g(dbkey)
    except Exception:
        return None

def api_cache_get(tool: str, term: str) -> Optional[Dict]:
    key = api_cache_key(tool, term)
    mem = cache_get(key)
    if mem is not None:
        return mem
    # DB (persistent) hit — 0 ms, koi network call nahi
    pc = _db_cache_get(f"osint:{key}")
    if isinstance(pc, dict) and pc:
        try:
            cache_set(key, pc, 300)
        except Exception:
            pass
        try:
            from modules.database import cache_hit_bump as _b
            _b(f"osint:{key}")
        except Exception:
            pass
        return pc
    return None

def api_cache_set(tool: str, term: str, data: Dict, ttl: int = 300, db_ttl: int = None):
    """mem TTL = ttl · DB TTL = db_ttl (None = default: ok 30 din, not_found 6 ghante)."""
    global _db_writes
    if not isinstance(data, dict):
        return
    # SAFETY (v2.1): sirf achha result cache karo — error kabhi cache na ho
    if data.get("ok") is False and not data.get("not_found"):
        return
    key = api_cache_key(tool, term)
    cache_set(key, data, ttl)
    try:
        from modules.database import cache_put as _p, cache_prune as _pr
        if isinstance(data, dict) and data.get("ok"):
            _p(f"osint:{key}", data, int(db_ttl) if db_ttl else 2592000)   # success
        elif isinstance(data, dict) and data.get("not_found"):
            _p(f"osint:{key}", data,
               min(int(db_ttl), 21600) if db_ttl else 21600)               # no-record
        _db_writes += 1
        if _db_writes % 50 == 0:
            _pr()
    except Exception:
        pass


# ======================================================================
#  v2.1 FAST-2 — ek line ka cache decorator (mem -> DB -> real call)
#  Decorated function ka pehla argument = term (url/number/query).
#  Repeat query = 0 ms instant; fail/HTTP error cache NAHI hota.
# ======================================================================
def with_cache(tool: str, mem_ttl: int = None, db_ttl: int = None):
    """@with_cache("num", 1800, 21600) — bas yahi ek line, poori speed."""
    _mem = mem_ttl if mem_ttl is not None else BOT_CACHE_TTL

    def _deco(fn):
        @functools.wraps(fn)
        def _wrap(term, *a, **kw):
            t = str(term or "").strip()
            if not t:
                return fn(term, *a, **kw)
            try:
                hit = api_cache_get(tool, t)
            except Exception:
                hit = None
            if isinstance(hit, dict) and hit:
                out = dict(hit)
                out["cached"] = True
                try:
                    out["ms"] = 0
                except Exception:
                    pass
                return out
            res = fn(term, *a, **kw)
            try:
                if isinstance(res, dict):
                    _ttl = _mem if (res.get("ok") or res.get("data")) else BOT_CACHE_TTL_NF
                    _db = db_ttl
                    # v2.18.1 — PARTIAL result (fallback layer) ko lamba cache nahi:
                    # 10 min baad provider dobara try ho, warna rich data 12h block rahega
                    if res.get("partial"):
                        _ttl = min(_ttl, 600)
                        _db = min(_db or 600, 600)
                    api_cache_set(tool, t, res, ttl=_ttl, db_ttl=_db)
            except Exception:
                pass
            return res
        return _wrap
    return _deco
