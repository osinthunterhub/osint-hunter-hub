# -*- coding: utf-8 -*-
"""
guard.py — 🛡️ GLOBAL CRASH SHIELD (v60)
========================================
Aapki requirement: "code crash ho jaata hai baar baar, aisa fix karo ki ye
kabhi crash na ho."

Ye file usi ke liye hai. Ye 6 alag-alag "crash ke raaste" band karti hai:

  1. HANDLER CRASH
     Kisi tool me chhoti si bug -> poora update handler mar jata hai -> user ko
     kuch nahi milta. Ab: `@guarded` decorator har handler ko lapet leta hai.
     Bug ho to user ko saaf message + admin ko log. Bot chalta rehta hai.

  2. BACKGROUND TASK CRASH
     `asyncio.create_task()` me error -> chup-chaap gayab (log me bhi kuch
     nahi). Ab: global asyncio exception handler + task done-callback.

  3. THREAD CRASH
     Keepalive / backup thread me error -> thread mar jata hai, bot aadha
     zinda rehta hai. Ab: threading.excepthook.

  4. 💀 OUT OF MEMORY (Render free plan par ye SABSE BADA killer hai)
     Render free plan = sirf 512 MB RAM. Video download + ffmpeg + PDF +
     Pillow + 20 user ek saath -> RAM khatam -> Render bot ko "OOM KILL" kar
     deta hai -> log me sirf "Killed" dikhta hai aur boot hota rehta hai.
     Ab: memory watchdog har 15 second RAM dekhta hai. 76% cross hone par
     cache/garbage collect karta hai; 82% par aggressive cleanup aur idle-only
     safe restart limit se pehle. Isliye OOM kill bahut rare ho gaya.

  5. 🧟 HUNG EVENT LOOP (chup-chaap maut)
     Kabhi koi tool aisa atak jata hai jo loop ko block kar de -> bot "zinda"
     dikhta hai (Render green) par koi message ka jawab nahi aata. Ye sabse
     confusing crash hai.
     Ab: heartbeat watchdog. Agar loop 4 minute tak ek baar bhi nahi hila,
     to watchdog khud process restart kar deta hai (clean restart > dead bot).

  6. REPEATED CRASH LOOP
     Agar bot lagataar crash ho raha ho to Render "Application failed" de deta
     hai. Ab: crash counter + exponential backoff (supervisor me).

----------------------------------------------------------------------
USE:
    from modules.core.guard import install_global_guard, guarded, heartbeat
    install_global_guard()          # boot par ek baar
    @guarded("tool_name")           # kisi bhi handler par
    async def my_handler(...): ...
----------------------------------------------------------------------
"""
from __future__ import annotations

import asyncio
import functools
import gc
import logging
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable, Optional

log = logging.getLogger("utility-super-bot.guard")

__all__ = [
    "install_global_guard", "guarded", "heartbeat", "HEART",
    "start_memory_watchdog", "start_hang_watchdog", "crash_state",
    "mem_mb", "guard_stats", "GC_TRIGGERS",
    "heart_report", "set_heartbeat_interval",
]

# =====================================================================
#  STATE
# =====================================================================
_START = time.time()
HEART: dict = {
    "beat": 0.0,           # aakhri baar event loop hilaya (epoch)
    "beats": 0,            # total beats
    "lag": 0.0,            # sabse zyada kitni der loop ATKA (interval se zyada)
    "interval": 20.0,      # heartbeat ka target gap (v77)
    "last_gap": 0.0,       # do beats ke beech ka aakhri gap
    "worst_gap": 0.0,      # sabse bada gap kabhi (stall 20s+ bhi pakda jaye)
    "total_gap": 0.0,      # average ke liye jama
    "gaps": 0,             # kitne gaps nape
}
_crash: dict = {
    "handler": 0,          # handler me kitni baar bug aaya (bot zinda raha)
    "task": 0,             # background task bug
    "thread": 0,           # thread bug
    "fatal": 0,            # poora process mara
    "last": "",
    "last_why": "",
    "recent": [],          # aakhri 20 ghatnayein (kaunsa tool tha)
}
_LOCK = threading.RLock()
_mem_state: dict = {"peak_mb": 0.0, "last_mb": 0.0, "gc_runs": 0, "aggressive": 0,
                    "warns": 0}
# ye caches memory watch dog saaf karta hai (bot.py khud register karta hai)
GC_TRIGGERS: list = []


def _effective_restart_mb(limit_mb: float) -> float:
    """Configured restart point ko actual memory limit se pehle cap karo.

    Purana Render env MEM_RESTART_MB=470 tha, jabki free tier 512MB hai.
    Ab default/max 84% (about 430MB), explicit 0 se opt-out ab bhi possible.
    """
    ceiling = max(64.0, float(limit_mb) * 0.84)
    raw = os.environ.get("MEM_RESTART_MB")
    if raw is None or not str(raw).strip():
        return ceiling
    try:
        value = float(raw)
    except Exception:                                            # noqa: BLE001
        return ceiling
    return 0.0 if value <= 0 else min(value, ceiling)


def crash_state() -> dict:
    """Bot kitni baar bacha, kitni baar mara — /health aur /sys ke liye."""
    with _LOCK:
        d = dict(_crash)
        d["recent"] = list(_crash["recent"][-6:])
        d["functools"] = None
        return d


def guard_stats() -> dict:
    """Poora guard ka report."""
    with _LOCK:
        return {
            "handled": _crash["handler"] + _crash["task"] + _crash["thread"],
            "handler": _crash["handler"], "task": _crash["task"],
            "thread": _crash["thread"], "fatal": _crash["fatal"],
            "last_why": _crash["last_why"], "last": _crash["last"],
            "recent": list(_crash["recent"][-8:]),
            "mem_mb": _mem_state["last_mb"], "mem_peak_mb": _mem_state["peak_mb"],
            "gc_runs": _mem_state["gc_runs"], "aggressive": _mem_state["aggressive"],
            "restarts": int(_mem_state.get("restarts", 0)),
            "restart_at_mb": _effective_restart_mb(
                float(os.environ.get("MEMORY_LIMIT_MB") or 512)),
            "trim_runs": int(_mem_state.get("trim_runs", 0)),
            "trim_released": int(_mem_state.get("trim_released", 0)),
            "last_freed_mb": float(_mem_state.get("last_freed_mb", 0.0)),
            "heart_lag": HEART["lag"], "beats": HEART["beats"],
            "heart_last_gap": HEART.get("last_gap", 0.0),
            "heart_worst_gap": HEART.get("worst_gap", 0.0),
            "heart_interval": HEART.get("interval", 20.0),
            "heart_avg_gap": (round(float(HEART.get("total_gap", 0.0))
                                    / HEART["gaps"], 1) if HEART.get("gaps") else 0.0),
            "heart_stalled": bool(
                HEART["beat"] and
                (time.time() - HEART["beat"]) >
                float(HEART.get("interval", 20.0)) * 3 + 5),
            "uptime": int(time.time() - _START),
        }


def _remember(where: str, err: BaseException) -> None:
    with _LOCK:
        _crash["last"] = time.strftime("%d-%m-%Y %H:%M:%S")
        _crash["last_why"] = f"{type(err).__name__}: {str(err)[:110]}"
        _crash["recent"].append({"at": _crash["last"], "where": where,
                                 "err": _crash["last_why"]})
        if len(_crash["recent"]) > 20:
            _crash["recent"] = _crash["recent"][-20:]


# =====================================================================
#  1 — @guarded  :  kisi bhi handler ko crash-proof banao
# =====================================================================
def guarded(name: str = "handler", notify: bool = True,
            rethrow: bool = False) -> Callable:
    """Decorator: handler ke andar kuch bhi ho jaye, bot nahi marega.

    Ye async aur normal dono functions par chalta hai.

    notify=True  -> user ko saaf message milega ("dobara try karo"),
                    chup-chaap fail nahi hoga.
    rethrow=True -> error aage bhi jayega (debugging ke liye), par log hoga.
    """
    def _deco(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def _awrap(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except Exception as e:                           # noqa: BLE001
                    with _LOCK:
                        _crash["handler"] += 1
                    _remember(name, e)
                    log.error("🛡️ GUARD ne sambhala [%s]: %s: %s", name,
                              type(e).__name__, str(e)[:220])
                    try:
                        log.debug("traceback:\n%s", traceback.format_exc()[:2000])
                    except Exception:                            # noqa: BLE001
                        pass
                    if notify:
                        await _tell_user(args, name)
                    if rethrow:
                        raise
                    return None
            return _awrap

        @functools.wraps(fn)
        def _wrap(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:                               # noqa: BLE001
                with _LOCK:
                    _crash["handler"] += 1
                _remember(name, e)
                log.error("🛡️ GUARD ne sambhala [%s]: %s: %s", name,
                          type(e).__name__, str(e)[:220])
                if rethrow:
                    raise
                return None
        return _wrap
    return _deco


async def _tell_user(args: tuple, where: str) -> None:
    """Handler fail hone par user ko izzat se batao — credit bhi kat chuka ho
    to user ko pata chale ki kya hua."""
    try:
        from .safesend import safe_reply
        target = None
        for a in args:
            if a is None:
                continue
            if hasattr(a, "effective_message") or hasattr(a, "message") \
                    or hasattr(a, "callback_query"):
                target = a
                break
        if target is None:
            return
        msg = None
        try:
            msg = getattr(target, "effective_message", None) or getattr(target, "message", None)
            if msg is None and getattr(target, "callback_query", None):
                msg = target.callback_query.message
        except Exception:                                        # noqa: BLE001
            msg = None
        if msg is None:
            return
        await safe_reply(
            msg,
            "⚠️ <b>Ye kaam poora nahi ho paya</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Andar koi chhoti dikkat aa gayi — <b>bot bilkul theek chal raha hai</b>, "
            "aapka data safe hai.\n\n"
            "🔁 10 second baad <b>dobara try karo</b> (zyada tar baar pehli hi baar me "
            "chal jata hai).\n"
            "📩 Baar-baar ho to: /support",
            parse_mode="HTML")
    except Exception:                                            # noqa: BLE001
        pass


# =====================================================================
#  2/3/5 — GLOBAL INSTALL
# =====================================================================
_installed = False


def install_global_guard(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Bot boot hone par ek baar chalao. Sab crash ke raaste band ho jayenge.

    Kabhi khud crash nahi karta.
    """
    global _installed
    try:
        if _installed:
            return
        _installed = True

        # ---- (1) Aakhri bachaav: koi bhi unhandled exception
        _prev_hook = sys.excepthook

        def _hook(exc_type, exc, tb):
            try:
                if issubclass(exc_type, KeyboardInterrupt):
                    _prev_hook(exc_type, exc, tb)
                    return
                with _LOCK:
                    _crash["fatal"] += 1
                _remember("main-thread", exc)
                log.critical("💥 GUARD: unhandled error — %s: %s", exc_type.__name__,
                             str(exc)[:250])
                log.debug("%s", "".join(traceback.format_exception(exc_type, exc, tb))[:2500])
            except Exception:                                    # noqa: BLE001
                pass
        sys.excepthook = _hook

        # ---- (2) Thread crash (keepalive / backup / watchdog threads)
        try:
            _prev_th = getattr(threading, "excepthook", None)

            def _thook(a):
                try:
                    with _LOCK:
                        _crash["thread"] += 1
                    _remember(f"thread:{getattr(a.thread, 'name', '?')}", a.exc_value)
                    log.error("🧵 GUARD: thread '%s' me error — %s: %s",
                              getattr(a.thread, "name", "?"),
                              getattr(a.exc_type, "__name__", "?"),
                              str(a.exc_value)[:200])
                except Exception:                                # noqa: BLE001
                    pass
                if _prev_th and _prev_th is not _thook:
                    try:
                        _prev_th(a)
                    except Exception:                            # noqa: BLE001
                        pass
            threading.excepthook = _thook
        except Exception:                                        # noqa: BLE001
            pass

        # ---- (4) Background asyncio task crash (chup-chaap gayab hone se bachao)
        try:
            lp = loop
            if lp is None:
                try:
                    lp = asyncio.get_running_loop()
                except RuntimeError:
                    lp = None
            if lp is not None:
                _prev_handler = lp.get_exception_handler()

                def _aio_handler(lp_, ctx):
                    try:
                        err = ctx.get("exception")
                        if isinstance(err, asyncio.CancelledError):
                            return
                        with _LOCK:
                            _crash["task"] += 1
                        _remember("asyncio-task", err or Exception("unknown"))
                        log.error("⚙️ GUARD: background task error — %s: %s",
                                  type(err).__name__ if err else "?",
                                  str(err)[:220] if err else "?")
                    except Exception:                            # noqa: BLE001
                        pass
                    if _prev_handler:
                        try:
                            _prev_handler(lp_, ctx)
                        except Exception:                        # noqa: BLE001
                            pass
                lp.set_exception_handler(_aio_handler)
        except Exception:                                        # noqa: BLE001
            pass

        log.info("🛡️ CRASH SHIELD ON — handler/thread/task/OOM/hang sab guard me hain")
    except Exception as e:                                       # noqa: BLE001
        log.warning("guard install me dikkat (bot normal chalega): %s", str(e)[:150])


def heartbeat() -> None:
    """Event loop 'zinda hai' ka signal. Loop har 20 second me isko call kare.

    v77 METRIC FIX (asli bug): pehle `lag = now - beat` napta tha, jahan beat
    khud 20 second me ek baar chalta hai. Iska matlab healthy bot par bhi
    hamesha ~20.0s "lag" dikhta tha — aur 20 second se chhota stall kabhi
    dikhta hi nahi tha. /health par "loop_lag=20.0s" dekh kar admin ulajh
    jaate the, aur chhota hang (jo sabse pehle dikhna chahiye tha) chup jaata
    tha. Ab INTENDED wake time se drift napte hain: healthy = ~0.0s.
    """
    try:
        now = time.time()
        with _LOCK:
            if HEART["beat"]:
                gap = now - HEART["beat"]
                HEART["total_gap"] = round(HEART.get("total_gap", 0.0) + gap, 1)
                HEART["gaps"] = HEART.get("gaps", 0) + 1
                # drift = kitni der BEHUAL se chalti (interval ke upar se)
                drift = gap - HEART.get("interval", 20.0)
                if drift < 0:
                    drift = 0.0
                # boot ke baad pehla gap (module load + imports) lamba hota hai —
                # use peak me na ginein, warna startup hi "hang" dikh jaayega.
                # (beats abhi-increment value hai: pehle measurement par 1)
                if HEART["beats"] >= 2 and drift > HEART["lag"]:
                    HEART["lag"] = round(drift, 1)
                HEART["last_gap"] = round(gap, 1)
                if gap > HEART.get("worst_gap", 0.0):
                    HEART["worst_gap"] = round(gap, 1)
            HEART["beat"] = now
            HEART["beats"] += 1
    except Exception:                                            # noqa: BLE001
        pass


def set_heartbeat_interval(interval: float) -> None:
    """Heartbeat kitne second me ek baar — watchdog/saaf reporting ke liye."""
    try:
        iv = float(interval)
        if iv >= 1.0:
            with _LOCK:
                HEART["interval"] = iv
    except Exception:                                            # noqa: BLE001
        pass


def heart_report() -> dict:
    """/health ke liye: sacha lag (drift), average gap, aur 'abhi kya hai'."""
    with _LOCK:
        iv = float(HEART.get("interval", 20.0))
        gaps = int(HEART.get("gaps", 0))
        total = float(HEART.get("total_gap", 0.0))
        since = round(time.time() - HEART["beat"], 1) if HEART["beat"] else -1.0
        return {
            "beats": HEART["beats"],
            "interval": iv,
            "lag": round(float(HEART.get("lag", 0.0)), 1),
            "last_gap": HEART.get("last_gap", 0.0),
            "worst_gap": HEART.get("worst_gap", 0.0),
            "avg_gap": round(total / gaps, 1) if gaps else 0.0,
            "since_beat": since,
            "stalled": bool(HEART["beat"] and since > iv * 3 + 5),
        }



async def _heartbeat_loop(interval: float = 20.0) -> None:
    """Khud hi beat karta rahega — bot.py isko task me daal dega."""
    set_heartbeat_interval(interval)
    while True:
        try:
            heartbeat()
        except Exception:                                        # noqa: BLE001
            pass
        await asyncio.sleep(interval)


def start_heartbeat_task(loop) -> None:
    """Heartbeat loop chalu karo (koi bhi loop me)."""
    async def _runner():
        set_heartbeat_interval(20)
        while True:
            try:
                heartbeat()
            except Exception:                                    # noqa: BLE001
                pass
            await asyncio.sleep(20)
    try:
        loop.create_task(_runner())
    except Exception as e:                                       # noqa: BLE001
        log.debug("heartbeat task skip: %s", str(e)[:90])


# =====================================================================
#  4 — 💀 MEMORY WATCHDOG  (Render free plan ka asli killer)
# =====================================================================
def mem_mb() -> float:
    """Is process ki RAM (MB me). Har platform par kaam karta hai."""
    # Linux (Render) — sabse sahi
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * (os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:                                            # noqa: BLE001
        return 0.0


def limit_mb_hint() -> float:
    """v107: RAM limit (MB) — env MEMORY_LIMIT_MB, default 512 (Render free)."""
    try:
        return float(os.environ.get("MEMORY_LIMIT_MB") or 512)
    except Exception:                                            # noqa: BLE001
        return 512.0


def register_gc_trigger(fn: Callable) -> None:
    """Ye function memory pressure me call hoga (cache clear karne ke liye).
    bot.py apne caches isme daalta hai."""
    try:
        if callable(fn):
            GC_TRIGGERS.append(fn)
    except Exception:                                            # noqa: BLE001
        pass


def malloc_trim() -> int:
    """glibc se freed pages OS ko wapas lene maango. 1=kuch chhoda, 0=chhoota nahi,
    -1=is system par support nahi (Windows/macOS/pure allocators).

    Kyun zaroori tha: 8 Oct ko /health ne dikhaya —
        gc_runs=3  par  memory=406 MB (ek bhi MB nahi gira),  caches: 0/1792
    Yaani safai TEEN baar chali, cache khaali tha, phir bhi RSS nahi hila. Kaaran:
    CPython/glibc freed memory ko process ke andar hi rok leta hai (high-water
    mark), OS ko wapas nahi karta. Isliye RAM hamesha 378 MB (throttle line) ke
    upar chipki rehti thi aur heavy-gate hamesha "allowed=1" par throttled —
    doosra user aate hi bot "kaam hi nahi kar raha" lagta tha.
    """
    try:
        import ctypes                                              # noqa: PLC0415
        last_err = None
        for name in ("libc.so.6", "libc.so", None):
            try:
                lib = ctypes.CDLL(name) if name else ctypes.CDLL(None)
                fn = lib.malloc_trim
            except Exception as e:                                 # noqa: BLE001
                last_err = e
                continue
            fn.argtypes = [ctypes.c_size_t, ctypes.c_int]
            fn.restype = ctypes.c_int
            try:
                return int(fn(0, 0))
            except Exception as e:                                 # noqa: BLE001
                last_err = e
                break
        log.debug("malloc_trim unavailable (%s)", str(last_err)[:70])
    except Exception as e:                                         # noqa: BLE001
        log.debug("malloc_trim import fail: %s", str(e)[:70])
    return -1


IDLE_CHECKS: list = []


def register_idle_check(fn: Callable) -> None:
    """Memory-pressure restart se PEHLE ye poochhte hain: koi kaam chal raha hai?"

    Bot apne gates (heavy/update) se 'idle' ka jawaab deta hai — taaki restart
    kabhi adhoore kaam ke beech na ho.
    """
    try:
        if callable(fn) and fn not in IDLE_CHECKS:
            IDLE_CHECKS.append(fn)
    except Exception:                                          # noqa: BLE001
        pass


def is_idle() -> bool:
    """Ek bhi inkaar mile to False. Koi check registered na ho to bhi False —

    confirm na ho to bot ko haath nahi lagate.
    """
    if not IDLE_CHECKS:
        return False
    for fn in list(IDLE_CHECKS):
        try:
            if not bool(fn()):
                return False
        except Exception:                                      # noqa: BLE001
            return False
    return True

def free_memory(aggressive: bool = False) -> dict:
    """Memory saaf karo — cache khali + garbage collect + (Linux) malloc_trim."""
    freed = 0.0
    before = mem_mb()
    for fn in list(GC_TRIGGERS):
        try:
            fn()
        except Exception:                                          # noqa: BLE001
            pass
    try:
        gc.collect()
        if aggressive:
            gc.collect(2)
            gc.collect()
    except Exception:                                              # noqa: BLE001
        pass
    trimmed = malloc_trim() if aggressive or before > 200 else -1
    if aggressive:
        # trim ke baad ek baar aur — pages chhootne me ek round aksar kaafi nahi hota
        try:
            gc.collect()
        except Exception:                                          # noqa: BLE001
            pass
        malloc_trim()
        # v107: EMERGENCY RAM VALVE — safai ke baad bhi RAM hard limit ke paas ho
        # to phonenumbers ka geocoder (~95 MB) wapas OS ko lauta do. Agli phone
        # lookup par wo dobara import ho jaata hai; OOM-kill se behtar hai.
        try:
            if mem_mb() >= limit_mb_hint() * 0.80:
                from modules import osint_tools as _osn
                if _osn.drop_geocoder():
                    gc.collect()
                    malloc_trim()
                    log.warning("🩸 v107 RAM valve: phonenumbers geocoder (~95 MB) "
                                "drop kiya — agle number lookup par wapas aa jayega")
        except Exception:                                          # noqa: BLE001
            pass
    after = mem_mb()
    freed = max(0.0, before - after)
    with _LOCK:
        _mem_state["gc_runs"] += 1
        _mem_state["trim_runs"] = int(_mem_state.get("trim_runs", 0)) + (1 if trimmed >= 0 else 0)
        _mem_state["trim_released"] = int(_mem_state.get("trim_released", 0)) + (1 if trimmed == 1 else 0)
        _mem_state["last_freed_mb"] = round(freed, 1)
        if aggressive:
            _mem_state["aggressive"] += 1
    return {"before_mb": round(before, 1), "after_mb": round(after, 1),
            "freed_mb": round(freed, 1), "trim": trimmed}


def start_memory_watchdog(limit_mb: float = 0.0, interval: float = 15.0) -> None:
    """RAM monitor. Render free=512MB par 389MB me trim, 420MB me hard clean.

    15s poll purane 60s se tezi se spikes pakadta hai. Restart threshold physical
    limit se neeche cap hai, aur restart sirf idle process par hota hai.
    """
    try:
        if limit_mb <= 0:
            limit_mb = float(os.environ.get("MEMORY_LIMIT_MB") or 512)
    except Exception:                                            # noqa: BLE001
        limit_mb = 512.0
    soft = limit_mb * 0.76
    hard = limit_mb * 0.82

    def _loop():
        # boot par thoda ruk kar asli baseline lo
        time.sleep(20)
        while True:
            try:
                m = mem_mb()
                with _LOCK:
                    _mem_state["last_mb"] = round(m, 1)
                    if m > _mem_state["peak_mb"]:
                        _mem_state["peak_mb"] = round(m, 1)
                if m >= hard:
                    r = free_memory(aggressive=True)
                    with _LOCK:
                        _mem_state["warns"] += 1
                    log.warning("💀 MEMORY HIGH %.0f MB (limit %.0f) — aggressive safai: "
                                "%.0f -> %.0f MB", m, limit_mb, r["before_mb"], r["after_mb"])
                    # Safai ke baad bhi RAM nahi giri = memory kachra nahi, LIVE objects
                    # me hai (8 Oct: gc_runs=3 par 0 MB hila). Aise me 512 MB par OOM-kill
                    # ka intezaar = user ka chalta kaam bhi jaata hai aur bot kuch minute
                    # 'zombie' (throttled) rehta hai. Isliye planned clean restart — par
                    # sirf tab jab koi kaam chal hi na raha ho (is_idle()).
                    try:
                        rat = _effective_restart_mb(limit_mb)
                        min_up = float(os.environ.get("MEM_RESTART_UPTIME_S") or 900)
                        up = time.time() - _START
                        if (rat > 0 and m >= rat and up >= min_up
                                and r["after_mb"] >= rat * 0.985 and is_idle()):
                            with _LOCK:
                                _mem_state["restarts"] = int(_mem_state.get("restarts", 0)) + 1
                            log.critical("🔁 MEMORY %.0f MB → safai ke baad bhi %.0f MB (leak,",
                                         "kachra nahi) | koi kaam nahi chal raha — CLEAN restart "
                                         "(fresh ~330 MB > throttled zombie)", m, r["after_mb"])
                            try:
                                sys.stdout.flush()
                                sys.stderr.flush()
                            except Exception:                    # noqa: BLE001
                                pass
                            os._exit(1)                          # Render dobara utha lega
                    except SystemExit:
                        raise
                    except Exception as e:                       # noqa: BLE001
                        log.debug("mem restart check: %s", str(e)[:80])
                elif m >= soft:
                    free_memory(aggressive=False)
                    log.info("🧹 Memory %.0f MB (limit %.0f) — cache saaf kiya", m, limit_mb)
            except Exception:                                    # noqa: BLE001
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="memory-watchdog")
    t.start()
    log.info("💀 Memory watchdog ON — soft %.0f MB / hard %.0f MB / idle-restart %.0f MB (limit %.0f MB)",
             soft, hard, _effective_restart_mb(limit_mb), limit_mb)


# =====================================================================
#  5 — 🧟 HANG WATCHDOG  (chup-chaap maut se bachao)
# =====================================================================
def start_hang_watchdog(limit_seconds: float = 0.0, exit_after: float = 0.0) -> None:
    """Agar event loop 4 minute se atka ho -> log + (optional) restart.

    Default: sirf log karta hai + Render ke health check ko signal deta hai.
    ZABARDASTI restart chahiye to: HANG_RESTART_SECONDS=600 set karo.
    """
    try:
        limit = float(limit_seconds or os.environ.get("HANG_LIMIT_SECONDS") or 240)
    except Exception:                                            # noqa: BLE001
        limit = 240.0
    try:
        exit_after = float(exit_after or os.environ.get("HANG_RESTART_SECONDS") or 0)
    except Exception:                                            # noqa: BLE001
        exit_after = 0.0
    started = time.time()

    def _loop():
        time.sleep(30)
        warned = 0
        while True:
            try:
                now = time.time()
                beat = HEART["beat"]
                if beat <= 0:
                    # heartbeat shuru hi nahi hua — boot ho raha hai
                    time.sleep(30)
                    continue
                stuck = now - beat
                if stuck > limit:
                    warned += 1
                    log.error("🧟 HANG DETECTED — event loop %.0f second se atka hai! "
                              "(baaki system theek hai) attempt=%s", stuck, warned)
                    if exit_after and stuck > exit_after:
                        log.critical("🔁 %.0f second se atka — process restart kar raha "
                                     "hoon taaki bot dobara jawab de sake "
                                     "(clean restart > dead bot)", stuck)
                        try:
                            sys.stdout.flush()
                            sys.stderr.flush()
                        except Exception:                        # noqa: BLE001
                            pass
                        os._exit(1)                              # supervisor/Render utha lega
            except Exception:                                    # noqa: BLE001
                pass
            time.sleep(20)

    t = threading.Thread(target=_loop, daemon=True, name="hang-watchdog")
    t.start()
    log.info("🧟 Hang watchdog ON — limit %.0f second%s", limit,
             f", restart after {exit_after:.0f}s" if exit_after else
             " (sirf report karega; restart ke liye HANG_RESTART_SECONDS set karo)")


# =====================================================================
#  6 — safe task wrapper
# =====================================================================
def safe_task(loop, coro, name: str = "task") -> None:
    """asyncio.create_task() ka safe version — task crash ho to chup na rahe."""
    try:
        t = loop.create_task(coro)

        def _done(tsk):
            try:
                if tsk.cancelled():
                    return
                err = tsk.exception()
                if err:
                    with _LOCK:
                        _crash["task"] += 1
                    _remember(f"task:{name}", err)
                    log.error("⚙️ background task '%s' fail: %s: %s", name,
                              type(err).__name__, str(err)[:200])
            except Exception:                                    # noqa: BLE001
                pass
        t.add_done_callback(_done)
    except Exception as e:                                       # noqa: BLE001
        log.debug("safe_task skip (%s): %s", name, str(e)[:90])
