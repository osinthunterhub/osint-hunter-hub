# -*- coding: utf-8 -*-
"""
modules.core — Utility Duniya Bot ka professional foundation layer (v50)
========================================================================
Ye package bot ke SAARE tools ko teen cheezein deta hai:

  1. `net`     — ek hi jagah se HTTP: timeout + retry + connection pool + SSRF guard
  2. `cache`   — TTL cache (same sawaal par baar-baar API call nahi = fast + API bachat)
  3. `limiter` — per-user rate limit (koi bhi bot ko spam karke API/IP nahi jala sakta)
  4. `telemetry` — per-tool live counters + upstream health (v53.0). Ab koi tool
     chup-chaap fail nahi ho sakta — `/sys` par dikhta hai kaunsa upstream DEAD hai.

Design rules:
  • Purane modules ko todna nahi hai — ye sab OPTIONAL helpers hain.
  • Har helper fail-safe hai: kuch bhi tut-ta hai to bot chalta rahega.
  • Koi naya third-party dependency nahi (Render par kuch install nahi karna padega).
"""
from .cache import TTLCache, cached_call, stats as cache_stats
from .limiter import RateLimiter, limiter, check_limit, reset_limit, limiter_stats
from . import telemetry
from .telemetry import (
    note as tel_note,
    note_soft_fail as tel_soft_fail,
    note_upstream as tel_upstream,
    tracked as tel_tracked,
    track as tel_track,
    is_soft_fail as tel_is_soft_fail,
    health_card as tel_health_card,
    snapshot as tel_snapshot,
    tool_stats as tel_tool_stats,
    all_stats as tel_all_stats,
    upstream_status as tel_upstream_status,
    worst_tools as tel_worst_tools,
)
from .net import (
    http_get,
    http_get_json,
    http_post,
    http_post_json,
    http_head,
    http_bytes,
    is_safe_url,
    NetError,
)

__all__ = [
    "telemetry",
    "tel_note", "tel_soft_fail", "tel_upstream", "tel_tracked", "tel_track",
    "tel_is_soft_fail", "tel_health_card", "tel_snapshot", "tel_tool_stats",
    "tel_all_stats", "tel_upstream_status", "tel_worst_tools",
    "TTLCache", "cached_call", "cache_stats",
    "RateLimiter", "limiter", "check_limit", "reset_limit", "limiter_stats",
    "http_get", "http_get_json", "http_post", "http_post_json",
    "http_head", "http_bytes", "is_safe_url", "NetError",
]
