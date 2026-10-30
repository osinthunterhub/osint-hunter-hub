# -*- coding: utf-8 -*-
"""
updategate.py — 🚦 UPDATE GATE (v77): fast bot + no double-work + no races
===========================================================================

DO asli samasyayein jo yahan sulajh rahi hain
---------------------------------------------
1) **"Bot atak jaata hai"**
   python-telegram-bot ka default `concurrent_updates = 1` hai — matlab ek
   bhi update ke khatam hone tak poora bot BAAKI sab ke liye ruk jaata hai.
   Ek user ne YouTube ka link bheja aur tool 28 second leta hai → us 28
   second me 40 aur users ka koi message process NAHI hota. Sabko lagta hai
   "bot crash ho gaya" (bot zinda tha, sirf line me khada tha).

   Webhook mode me iska ek aur nuksaan: Telegram ko har update ka HTTP jawab
   jaldi chahiye. Jawab der se aaye to Telegram **wahi update dobara bhejta
   hai** (retry). Retry = wahi kaam dobara = RAM/CPU double = OOM kill.
   Kuch tools (payment approve, credit deduct, bulk report) dobara chal jaate
   hain to **dohra kaam / dohra paisa** bhi ho sakta hai.

2) **Concurrency khulane ka khatra (isliye seedha 256 nahi kiya)**
   Bot ke paas bahut se "wizard" flow hain (RESULT CHECK, BUSINESS STUDIO ke
   6-step forms, TEMP NUMBER) jo user ke state ko ek-ke message par aage
   badhate hain. Agar ek hi user ke do message *saath me* chalne lagein to
   state ka order bigad jaata hai (step 3 se pehle step 4 save ho gaya).

Iska hal (ye file)
------------------
• Alag-alag **chat** ke updates parallel chalte hain  → bot kabhi "line me"
  nahi atakta (default 1 se max 8 tak, env se badaltegable).
• Ek hi **chat** ke updates ek lock se **क्रमबद्ध** (in order) chalte hain →
  wizard state ki suraksha bani rehti hai, bilkul aaj jaisi hi.
• Ek hi **update_id** dobara aaya (Telegram retry) to use chup-chaap **drop**
  kar diya jaata hai — double kaam, double credit, double ffmpeg nahi.
  Drop counters /health par dikhte hain, taaki pata chale ki retry ho rahe the.
• Semaphore (zyada se zyada N updates ek saath) RAM ko upar se cap karta hai;
  bhaari kaam ka apna alag gate `modules/core/heavy.py` me hai.

Kabhi bhi crash nahi karta: update_id na mile, chat na mile, PTB ka API badal
jaaye — sab me kaam normal chalta rehta hai (fail-open).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Dict, Optional

log = logging.getLogger("utility-super-bot.updategate")

try:                                                   # PTB 20.4+
    from telegram.ext import BaseUpdateProcessor as _Base
    _HAVE_PTB = True
except Exception:                                      # noqa: BLE001
    _Base = object                                     # tests/standalone fallback
    _HAVE_PTB = False

__all__ = ["GuardedUpdateProcessor", "build_processor", "stats", "health_line"]


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        raw = (os.environ.get(name) or "").strip()
        num = ""
        for ch in raw:
            if ch.isdigit():
                num += ch
            elif num:
                break
        v = int(num) if num else default
    except Exception:                                  # noqa: BLE001
        v = default
    return max(lo, min(hi, v))


# seen update_ids itne time tak yaad rakhe jaate hain (Telegram retries usually
# kuch second-to-minute me aate hain; 10 min safe hai)
_SEEN_TTL = _env_int("UPDATE_DEDUPE_TTL", 600, 30, 7200)
_SEEN_MAX = _env_int("UPDATE_DEDUPE_SIZE", 8192, 256, 200000)
_MAX_CONC = _env_int("MAX_CONCURRENT_UPDATES", 8, 1, 64)
_DEDUPE_ON = (os.environ.get("UPDATE_DEDUPE", "on").strip().lower()
              not in ("off", "0", "no", "false"))
_PER_CHAT_ORDER = (os.environ.get("UPDATE_PER_CHAT_ORDER", "on").strip().lower()
                   not in ("off", "0", "no", "false"))


class GuardedUpdateProcessor(_Base):                   # type: ignore[misc]
    """Bounded concurrency + per-chat ordering + duplicate-retry drop."""

    def __init__(self, max_concurrent_updates: int = _MAX_CONC,
                 dedupe: bool = _DEDUPE_ON, per_chat_order: bool = _PER_CHAT_ORDER,
                 seen_ttl: float = _SEEN_TTL, seen_max: int = _SEEN_MAX):
        try:
            super().__init__(max_concurrent_updates)
        except Exception:                              # noqa: BLE001
            pass
        self._max = max(1, int(max_concurrent_updates))
        self._dedupe = bool(dedupe)
        self._order = bool(per_chat_order)
        self._ttl = float(seen_ttl)
        self._seen_cap = int(seen_max)
        self._seen: "Dict[int, float]" = {}     # update_id -> expire epoch
        self._locks: "Dict[int, asyncio.Lock]" = {}
        self._lock_users: Dict[int, int] = {}
        self._st: Dict[str, Any] = {
            "total": 0, "parallel": 0, "peak_parallel": 0, "dropped_dup": 0,
            "waiting_chat_lock": 0, "serial": 0, "errors": 0,
            "last_drop_id": None, "last_drop_age": None,
        }

    # ------------------------------------------------------------------ utils
    def _forget_old(self) -> None:
        now = time.time()
        if len(self._seen) > self._seen_cap:
            cut = now - self._ttl
            for k in [k for k, v in self._seen.items() if v < cut][:len(self._seen)
                                                                     - self._seen_cap]:
                self._seen.pop(k, None)
            # ab bhi bada ho to sabse purane 25% fenk do (memory kabhi na phate)
            while len(self._seen) > self._seen_cap:
                self._seen.pop(next(iter(self._seen)), None)

    @staticmethod
    def _chat_of(update: Any) -> Optional[int]:
        try:
            eff = update.effective_message or update.effective_chat
            if eff is None:
                return None
            cid = getattr(eff, "chat_id", None)
            if cid is None:
                cid = getattr(getattr(eff, "chat", None), "id", None)
            return int(cid) if cid is not None else None
        except Exception:                              # noqa: BLE001
            return None

    # ------------------------------------------------------------------ PTB hook
    async def do_process_update(self, update: Any, coroutine: Awaitable[Any]) -> None:
        self._st["total"] += 1
        uid = None
        try:
            uid = int(getattr(update, "update_id"))
        except Exception:                              # noqa: BLE001
            uid = None

        # 1) Telegram ka dobara bheja hua SAME update -> drop (double kaam nahi)
        if self._dedupe and uid is not None:
            now = time.time()
            exp = self._seen.get(uid)
            if exp is not None and exp > now:
                self._st["dropped_dup"] += 1
                self._st["last_drop_id"] = uid
                self._st["last_drop_age"] = round(now - (self._ttl - (exp - now)), 1)
                try:
                    coroutine.close()                  # "never awaited" warning nahi
                except Exception:                      # noqa: BLE001
                    pass
                return
            self._seen[uid] = now + self._ttl
            self._forget_old()

        # 2) parallel gauge
        self._st["parallel"] += 1
        if self._st["parallel"] > self._st["peak_parallel"]:
            self._st["peak_parallel"] = self._st["parallel"]
        try:
            cid = self._chat_of(update) if self._order else None
            if cid is None:
                self._st["serial"] += 1
                await coroutine                        # jaisa tha waisa
            else:
                lk = self._locks.get(cid)
                if lk is None:
                    if len(self._locks) > 4096:        # locks kabhi na badhte jaayein
                        for k in [k for k, u in list(self._locks.items())
                                  if not u.locked()][:2048]:
                            self._locks.pop(k, None)
                            self._lock_users.pop(k, None)
                    lk = asyncio.Lock()
                    self._locks[cid] = lk
                self._lock_users[cid] = self._lock_users.get(cid, 0) + 1
                if lk.locked():
                    self._st["waiting_chat_lock"] += 1
                async with lk:                         # ek chat = ek time par ek update
                    await coroutine
                self._lock_users[cid] = max(0, self._lock_users.get(cid, 1) - 1)
        except Exception:                              # noqa: BLE001
            self._st["errors"] += 1
            raise
        finally:
            self._st["parallel"] -= 1

    async def initialize(self) -> None:
        try:
            await super().initialize()                 # type: ignore[misc]
        except Exception:                              # noqa: BLE001
            pass
        log.info("🚦 UPDATE GATE ON — %s update parallel, per-chat order=%s, "
                 "retry-drop=%s", self._max, "ON" if self._order else "off",
                 "ON" if self._dedupe else "off")

    async def shutdown(self) -> None:
        try:
            await super().shutdown()                   # type: ignore[misc]
        except Exception:                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------ report
    def stats(self) -> Dict[str, Any]:
        out = dict(self._st)
        out["max_concurrent"] = self._max
        out["dedupe"] = self._dedupe
        out["per_chat_order"] = self._order
        out["seen_size"] = len(self._seen)
        out["locks"] = len(self._locks)
        out["ptb"] = _HAVE_PTB
        return out


_PROC: Optional[GuardedUpdateProcessor] = None


def build_processor() -> Optional[GuardedUpdateProcessor]:
    """Builder ke liye processor; PTB na ho/error ho to None (bot phir bhi chalega)."""
    global _PROC
    try:
        if not _HAVE_PTB:
            return None
        _PROC = GuardedUpdateProcessor(max_concurrent_updates=_MAX_CONC,
                                       dedupe=_DEDUPE_ON,
                                       per_chat_order=_PER_CHAT_ORDER)
        return _PROC
    except Exception as e:                             # noqa: BLE001
        log.warning("update gate skip (%s) — bot normal chalega", str(e)[:120])
        _PROC = None
        return None


def stats() -> Dict[str, Any]:
    try:
        return _PROC.stats() if _PROC is not None else {"off": True}
    except Exception as e:                             # noqa: BLE001
        return {"error": str(e)[:80]}


def health_line() -> str:
    s = stats()
    if s.get("off"):
        return "update-gate: off"
    return (f"update-gate: {s.get('parallel', 0)}/{s.get('max_concurrent', 1)} in "
            f"flight (peak {s.get('peak_parallel', 0)}) | total {s.get('total', 0)} "
            f"| dropped retries {s.get('dropped_dup', 0)} "
            f"| chat-lock waits {s.get('waiting_chat_lock', 0)} "
            f"| order={'per-chat' if s.get('per_chat_order') else 'global'} "
            f"| tracked {s.get('seen_size', 0)}")
