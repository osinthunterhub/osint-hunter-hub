# -*- coding: utf-8 -*-
"""
truecaller.py — 📱 TRUE CALLER PRO LOOKUP (bot tool, v2.18.1)
=============================================================
Number daalo → naam, carrier, circle, IMEI, IP, MAC, locations, complaints —
sab ek hi PRO card me, inline buttons ke saath (live refresh + num info).

Architecture (wahi purana rule — koi random API direct nahi):
    Bot sirf apne gateway (utility-api) ka `tool=tc` chhuntа hai. Uske peeche
    provider chain lagi hai. Gateway par hi key-scope hota hai, isliye ek key
    leak ho to bhi baaki tools safe.

3-LAYER (koi ek mare to bhi tool zinda):
    L1  provider   — rich fields (IMEI/IP/MAC/locations), slow 10-14s
    L2  offline    — phonenumbers lib: carrier/circle/timezone — 0 ms, kabhi fail nahi
    L3  num        — apna dataset: naam/pita/address/alt number

Pro touches:
    • 12h cache — repeat lookup 0 ms, card par "⚡ instant"
    • lookup_truecaller_fresh() — cache bypass (Live refresh button ke liye)
    • complaints se risk badge (✅ Clean / ⚠️ reports)
    • number ka proper format: +91 XXXXX XXXXX
    • card me sections: IDENTITY / SIM & NETWORK / LOCATION / DEVICE / REPUTATION
    • footer me layer badges — kahan se kya aaya, saaf dikhta hai
"""
from __future__ import annotations

import html
import os
import re
import time
from typing import Any, Dict

from modules.cardstyle import box, field, footer, sep, subhead
from .fast_cache import get_session, with_cache

API_URL = os.getenv("MYNUM_API_URL", "https://utility-api-v2.onrender.com/api").strip()
API_KEY = (os.getenv("MYNUM_API_KEY") or os.getenv("GATEWAY_KEY") or "").strip()
TIMEOUT = (5, int(os.getenv("TRUECALLER_TIMEOUT", "30")))

# card ke sections — (icon, label, provider-key candidates)
_SECTIONS = [
    ("🪪 IDENTITY", [
        ("👤", "Owner Name", ("name", "Owner Name", "owner_name", "full_name")),
        ("🏷", "Alt Name", ("alt_name", "aka", "Alias")),
        ("👨", "Father Name", ("Father Name", "father_name")),
        ("✉️", "Email", ("email", "Email", "Owner Email")),
    ]),
    ("📶 SIM & NETWORK", [
        ("📡", "Connection", ("Connection", "connection", "carrier", "network")),
        ("🌍", "Country", ("Country", "country")),
        ("📱", "Number", ("Number", "number", "mobile", "phone")),
        ("💬", "Language", ("Language", "language")),
    ]),
    ("📍 LOCATION", [
        ("📍", "State", ("Mobile State", "state", "circle")),
        ("🏠", "Hometown", ("Hometown", "hometown", "address", "Owner Address")),
        ("🗺", "Locations", ("Mobile Locations", "locations")),
        ("🕒", "Last Seen", ("Last Seen", "last_seen")),
    ]),
    ("📟 DEVICE & TECH", [
        ("📟", "IMEI", ("IMEI Number", "imei", "IMEI")),
        ("🌐", "IP", ("IP Address", "ip", "ip_address")),
        ("🔗", "MAC", ("MAC Address", "mac", "mac_address")),
        ("🧠", "Personality", ("Owner Personality", "personality")),
    ]),
]

# ye keys card ke sections me kabhi "EXTRA" me nahi dikhengi
_COVERED = {"Complaints", "complaints", "reports", "Time Zone", "Series",
            "Alt Number", "connection", "carrier"}
_SKIP_KEYS = {"success", "endpoint", "query", "data", "api", "source", "cached",
              "ms", "provider", "provider_note", "raw"} | _COVERED


def clean_number(num: Any) -> str:
    d = re.sub(r"\D", "", str(num or ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "6789" else ""


def pretty_number(n: str) -> str:
    """9876543210 → +91 98765 43210"""
    return f"+91 {n[:5]} {n[5:]}" if len(n) == 10 else str(n)


def _clean_txt(v: Any) -> str:
    """dataset ke purane addresses '!43!jarsat...' jaise separators ko saaf karo (data same rehta hai)."""
    s = re.sub(r"!{2,}", ", ", str(v or ""))
    s = s.replace("!", " ").replace("  ", " ").strip(" ,")
    return s


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _offline_layer(number: str) -> Dict[str, Any]:
    """L2 — 0 ms, koi network nahi: carrier / circle / timezone / type."""
    out: Dict[str, Any] = {}
    try:
        import phonenumbers
        from phonenumbers import carrier, geocoder, timezone as _tz
        n = phonenumbers.parse("+91" + number, None)
        if phonenumbers.is_valid_number(n):
            out["Connection"] = carrier.name_for_number(n, "en") or ""
            out["Mobile State"] = geocoder.description_for_number(n, "en") or ""
            tzs = _tz.time_zones_for_number(n)
            if tzs:
                out["Time Zone"] = tzs[0]
            out["Series"] = "Mobile"
    except Exception:
        pass
    return {k: v for k, v in out.items() if v}


def _num_layer(number: str) -> Dict[str, Any]:
    """L3 — apne gateway ka `num` tool: naam/pita/address/alt."""
    try:
        r = get_session().get(API_URL, params={"key": API_KEY, "tool": "num", "term": number},
                              timeout=(4, 12))
        j = r.json() if r.status_code == 200 else {}
        d = (j.get("data") or {}) if isinstance(j, dict) else {}
        out = {}
        if d.get("owner_name"):
            out["Owner Name"] = d["owner_name"]
        if d.get("father_name"):
            out["Father Name"] = d["father_name"]
        if d.get("address"):
            out["Owner Address"] = d["address"]
        if d.get("circle"):
            out.setdefault("Mobile State", d["circle"])
        if d.get("alt_mobile"):
            out["Alt Number"] = d["alt_mobile"]
        return out
    except Exception:
        return {}


def _lookup_impl(term: Any) -> Dict[str, Any]:
    """Asli lookup (bina cache) — neeche with_cache isse wrap karta hai."""
    number = clean_number(term)
    if not number:
        return {"ok": False, "error": "10 digit Indian mobile number bhejo — jaise 9492915392"}
    if not API_KEY:
        return {"ok": False, "error": "gateway key set nahi hai (owner: Render env MYNUM_API_KEY)"}

    t0 = time.time()
    try:
        r = get_session().get(API_URL, params={"key": API_KEY, "tool": "tc", "term": number},
                              timeout=TIMEOUT)
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "error": f"network: {str(e)[:100]}"}

    ms = int((time.time() - t0) * 1000)
    if r.status_code in (401, 403):
        return {"ok": False, "error": "lookup service ka access band hai — owner se bolo"}
    if r.status_code == 429:
        return {"ok": False, "error": "thodi der me try karo (limit poori ho gayi)"}
    if r.status_code != 200:
        return {"ok": False, "error": f"service HTTP {r.status_code}"}

    try:
        j = r.json()
    except Exception:
        return {"ok": False, "error": "service se bad jawab aaya"}

    if not isinstance(j, dict):
        return {"ok": False, "error": "service ne ajeeb jawab diya"}

    if not j.get("ok"):
        why = str(j.get("why") or "").lower()
        if why in ("notfound", "not_found"):
            # provider ke paas kuch nahi — L2+L3 se jitna mile de do
            d2 = {}
            d2.update(_offline_layer(number))
            d2.update(_num_layer(number))
            if d2:
                d2["Number"] = number
                return {"ok": True, "number": number, "data": d2, "ms": ms,
                        "cached": False, "source": "local-fallback",
                        "layers": ["offline", "num"], "partial": True,
                        "note": "rich provider ke paas is number ki info nahi — basic info de raha hoon"}
            return {"ok": False, "not_found": True,
                    "error": "Is number ki koi public info nahi mili.", "ms": ms}
        if why in ("timeout", "upstream", "credits"):
            # provider down/quota — L2+L3 khali haath nahi laautne deta
            d2 = {}
            d2.update(_offline_layer(number))
            d2.update(_num_layer(number))
            if d2:
                d2["Number"] = number
                return {"ok": True, "number": number, "data": d2, "ms": ms,
                        "cached": False, "source": "local-fallback",
                        "layers": ["offline", "num"], "partial": True,
                        "note": "rich provider abhi down hai — ye basic info hai, /tc me 'Live refresh' se dobara try"}
            return {"ok": False, "error": "service se jawab nahi aaya — thodi der baad try karo", "ms": ms}
        return {"ok": False, "error": (str(j.get("msg") or why or "lookup fail"))[:160], "ms": ms}

    d = j.get("data")
    if not isinstance(d, dict) or not d:
        return {"ok": False, "not_found": True, "error": "Is number ki koi public info nahi mili.", "ms": ms}

    d = dict(d)
    layers = ["provider"]

    def _masked(v) -> bool:
        return str(v or "").count("*") >= 3          # provider ka "****** (enquire)" jaisa value

    for key, val in _offline_layer(number).items():
        if not d.get(key):
            d[key] = val
            layers.append("offline")
    for key, val in _num_layer(number).items():
        cur = d.get(key)
        # khaali field bharo + masked-junk (******) ko clean local value se REPLACE karo
        if not cur or (_masked(cur) and not _masked(val)):
            d[key] = val
            layers.append("num")
    d.setdefault("Number", number)

    return {"ok": True, "number": number, "data": d, "ms": ms,
            "cached": bool(j.get("cached")), "source": j.get("source") or "gateway",
            "layers": sorted(set(layers))}


# 12h ok / 24h tak notfound bhi yaad rakho (provider slow hai — repeat 0 ms)
lookup_truecaller = with_cache("truecaller", 43200, 86400)(_lookup_impl)


def lookup_truecaller_fresh(term: Any) -> Dict[str, Any]:
    """Cache bypass — '🔁 Live refresh' button isi ko chalata hai."""
    return _lookup_impl(term)


def _pick(d: Dict[str, Any], names) -> Any:
    for n in names:
        if n in d and d[n] not in (None, "", [], {}):
            return d[n]
    return ""


def _risk_badge(d: Dict[str, Any]) -> str:
    """Complaints field se honest risk line (sirf provider ke apne data se)."""
    raw = str(_pick(d, ("Complaints", "complaints", "reports")) or "")
    m = re.search(r"\d+", raw)
    if not raw:
        return ""
    n = int(m.group()) if m else 0
    if n == 0:
        return "🛡 <b>Risk:</b> ✅ Clean — koi complaint nahi"
    if n <= 3:
        return f"🛡 <b>Risk:</b> ⚠️ Low — {n} reports"
    if n <= 10:
        return f"🛡 <b>Risk:</b> ⚠️ Medium — {n} reports"
    return f"🛡 <b>Risk:</b> 🚨 High — {n} reports"


def format_truecaller_card(result: Dict[str, Any]) -> str:
    """📱 TRUE CALLER PRO card — sections wala design, baaki cards se match."""
    if not result.get("ok"):
        return f"❌ <b>{_e(result.get('error') or 'lookup fail')}</b>"

    d = result.get("data") or {}
    number = str(result.get("number") or "")

    L = [box("📱", "TRUE CALLER — PRO"), ""]
    L.append(f"🔢 <b>Number:</b> <code>{_e(pretty_number(number))}</code>\n")

    shown = set()
    for head, rows in _SECTIONS:
        sec = []
        for icon, label, keys in rows:
            val = _pick(d, keys)
            if isinstance(val, (list, tuple)):
                val = ", ".join(str(x) for x in val if str(x).strip())
            val = str(val or "").strip()
            if not val or val.lower() in ("n/a", "na", "none", "null", "-"):
                continue
            if label in shown:
                continue
            shown.add(label)
            # Number field section me dobara nahi — upar formatted dikha chuke
            if label == "Number":
                continue
            if label in ("Hometown", "Locations", "Owner Address", "State"):
                val = _clean_txt(val)
            sec.append(field(icon, label, val[:300]))
        if sec:
            L.append(subhead(head))
            L.append("\n".join(sec))
            L.append("")

    # offline layer ke extra facts (timezone / type)
    extra_facts = []
    if d.get("Time Zone"):
        extra_facts.append(field("🕐", "Time Zone", d["Time Zone"]))
    if d.get("Series"):
        extra_facts.append(field("📶", "Line Type", d["Series"]))
    if d.get("Alt Number"):
        extra_facts.append(field("☎️", "Alt Number", d["Alt Number"]))
    if extra_facts:
        L.append(subhead("➕ MORE"))
        L.append("\n".join(extra_facts))
        L.append("")

    # jo provider ne naya field bheja aur humne map nahi kiya — chhupa nahi rahe
    _mapped = set()
    for _head, _rows in _SECTIONS:
        for _icon, _label, _keys in _rows:
            _mapped.update(_keys)
    rest = []
    for k, v in d.items():
        if k in _SKIP_KEYS or not v or isinstance(v, (dict, list)):
            continue
        if k in _mapped:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("n/a", "na", "none", "-"):
            rest.append(f"• <b>{_e(k)}:</b> {_e(s[:160])}")
        if len(rest) >= 5:
            break
    if rest:
        L.append(subhead("🔎 PROVIDER EXTRA"))
        L.append("\n".join(rest))
        L.append("")

    risk = _risk_badge(d)
    if risk:
        L.append(sep())
        L.append(risk)

    L.append("")
    L.append(sep())
    layers = result.get("layers") or []
    badge = "⚡ <b>instant (cache)</b>" if result.get("cached") else "🌐 live"
    lay = " + ".join(layers) if layers else "gateway"
    L.append(f"<i>{badge} · data: {lay} · public lookup sources ka combined result "
             f"(koi private DB nahi) · @ANONYMOUS_XH</i>")
    L.append(footer(result.get("ms")))
    return "\n".join(L)
