"""
Number Info Tool - ULTRA FAST - Same as utility-duniya-bot
Uses utility-api-vu83 gateway with caching + session pooling
"""

import os
import re
import time
import fasthttp as requests
from typing import Dict, Any
from .fast_cache import get_session, api_cache_get, api_cache_set

API_URL = os.getenv("MYNUM_API_URL", "https://utility-api-we9m.onrender.com/api")
API_KEY = os.getenv("MYNUM_API_KEY", "")
TIMEOUT = (4, int(os.getenv("MYNUM_API_TIMEOUT", "20")))  # v2.17: gateway ab fast hai; 25s waste hataaya

def clean_number(num: str) -> str:
    d = re.sub(r'\D', '', str(num))
    if len(d) == 12 and d.startswith('91'):
        d = d[2:]
    elif len(d) == 11 and d.startswith('0'):
        d = d[1:]
    return d if len(d) == 10 else ""

def mask_aadhaar(v: str) -> str:
    if not v:
        return ""
    d = re.sub(r'\D', '', str(v))
    if len(d) < 4:
        return ""
    if len(d) == 12:
        return "XXXX-XXXX-" + d[-4:]
    return "X"*(len(d)-4) + d[-4:]

def _sarkari_num(digits: str):
    """v2.8 fallback: sarkariallupdates ?=num direct — utility-api upstream down rahe toh.
    None = fail/milta nahi, dict = success result (number_info ke contract mein)."""
    try:
        url = (f"https://api.sarkariallupdates.com/api?=num&key=30day"
               f"&q={digits}&xr={digits}&num={digits}&term={digits}")
        r = get_session().get(url, timeout=8)  # v2.17: fallback ka time kam (5.4MB source)
        if r.status_code != 200:
            return None
        j = r.json()
        lst = j.get("data") if isinstance(j, dict) else None
        if not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
            return None
        first = lst[0]
        def g(*names):
            for n in names:
                v = first.get(n)
                if v is not None and str(v).strip() and str(v).strip().lower() not in ("n/a", "na", "none", "null", "-", "0"):
                    return str(v).strip()
            return ""
        owner = {}
        for k, names in {
            "name": ("name",), "father": ("fname", "father_name"),
            "phone": ("mobile", "mobile_no"), "alt_phone": ("alt", "alt_mobile"),
            "address": ("address",), "circle": ("circle",),
            "aadhaar": ("id", "aadhar_card_no", "aadhaar"),
        }.items():
            v = g(*names)
            if v:
                owner[k] = v
        if "aadhaar" in owner:
            owner["aadhaar"] = mask_aadhaar(owner["aadhaar"])
        if not owner.get("name") and not owner.get("phone"):
            return None
        try:
            rec = int(j.get("count") or len(lst))
        except Exception:
            rec = len(lst)
        return {
            "ok": True, "number": digits, "owner": owner,
            "circle": owner.get("circle", ""), "records": max(1, rec),
            "ms": 0, "source": "sarkari-api", "raw": first,
        }
    except Exception:
        return None

def lookup_number(number: str) -> Dict[str, Any]:
    digits = clean_number(number)
    if not digits:
        return {"ok": False, "error": "Sahi 10-digit number bhejo. Example: 9876543210"}

    # Check cache first - 5 min TTL - ULTRA FAST
    cached = api_cache_get("num", digits)
    if cached:
        cached = cached.copy()
        cached["ms"] = 0
        cached["cached"] = True
        return cached

    try:
        t0 = time.time()
        session = get_session()
        r = session.get(
            API_URL,
            params={"key": API_KEY, "tool": "num", "term": digits},
            timeout=TIMEOUT
        )
        ms = int((time.time() - t0)*1000)

        if r.status_code in (401,403) or r.status_code >= 400:
            # v2.8: gateway auth/rate/HTTP fail par bhi sarkari fallback try karo
            fb = _sarkari_num(digits)
            if fb is not None:
                fb["ms"] = int((time.time() - t0) * 1000)
                api_cache_set("num", digits, fb, ttl=43200)
                return fb
            if r.status_code in (401,403):
                return {"ok": False, "error": f"API key reject (HTTP {r.status_code})"}
            if r.status_code == 429:
                return {"ok": False, "error": "API limit khatam (429), thodi der baad try karo"}
            return {"ok": False, "error": f"API HTTP {r.status_code}"}

        data = r.json()
        d = data.get("data", {}) if isinstance(data, dict) else {}

        # SPEED v2.12.2: gateway NE KHUD sarkari chala (native) — honest notfound
        # par WAPAS sarkari fallback = double query (~1.5s waste). Seedha return + cache.
        if isinstance(data, dict) and data.get("ok") is False and str(data.get("why") or "").lower() in ("notfound", "not_found"):
            nf = {"ok": False, "not_found": True,
                  "error": "❌ No records found for this number in any database.", "ms": ms}
            api_cache_set("num", digits, nf, ttl=1800)
            return nf

        if not d or not isinstance(d, dict) or not any(str(k).startswith("owner_name") for k in d.keys()):
            # v2.8: utility-api upstream down (why=upstream) ya shape alag → sarkari ?=num fallback
            fb = _sarkari_num(digits)
            if fb is not None:
                fb["ms"] = int((time.time() - t0) * 1000)
                api_cache_set("num", digits, fb, ttl=43200)
                return fb
            nf = {"ok": False, "not_found": True, "error": "❌ No records found for this number in any database.", "ms": ms}
            api_cache_set("num", digits, nf, ttl=1800)
            return nf

        owner = {
            "name": (d.get("owner_name") or "").strip(),
            "father": (d.get("father_name") or "").strip(),
            "phone": (d.get("mobile_no") or "").strip(),
            "alt_phone": (d.get("alt_mobile") or "").strip(),
            "address": (d.get("address") or "").strip(),
            "circle": (d.get("circle") or "").strip(),
            "aadhaar": mask_aadhaar(d.get("aadhar_card_no")),
        }
        owner = {k:v for k,v in owner.items() if v and v.lower() not in ("na","n/a","none","null","-")}

        rec_count = len([k for k in d.keys() if k.startswith("owner_name")])

        result = {
            "ok": True,
            "number": digits,
            "owner": owner,
            "circle": d.get("circle",""),
            "records": rec_count,
            "ms": ms,
            "raw": d
        }
        # Cache success for 1 hour (ultra-fast repeat)
        api_cache_set("num", digits, result, ttl=3600)
        return result

    except Exception as e:
        # v2.8: network/parse fail par bhi sarkari fallback
        try:
            fb = _sarkari_num(digits)
            if fb is not None:
                fb["ms"] = int((time.time() - t0) * 1000)
                api_cache_set("num", digits, fb, ttl=43200)
                return fb
        except Exception:
            pass
        return {"ok": False, "error": f"Lookup error: {str(e)[:100]}"}

from modules.cardstyle import box, field, sep, footer

def format_card(result: Dict[str, Any]) -> str:
    """📱 NUMBER INFO — owner signature design (box + bold title + spaced fields)."""
    if not result.get("ok"):
        return result.get("error", "❌ No records found for this number in any database.")

    owner = result.get("owner", {})
    num = result.get("number")
    circle = result.get("circle") or owner.get("circle", "")
    ms = result.get("ms", 0)
    rec = result.get("records", 1)

    L = [box("📱", "NUMBER INFO"), ""]
    L.append(field("🔢", "Phone", num))
    L.append(field("🌐", "Circle", circle))
    L.append(field("👤", "Name", owner.get("name")))
    L.append(field("👨‍👦", "Father", owner.get("father")))
    L.append(field("📞", "Mobile", owner.get("phone")))
    L.append(field("📞", "Alt Mobile", owner.get("alt_phone")))
    L.append(field("🏠", "Address", str(owner.get("address") or "")[:200]))
    L.append(field("🆔", "Aadhaar", owner.get("aadhaar")))
    L.append(sep())
    extra = f" · 📑 {rec} records" if rec and rec > 1 else ""
    L.append(footer(ms, extra))
    return "".join([x for x in L if x])
