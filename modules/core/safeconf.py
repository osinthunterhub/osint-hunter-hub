# -*- coding: utf-8 -*-
"""
safeconf.py — Crash-proof environment variable reading (v60)
============================================================
KYA PROBLEM THI (asli crash ki jadd):

    ADMIN_ID     = int(os.getenv("ADMIN_ID", "0") or 0)
    FREE_CREDITS = int(os.getenv("FREE_CREDITS", "25") or 25)
    FREE_LIMIT   = int(os.getenv("FREE_LIMIT", "10") or 10)
    REFER_NEED   = int(os.getenv("REFER_NEED", "5") or 5)

Agar Render ke Environment tab me galti se aisi value chali jaye:

    ADMIN_ID = 9876543210        <- thik hai
    ADMIN_ID = "9876543210 "     <- ok (strip ho jata hai int() me)
    ADMIN_ID = 12345   # mera id <- BOOM!  int() -> ValueError -> BOT START HI NAHI HOTA
    ADMIN_ID = @myusername       <- BOOM!
    FREE_CREDITS = 25 credits    <- BOOM!

.hamare hi `.env.example` me aise inline comments likhe hue the — koi copy-paste
kar de to bot boot par hi `ValueError: invalid literal for int()` de kar mar
jata tha. Render logs me "Application failed to start" dikhta aur user ko
samajh hi nahi aata ki kya hua.

AB KUCH NAHI TOOTEGA:
  • `env_int("ADMIN_ID", 0)`  — kachra value aayi to default use karo + WARN log
  • `env_bool("PREMIUM_ONLY", True)` — on/1/yes/true/haan/chalu sab chalta hai
  • `env_float`, `env_str` — same tarah safe

Design rule: YE FILE KABHI EXCEPTION NAHI DEGI. Kuch bhi ho, ek usable value
return karegi. Boot crash namumkin.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Optional

__all__ = ["env_raw", "env_str", "env_int", "env_float", "env_bool", "env_choice",
           "env_list", "conf_report"]

log = logging.getLogger("utility-super-bot.safeconf")

# Ek env value se "asli hissa" nikaalo:
#   " 25   # naye user ko itne credits "  ->  "25"
#   '"https://youtube.com/"'              ->  "https://youtube.com/"
#   "on "                                 ->  "on"
_QUOTES = "\"'`“”‘’"


def env_raw(name: str, default: str = "") -> str:
    """Env value ko saaf karo: quotes hatao, '#' ke baad ka comment hatao.

    Ye kabhi exception nahi deta.
    """
    try:
        v = os.environ.get(name)
    except Exception:                                            # noqa: BLE001
        return default
    if v is None:
        return default
    if not isinstance(v, str):
        try:
            v = str(v)
        except Exception:                                        # noqa: BLE001
            return default
    v = v.strip()
    # BOM / zero-width jo Render ke paste se aate hain
    v = v.replace("\ufeff", "").replace("\u200b", "").strip()
    # aage-peeche ke quotes hatao
    for _ in range(3):
        if len(v) >= 2 and v[0] in _QUOTES and v[-1] in _QUOTES:
            v = v[1:-1].strip()
        else:
            break
    # Inline comment: "25   # blah" -> "25"
    # (par URL me '#anchor' ho sakta hai — uske liye '#' se pehle space chahiye)
    if "#" in v:
        head = v.split("#", 1)[0]
        if head != v and (len(head) == 0 or v[len(head) - 1].isspace()):
            v = head.strip()
    return v.strip()


def env_str(name: str, default: str = "") -> str:
    """Simple safe string."""
    out = env_raw(name, default)
    return out if out else default


def env_int(name: str, default: int = 0,
            lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    """Safe int. Kachra value -> default (aur WARN log). Range clamp ke saath.

    Ye function KABHI ValueError nahi dega — isliye boot crash namumkin.
    """
    raw = env_raw(name, "")
    if raw == "":
        val = int(default)
    else:
        val = None
        # pehla number jaisa hissa nikaalo: "25 credits" -> 25, "12.7" -> 12
        m = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
        if m:
            try:
                val = int(float(m.group(0)))
            except Exception:                                    # noqa: BLE001
                val = None
        if val is None:
            log.warning("%s ki value samajh nahi aayi (%r) — default %s use kar raha hoon",
                        name, raw[:40], default)
            val = int(default)
        elif m and m.group(0).strip() != raw:
            # value me extra kachra tha — user ko bata do, chup na raho
            log.info("%s: %r -> %s maana gaya (extra text hata diya)", name, raw[:40], val)
    if lo is not None and val < lo:
        val = int(lo)
    if hi is not None and val > hi:
        val = int(hi)
    return int(val)


def env_float(name: str, default: float = 0.0,
              lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    """Safe float — kabhi exception nahi."""
    raw = env_raw(name, "")
    if raw == "":
        val = float(default)
    else:
        m = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
        try:
            val = float(m.group(0)) if m else float(default)
        except Exception:                                        # noqa: BLE001
            val = float(default)
    if lo is not None and val < lo:
        val = float(lo)
    if hi is not None and val > hi:
        val = float(hi)
    return float(val)


# Jo bhi "ON" maana jayega (Hinglish bhi!)
_TRUE = {"1", "on", "yes", "y", "true", "t", "haan", "han", "chalu", "chaloo",
         "enable", "enabled", "start", "oka", "ok", "sahi", "on karo"}
_FALSE = {"0", "off", "no", "n", "false", "f", "nahi", "nahin", "band", "disable",
          "disabled", "stop", "galt", "off karo", "none", "null"}


def env_bool(name: str, default: bool = False) -> bool:
    """Safe boolean. 'on'/'haan'/'chalu'/'1'/'yes' sab True.

    Kachra value -> default (jo ki aapne diya).
    """
    raw = env_raw(name, "").lower()
    if raw == "":
        return bool(default)
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    # "on karo bhai" jaisa kuch? pehla shabd dekho
    head = re.split(r"[\s,;]+", raw)[0]
    if head in _TRUE:
        return True
    if head in _FALSE:
        return False
    log.warning("%s: %r samajh nahi aaya boolean — default %s", name, raw[:30], default)
    return bool(default)


def env_choice(name: str, default: str, options: Iterable[str]) -> str:
    """Value ko allowed list me se chuno (case-insensitive). Warna default."""
    opts = [str(o).lower() for o in options]
    raw = env_raw(name, "").lower()
    if raw in opts:
        return raw
    if raw:
        log.warning("%s: %r allowed nahi (%s) — default %r", name, raw[:30],
                    "/".join(opts), default)
    return str(default).lower()


def env_list(name: str, default: str = "", sep: str = ",") -> list:
    """Comma/space se alag list. Khaali entries hata deta hai."""
    raw = env_raw(name, default)
    if not raw:
        return []
    parts = re.split(r"[,\s;]+" if sep in (",", " ") else re.escape(sep), raw)
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------- diagnostics
_CONF_NOTES: list = []


def _note(name: str, ok: bool, detail: str = "") -> None:
    try:
        _CONF_NOTES.append({"name": name, "ok": bool(ok), "detail": detail})
    except Exception:                                            # noqa: BLE001
        pass


def conf_report() -> list:
    """Kaun-kaun si env value suspect thi — /sys panel ke liye."""
    return list(_CONF_NOTES)
