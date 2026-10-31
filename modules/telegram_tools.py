"""
Telegram Tools — TELEGRAM NUMBER (owner signature design)
Gateway: utility-api-vu83 (owner ka apna local dataset)
"""

import os
import re
import time
from modules.cardstyle import box, field, sep, footer
from .fast_cache import with_cache
from typing import Dict, Any

API_URL = os.getenv("MYNUM_API_URL", "https://utility-api-we9m.onrender.com/api")
API_KEY = os.getenv("MYNUM_API_KEY", "")
TIMEOUT = 25

def clean_input(text: str) -> str:
    return str(text or "").strip()

@with_cache("tg4", 300, 2592000)   # v2.5.2: tg2 ke public-card entries orphan (number wapas)
def lookup_tg(username: str) -> Dict[str, Any]:
    """
    Telegram to Number - tool=telegramtonumber or tgnum
    Input: @username or telegram username or phone?
    """
    term = clean_input(username).lstrip('@')
    if not term:
        return {"ok": False, "error": "Username bhejo. Example: @username ya username"}

    # Try both tool names - utility bot uses telegramtonumber and tgnum
    for tool_name in ["telegramtonumber", "tgnum"]:
        try:
            t0 = time.time()
            from .fast_cache import get_session
            r = get_session().get(
                API_URL,
                params={"key": API_KEY, "tool": tool_name, "term": term},
                timeout=TIMEOUT
            )
            ms = int((time.time()-t0)*1000)
            if r.status_code != 200:
                continue
            data = r.json()
            # Parse - similar to other tools
            if not data or not isinstance(data, dict):
                continue
            
            # Check success
            d = data.get("data") or data
            if isinstance(d, dict) and not d:
                continue
            
            # If has error field
            if isinstance(data, dict) and (data.get("success") == False or data.get("ok") is False):
                continue

            return {"ok": True, "tool": tool_name, "term": term, "data": d, "ms": ms, "raw": data}
        except Exception as e:
            continue

    # v2.5.4 (USER ORDER 18 Oct): t.me PUBLIC-profile fallback PERMANENT BAND.
    # TG tool ab sirf APNE HUB se number deta hai; hub se number na mile to
    # sirf "data not found in any database".
    return {"ok": False, "not_found": True,
            "error": "Data not found in any database."}


def format_tg_card(result: Dict[str, Any]) -> str:
    """🆔 TELEGRAM NUMBER — owner signature design."""
    if not result.get("ok"):
        return result.get("error", "❌ No records found")

    data = result.get("data", {})
    ms = result.get("ms", 0)
    L = [box("🆔", "TELEGRAM NUMBER"), ""]
    L.append(field("🔍", "Query", result.get("term")))
    if isinstance(data, dict):
        for k, v in list(data.items())[:20]:
            if str(k).lower() in ("source",):
                L.append(field("📡", str(k), v))
            else:
                L.append(field("🔹", str(k).replace("_", " ").title(), v))
    else:
        L.append(f"{str(data)[:1000]}\n\n")
    L.append(sep())
    L.append(footer(ms))
    return "".join([x for x in L if x])

def lookup_tg_info_live(username: str) -> Dict[str, Any]:
    """v2.26.4 — Telegram LIVE info via gateway tool=tginfo (t.me-scrape + BotAPI)."""
    for attempt in range(2):
        try:
            resp = requests.get(
                API_URL,
                params={"key": API_KEY, "tool": "tginfo", "term": username},
                timeout=30,
            )
            if resp.status_code == 200:
                j = resp.json()
                if isinstance(j, dict) and j.get("ok"):
                    return j
                if isinstance(j, dict) and j.get("why") in ("notfound", "validation", "unknown_tool"):
                    return j
        except Exception:
            pass
        time.sleep(1)
    return {"ok": False, "msg": "gateway tginfo failed"}
