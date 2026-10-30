# -*- coding: utf-8 -*-
"""
🛰️ GATEWAY TOOLS v3 — BUSINESS INFO + E-CHALLAN (RC ka hissa)
===============================================================
Ye tools USER ke APNE gateway (utility-api) par chalte hain:
phonebiz = owner ka local 1.72M index · echallan = RC+CHALLAN ke andar merge.

    GET https://utility-api-v2.onrender.com/api
        ?key=<MYNUM_API_KEY>&tool=phonebiz|challan&term=<input>

Architectural rule (USER ORDER, 23 Oct 2026):
    "Pehle API ko MERE LIYE personally banao (gateway), PHIR bots mein lagao."
    → Bot kabhi kisi random third-party API ko directly nahi chhunta.
    → Never-expire: source badalne par sirf gateway ka env var badalta hai.

phonebiz → owner ka local 1.72M B2B index (never-expire)
challan  → {vehicle, status_queried, total_count, total_amount,
            results:{<status>:{count, total_amount, challans[<=5]}}}

Cache: fast_cache.with_cache — sirf ok/notfound cache hota hai, errors nahi
(safety built-in in api_cache_set).
"""

from __future__ import annotations

import html
import os
import re
import time
from typing import Any, Dict, List

from modules.cardstyle import box, field, sep, footer, subhead
from .fast_cache import with_cache, get_session

API_URL = os.getenv("MYNUM_API_URL", "https://utility-api-v2.onrender.com/api").strip()
API_KEY = os.getenv("MYNUM_API_KEY", "").strip()
TIMEOUT = int(os.getenv("GW_TIMEOUT", "20"))  # v2.17

# Gateway / tool infra keys — user ko nahi dikhani (noise ya secret)


# ══════════════════════════════════════════════════════════════════
#  CORE GATEWAY CALL
# ══════════════════════════════════════════════════════════════════
def _call(tool: str, term: str) -> Dict[str, Any]:
    """Gateway lookup — telegram_tools.lookup_tg jaisa, par tool param naya."""
    t0 = time.time()
    if not term:
        return {"ok": False, "error": "Input khaali hai."}
    try:
        r = get_session().get(
            API_URL,
            params={"key": API_KEY, "tool": tool, "term": term},
            timeout=(5, TIMEOUT),
        )
        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return {"ok": False, "error": f"gateway HTTP {r.status_code}"}
        try:
            j = r.json()
        except Exception:
            return {"ok": False, "error": "gateway se bad JSON aaya"}
        if not isinstance(j, dict):
            return {"ok": False, "error": "gateway ne ajeeb jawab diya"}
        if not j.get("ok"):
            why = str(j.get("why") or j.get("message") or j.get("error") or "").strip()
            if why.lower() in ("notfound", "not_found", "no record found"):
                return {"ok": False, "not_found": True, "error": "Data not found."}
            # v2.17 — technical 'upstream/degraded' user ko saaf bhasha me
            _tech = {"upstream": "service abhi available nahi hai (source down) — thodi der baad try karo",
                     "degraded": "service abhi available nahi hai — owner ko notify kar diya gaya",
                     "timeout": "source slow hai — thodi der baad try karo",
                     "credits": "service ka quota khatam — thodi der baad try karo"}
            msg = _tech.get(why.lower(), (why or "lookup fail")[:160])
            return {"ok": False, "error": msg, "tech": why}
        d = j.get("data")
        if not isinstance(d, dict) or not d:
            return {"ok": False, "not_found": True, "error": "Data not found."}
        return {"ok": True, "tool": tool, "term": term, "data": d, "ms": ms}
    except Exception as e:                                         # noqa: BLE001
        return {"ok": False, "error": f"network: {str(e)[:120]}"}


_VEHICLE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")


def _clean_vehicle(term: Any) -> str:
    return re.sub(r"\s+", "", str(term or "")).upper()


# ══════════════════════════════════════════════════════════════════
#  LOOKUPS (grid + commands dono yahi use karte hain)
# ══════════════════════════════════════════════════════════════════


@with_cache("echallan", 600, 43200)
def lookup_echallan(term: Any) -> Dict[str, Any]:
    """📑 Gaadi number → Parivahan e-challan (ansh source, gateway env: CHALLAN_API_*)."""
    v = _clean_vehicle(term)
    if not _VEHICLE_RE.match(v):
        return {"ok": False, "error": "Gaadi number sahi format me bhejo — jaise DL8CAB1234"}
    return _call("challan", v)


# ══════════════════════════════════════════════════════════════════
#  FORMATTERS (HTML cards — bot style: box header + fields + footer)
# ══════════════════════════════════════════════════════════════════
def _e(v: Any) -> str:
    """HTML-escape API value (ParseMode.HTML na toote)."""
    return html.escape(str(v if v is not None else ""))


def _rupee(n: Any) -> str:
    try:
        return f"₹{int(n):,}"
    except Exception:
        return str(n or "0")


def _ms(result: Dict[str, Any]) -> str:
    return str(result.get("ms") or 0)



# ---------- 📞 PHONE → BUSINESS (gateway tool=phonebiz, owner's local index) ----------
@with_cache("phonebiz", 1800, 86400)
def lookup_phonebiz(term: Any) -> Dict[str, Any]:
    """📞 Phone/landline → business info (gateway v2.10 local 1.72M index)."""
    d = re.sub(r"\D", "", str(term or ""))
    if not (8 <= len(d) <= 15):
        return {"ok": False, "error": "8-15 digit phone number bhejo — landline ya mobile dono chalega."}
    return _call("phonebiz", d)


def format_phonebiz_card(result: Dict[str, Any]) -> str:
    """📞 BUSINESS INFO — owner signature design."""
    d = result.get("data") or {}
    num = str(result.get("term") or "").strip()
    addr = ", ".join(x for x in [str(d.get("street_address") or "").strip(),
                                 str(d.get("area") or "").strip()] if x)
    city_line = ", ".join(x for x in [str(d.get("city") or "").strip(),
                                      str(d.get("state") or "").strip()] if x)
    L = [box("📞", "BUSINESS INFO"), ""]
    L.append(f"🔢 <b>Number:</b> <code>{_e(num)}</code>\n\n")
    L.append(field("🏢", "Business", d.get("business_name")))
    L.append(field("🏠", "Address", addr))
    L.append(field("🏙", "City", city_line))
    L.append(field("📌", "Pincode", d.get("pincode")))
    L.append(field("📧", "Email", d.get("email")))
    L.append(field("🌐", "Website", d.get("website")))
    L.append(field("📇", "Phones", d.get("phones")))
    L.append(field("📶", "DND Status", d.get("dnd_status")))
    L.append(field("🗺", "Directory", d.get("sheet")))
    L.append(field("🔍", "Matched", d.get("matched_phone")))
    L.append(sep())
    L.append(footer(result.get("ms") or 0))
    return "".join([x for x in L if x])


def echallan_section(data: Dict[str, Any], ms: int = 0) -> str:
    """📑 E-CHALLAN block — RC+CHALLAN card ke andar render hota hai (owner order)."""
    if not isinstance(data, dict) or not data:
        return ""
    L = [sep(), subhead("E-CHALLAN (PARIVAHAN)")]
    L.append(f"📊 <b>Total:</b> {_e(str(data.get('total_count', 0)))} challan · <b>{_rupee(data.get('total_amount', 0))}</b>\n\n")
    results = data.get("results") or {}
    if not isinstance(results, dict) or not results:
        L.append("❌ Koi challan record nahi mila.\n\n")
        return "".join(L)
    for status, grp in list(results.items())[:4]:
        if not isinstance(grp, dict):
            continue
        gcount = grp.get("count", 0)
        gamt = grp.get("total_amount", 0)
        L.append(f"🗂 <b>{_e(str(status).upper())}</b> — {_e(gcount)} · <b>{_rupee(gamt)}</b>\n\n")
        for i, ch in enumerate((grp.get("challans") or [])[:5], 1):
            if not isinstance(ch, dict):
                continue
            cn = ch.get("challan_number") or ch.get("id") or "?"
            date = str(ch.get("challan_date") or "")[:10]
            amt = _rupee(ch.get("amount"))
            L.append(f"   {i}. <code>{_e(cn)}</code> · {_e(date)} · <b>{amt}</b>\n")
            who = str(ch.get("accused_name") or "").strip()
            src_ = str(ch.get("source") or "").strip()
            bits = []
            if who:
                bits.append(f"👤 {_e(who)}")
            if src_:
                bits.append(f"📍 {_e(src_)}")
            if bits:
                L.append("      " + " · ".join(bits) + "\n")
            cd = ch.get("court_data")
            if isinstance(cd, dict) and cd.get("is_virtual_court"):
                L.append("      🏛 Virtual Court\n")
            L.append("\n")
        more = int(gcount or 0) - min(int(gcount or 0), 5)
        if more > 0:
            L.append(f"   … +{more} aur\n\n")
    return "".join(L)


