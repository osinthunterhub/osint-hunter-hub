# -*- coding: utf-8 -*-
"""
telemetry.py — Per-tool observability (v53.0)
=============================================
Problem jo v53.0 audit me mili:
  • **58** jagah `except Exception: pass` — error poori tarah nigal jata tha.
  • **159** bare `except Exception:` — admin ko pata hi nahi chalta tha ki kaunsa
    tool kitni baar fail hua aur kyun.
  • Do tool (🎮 BGMI, 🔥 FF) upstream dead hone ke bawajood menu me "kaam karte"
    dikhte the — kisi ko pata nahi chala kyunki **koi counter hi nahi tha**.

Ab:
  • Har tool ke liye live counters: calls / ok / fail / soft-fail / avg latency /
    max latency / p95 / last error / credits charged.
  • **Upstream health** — kaunsi third-party API zinda hai, kaunsi murda.
    (BGMI jaisa case ab `/sys` par turant dikhega, chup nahi rahega.)
  • `soft_fail` = service ki galti (upstream dead / rate-limit), user ki nahi.
    **Inme credit NAHI katna chahiye** — ye flag isi ko mark karta hai.
  • `@tracked("toolname")` decorator — engine functions par lagao, khud count hoga.
  • Bounded + lock-free reads (snapshot copy deta hai) → Render 512MB par safe.

Koi nayi third-party dependency nahi.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("ud.telemetry")

__all__ = [
    "track", "tracked", "note", "note_soft_fail", "note_upstream",
    "tool_stats", "all_stats", "health_card", "snapshot", "reset",
    "upstream_status", "worst_tools", "SOFT_FAIL_KEYS",
]

_MAX_TOOLS = int(os.environ.get("TELEMETRY_MAX_TOOLS", "120"))
_LATENCY_WINDOW = int(os.environ.get("TELEMETRY_LATENCY_WINDOW", "60"))
_MAX_ERRORS = int(os.environ.get("TELEMETRY_MAX_ERRORS", "4"))
_STARTED = time.time()

# Result dict me ye keys True/`service_busy` hon to "service ki galti" maano
# (user ki nahi) → credit nahi katna chahiye.
SOFT_FAIL_KEYS = ("service_busy", "unavailable", "upstream_dead", "ratelimited")


class _ToolStat:
    """Ek tool ke live counters."""

    __slots__ = ("name", "calls", "ok", "fail", "soft_fail", "credits",
                 "cache_hits", "lat", "errors", "last_ok_at", "last_fail_at",
                 "last_error", "total_ms", "max_ms", "first_seen")

    def __init__(self, name: str):
        self.name = name
        self.calls = 0
        self.ok = 0
        self.fail = 0
        self.soft_fail = 0
        self.credits = 0
        self.cache_hits = 0
        self.lat: deque = deque(maxlen=_LATENCY_WINDOW)
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.errors: deque = deque(maxlen=_MAX_ERRORS)
        self.last_ok_at: Optional[float] = None
        self.last_fail_at: Optional[float] = None
        self.last_error: str = ""
        self.first_seen = time.time()

    def record(self, ok: bool, ms: float, soft: bool = False,
               error: str = "", credit: bool = False, cache_hit: bool = False):
        self.calls += 1
        if ms and ms > 0:
            self.lat.append(ms)
            self.total_ms += ms
            if ms > self.max_ms:
                self.max_ms = ms
        if cache_hit:
            self.cache_hits += 1
        if ok:
            self.ok += 1
            self.last_ok_at = time.time()
        else:
            self.fail += 1
            self.last_fail_at = time.time()
            if soft:
                self.soft_fail += 1
            if error:
                self.last_error = error[:160]
                self.errors.append(error[:160])
        if credit:
            self.credits += 1

    def p95(self) -> float:
        if not self.lat:
            return 0.0
        xs = sorted(self.lat)
        i = min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))
        return round(xs[i], 1)

    def avg_ms(self) -> float:
        return round(self.total_ms / self.calls, 1) if self.calls else 0.0

    def success_rate(self) -> float:
        return round(self.ok / self.calls * 100, 1) if self.calls else 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "calls": self.calls,
            "ok": self.ok,
            "fail": self.fail,
            "soft_fail": self.soft_fail,
            "hard_fail": self.fail - self.soft_fail,
            "credits": self.credits,
            "cache_hits": self.cache_hits,
            "success_rate": self.success_rate(),
            "avg_ms": self.avg_ms(),
            "p95_ms": self.p95(),
            "max_ms": round(self.max_ms, 1),
            "last_error": self.last_error,
            "recent_errors": list(self.errors),
            "last_ok_at": self.last_ok_at,
            "last_fail_at": self.last_fail_at,
        }


class _Upstream:
    """Ek third-party API/service ka health record."""

    __slots__ = ("name", "ok", "fail", "last_ok_at", "last_fail_at",
                 "last_error", "alive", "checked_at")

    def __init__(self, name: str):
        self.name = name
        self.ok = 0
        self.fail = 0
        self.last_ok_at: Optional[float] = None
        self.last_fail_at: Optional[float] = None
        self.last_error: str = ""
        self.alive: Optional[bool] = None
        self.checked_at: float = 0.0


_lock = threading.Lock()
_tools: Dict[str, _ToolStat] = {}
_upstreams: Dict[str, _Upstream] = {}


def _stat(name: str) -> _ToolStat:
    """Lock ke andar call karo."""
    st = _tools.get(name)
    if st is None:
        # bounded: bahut zyada tool names na ho jaayein
        if len(_tools) >= _MAX_TOOLS:
            oldest = min(_tools, key=lambda k: _tools[k].first_seen)
            _tools.pop(oldest, None)
        st = _ToolStat(name)
        _tools[name] = st
    return st


# ============================================================ core recording
def note(tool: str, ok: bool, ms: float = 0.0, *, soft: bool = False,
         error: str = "", credit: bool = False, cache_hit: bool = False) -> None:
    """Ek tool call ka result record karo. Kabhi exception nahi phenkta."""
    try:
        with _lock:
            _stat(str(tool or "unknown")).record(
                ok=bool(ok), ms=float(ms or 0.0), soft=bool(soft),
                error=str(error or ""), credit=bool(credit), cache_hit=bool(cache_hit))
    except Exception:                                               # noqa: BLE001
        pass


def note_soft_fail(tool: str, error: str = "") -> None:
    """Service-side failure (upstream dead / rate-limit) — credit nahi katna chahiye."""
    note(tool, False, soft=True, error=error)


def note_upstream(name: str, alive: bool, error: str = "") -> None:
    """Kisi third-party API ka health record karo (BGMI/FF/Pinterest jaise)."""
    try:
        with _lock:
            up = _upstreams.get(name)
            if up is None:
                up = _Upstream(name)
                _upstreams[name] = up
            up.checked_at = time.time()
            up.alive = bool(alive)
            if alive:
                up.ok += 1
                up.last_ok_at = time.time()
            else:
                up.fail += 1
                up.last_fail_at = time.time()
                if error:
                    up.last_error = str(error)[:160]
    except Exception:                                               # noqa: BLE001
        pass


def is_soft_fail(result: Any) -> bool:
    """Result dict me soft-fail flag hai? (credit charge rokne ke liye)

    Do tarah ke signals:
      • truthy flags   — `service_busy`, `unavailable`, `upstream_dead`, `ratelimited`
      • explicit False — `available: False` (BGMI availability gate)
        ⚠️ Ye alag se check hota hai kyunki `bool(False)` = False hai, to
        upar wala `any(bool(...))` ise kabhi pakad hi nahi sakta tha
        (v53.0 selftest me pakda gaya).
    """
    if not isinstance(result, dict):
        return False
    if result.get("ok"):
        return False
    if any(bool(result.get(k)) for k in SOFT_FAIL_KEYS):
        return True
    if "available" in result and result.get("available") is False:
        return True
    return False


# ============================================================ decorator
def tracked(tool: str, credit: bool = False):
    """Engine function par lagao — calls/ok/fail/latency khud record honge.

    Function dict return kare to `ok` key se success decide hoti hai, aur
    SOFT_FAIL_KEYS se soft-fail. Exception bhi count hoti hai (phir wapas
    phenk di jati hai — behavior nahi badalta).
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                res = fn(*args, **kwargs)
            except Exception as e:                                  # noqa: BLE001
                ms = (time.perf_counter() - t0) * 1000.0
                note(tool, False, ms, error=f"{type(e).__name__}: {str(e)[:100]}")
                raise
            ms = (time.perf_counter() - t0) * 1000.0
            if isinstance(res, dict):
                ok = bool(res.get("ok"))
                soft = is_soft_fail(res)
                err = "" if ok else str(res.get("error") or res.get("err") or "")[:120]
                note(tool, ok, ms, soft=soft, error=err)
            else:
                note(tool, res is not None, ms)
            return res
        return wrapper
    return deco


# ============================================================ read side
def tool_stats(tool: str) -> Dict[str, Any]:
    with _lock:
        st = _tools.get(tool)
        return st.snapshot() if st else {"name": tool, "calls": 0, "ok": 0,
                                         "fail": 0, "success_rate": 0.0}


def all_stats() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return {k: v.snapshot() for k, v in _tools.items()}


def upstream_status() -> Dict[str, Dict[str, Any]]:
    with _lock:
        out = {}
        for k, u in _upstreams.items():
            age = round(time.time() - u.checked_at) if u.checked_at else None
            out[k] = {"alive": u.alive, "ok": u.ok, "fail": u.fail,
                      "last_error": u.last_error, "age_sec": age}
        return out


def worst_tools(n: int = 5) -> List[Dict[str, Any]]:
    """Sabse zyada fail hone wale tools (admin ko sabse pehle yahi dikhna chahiye)."""
    with _lock:
        rows = [v.snapshot() for v in _tools.values() if v.calls > 0]
    rows.sort(key=lambda r: (-r["fail"], -(100 - r["success_rate"])))
    return rows[:max(1, n)]


def snapshot() -> Dict[str, Any]:
    """Poora telemetry snapshot (JSON-serialisable)."""
    with _lock:
        tools = {k: v.snapshot() for k, v in _tools.items()}
        ups = {}
        for k, u in _upstreams.items():
            ups[k] = {"alive": u.alive, "ok": u.ok, "fail": u.fail,
                      "last_error": u.last_error,
                      "age_sec": round(time.time() - u.checked_at) if u.checked_at else None}
        calls = sum(t["calls"] for t in tools.values())
        ok = sum(t["ok"] for t in tools.values())
        fail = sum(t["fail"] for t in tools.values())
        soft = sum(t["soft_fail"] for t in tools.values())
        credits = sum(t["credits"] for t in tools.values())
        dead_ups = [k for k, v in ups.items() if v["alive"] is False]
    return {
        "uptime_sec": int(time.time() - _STARTED),
        "tools_tracked": len(tools),
        "calls": calls, "ok": ok, "fail": fail,
        "soft_fail": soft, "hard_fail": fail - soft,
        "success_rate": round(ok / calls * 100, 1) if calls else 0.0,
        "credits_charged": credits,
        "upstreams": ups,
        "dead_upstreams": dead_ups,
        "tools": tools,
    }


def health_card(max_rows: int = 12) -> str:
    """Admin ke liye readable Hinglish health card (HTML).

    Dead upstream sabse upar — taaki BGMI/FF jaisa case chhup na sake.
    """
    s = snapshot()
    L: List[str] = []
    L.append("📡 <b>TOOL HEALTH (live telemetry)</b>")
    L.append("━━━━━━━━━━━━━━━━━━━━━━")
    L.append(f"• Tracked tools: <b>{s['tools_tracked']}</b>")
    L.append(f"• Total calls: <b>{s['calls']}</b> "
             f"(✅ {s['ok']} · ❌ {s['hard_fail']} · ⚠️ service-side {s['soft_fail']})")
    L.append(f"• Success rate: <b>{s['success_rate']}%</b>")
    L.append(f"• Credits charged: <b>{s['credits_charged']}</b>")

    if s["upstreams"]:
        L.append("")
        L.append("<b>🔌 Upstream APIs:</b>")
        for name, u in sorted(s["upstreams"].items(),
                              key=lambda kv: (kv[1]["alive"] is not False, kv[0])):
            if u["alive"] is True:
                mark = "🟢"
            elif u["alive"] is False:
                mark = "🔴 <b>DEAD</b>"
            else:
                mark = "⚪ unknown"
            age = f" ({u['age_sec']}s pehle)" if u.get("age_sec") is not None else ""
            line = f"  {mark} {name} — {u['ok']} ok / {u['fail']} fail{age}"
            if u["alive"] is False and u["last_error"]:
                line += f"\n      ↳ <code>{u['last_error'][:70]}</code>"
            L.append(line)

    if s["dead_upstreams"]:
        L.append("")
        L.append("⚠️ <b>Ye services abhi BAND hain</b> — inke tools par credit "
                 "nahi katna chahiye: " + ", ".join(s["dead_upstreams"]))

    worst = worst_tools(max_rows)
    if worst:
        L.append("")
        L.append("<b>🩺 Sabse zyada fail hone wale tools:</b>")
        for r in worst:
            if r["calls"] == 0:
                continue
            L.append(f"  • <b>{r['name']}</b> — {r['fail']}/{r['calls']} fail "
                     f"({r['success_rate']}% ok, avg {r['avg_ms']}ms, p95 {r['p95_ms']}ms)")
            if r["last_error"]:
                L.append(f"      ↳ <code>{r['last_error'][:76]}</code>")
    return "\n".join(L)


def reset() -> None:
    """Sab counters saaf karo (admin / test isolation)."""
    global _STARTED
    with _lock:
        _tools.clear()
        _upstreams.clear()
        _STARTED = time.time()


def track(tool: str, result: Any, ms: float = 0.0, credit: bool = False) -> bool:
    """Ek-line helper: result record karo aur `ok` return karo.

    Bot handlers me aise use hota hai:
        res = await asyncio.to_thread(engine, arg)
        ok = track("<tool_key>", res)
        if ok:
            await ...spend_credit_msg(uid, "<tool_key>")
    Soft-fail (service ki galti) par `ok=False` aata hai, aur `is_soft_fail(res)`
    se pata chalta hai ki credit nahi katna chahiye.
    """
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        note(tool, ok, ms, soft=is_soft_fail(result),
             error="" if ok else str(result.get("error") or "")[:120],
             credit=bool(credit and ok))
        return ok
    ok = result is not None and result is not False
    note(tool, ok, ms, credit=bool(credit and ok))
    return ok
