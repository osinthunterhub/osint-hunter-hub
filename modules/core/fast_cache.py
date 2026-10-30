"""
Ultra-Fast Cache + Session for OSINT Hunter Hub
- Connection pooling
- In-memory TTL cache
- Performance metrics
"""
import time
import threading
import requests
from typing import Any, Dict, Optional

# Global session with connection pooling - reuse for speed
_session = None
_session_lock = threading.Lock()

def get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                s = requests.Session()
                # Connection pooling: 20 connections, keep-alive
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=20,
                    max_retries=2,
                    pool_block=False
                )
                s.mount('http://', adapter)
                s.mount('https://', adapter)
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
        return {
            "hits": _cache_hits,
            "miss": _cache_miss,
            "hit_rate": f"{hit_rate:.1f}%",
            "size": len(_cache)
        }

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

# API response cache - 5 min for same queries
def api_cache_key(tool: str, term: str) -> str:
    return f"{tool}:{term.lower().strip()}"

def api_cache_get(tool: str, term: str) -> Optional[Dict]:
    return cache_get(api_cache_key(tool, term))

def api_cache_set(tool: str, term: str, data: Dict, ttl: int = 300):
    cache_set(api_cache_key(tool, term), data, ttl)
