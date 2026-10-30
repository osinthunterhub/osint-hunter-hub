"""
👪 FAMILY INFO - BEST - OSINT Hunter Hub
API: utility-api-vu83 + supabase fallback
Aadhaar → Ration Card + Family Members
"""

import os
import re
import time
import fasthttp as requests
from .fast_cache import get_session, api_cache_get, api_cache_set

from modules.cardstyle import box, field, sep, footer

API_URL = os.getenv("FAMINFO_API_URL", "https://utility-api-v2.onrender.com/api").strip()
API_KEY = os.getenv("FAMINFO_API_KEY", "").strip()
TIMEOUT = (5, 20)  # (connect5s, read20s) — SPEED v2.12.2

BUILTIN_URL = "https://jgvicvhgfwcfndgookul.supabase.co/functions/v1/bot-api"
BUILTIN_KEY = "Demo"

NUM_RE = re.compile(r"\D")

def _clean_aadhaar(aadhaar: str) -> str:
    digits = NUM_RE.sub("", str(aadhaar))
    return digits

def _mask_aadhaar(aadhaar: str) -> str:
    d = _clean_aadhaar(aadhaar)
    if len(d) == 12:
        return f"XXXX-XXXX-{d[-4:]}"
    return "XXXX-XXXX-XXXX"

def _normalize_ration(d: dict) -> dict:
    """v2.8: sarkari ?=ration data -> format_family_card ka contract.
    Khaali/fake aadhaar (sab N/A + 0 members) par {} (not found)."""
    def clean(v):
        s = str(v or "").strip()
        return "" if s.lower() in ("n/a", "na", "none", "null", "-", "0") else s
    out = {}
    card = clean(d.get("ration_card_number"))
    scheme = clean(d.get("scheme"))
    st = clean(d.get("state"))
    dist = clean(d.get("district"))
    fps = clean(d.get("fps_id"))
    sd = " / ".join(x for x in (st, dist) if x)
    try:
        tm = int(d.get("total_members") or 0)
    except Exception:
        tm = 0
    names = []
    mem = d.get("members")
    if isinstance(mem, list):
        for m in mem:
            if isinstance(m, dict):
                nm = clean(m.get("name") or m.get("member_name") or m.get("full_name") or m.get("member_name_english"))
            else:
                nm = clean(m)
            if nm and nm not in names:
                names.append(nm)
    head = clean(d.get("head_of_family"))
    if head and head not in names:
        names.insert(0, head)
    # fake aadhaar = members empty + state/district/fps sab N/A
    if not names and tm <= 0 and not sd and not fps:
        return {}
    if card:
        out["card_number"] = card
    if scheme:
        out["card_type"] = scheme
    if sd:
        out["state_dist"] = sd
    if fps:
        out["fps_id"] = fps
    if names:
        out["name"] = names[0]
        for i, nm in enumerate(names[1:9], 1):
            out[f"name_{i}"] = nm
    out["family_count"] = tm if tm > 0 else len(names)
    return out

def lookup_family(aadhaar: str) -> dict:
    start = time.time()
    aadhaar_clean = _clean_aadhaar(aadhaar)
    
    if len(aadhaar_clean) != 12:
        return {
            "ok": False,
            "error": "Aadhaar must be 12 digits",
            "masked": _mask_aadhaar(aadhaar),
            "ms": int((time.time() - start) * 1000)
        }
    
    # Cache check - ULTRA FAST
    cached = api_cache_get("family", aadhaar_clean)
    if cached:
        cached = cached.copy()
        cached["ms"] = 0
        cached["cached"] = True
        return cached
    
    masked = _mask_aadhaar(aadhaar_clean)
    
    # Try primary API with session pooling
    try:
        session = get_session()
        params = {
            "key": API_KEY,
            "tool": "familyinfo",
            "term": aadhaar_clean
        }
        resp = session.get(API_URL, params=params, timeout=TIMEOUT)
        if resp.status_code == 200:
            try:
                data = resp.json()
                # SPEED v2.12.2: gateway honest notfound → sarkari fallback MATT (double query)
                if isinstance(data, dict) and not data.get("success") and str(data.get("why") or "").lower() in ("notfound", "not_found"):
                    nf = {"ok": False, "not_found": True, "error": "No family data found",
                          "masked": masked, "ms": int((time.time() - start) * 1000), "source": "utility-api"}
                    api_cache_set("family", aadhaar_clean, nf, ttl=1800)
                    return nf
                # Check if data has family info
                if data.get("success") and data.get("data"):
                    d = data["data"]
                    # Check if not found
                    if "family_info_not_found" in str(d).lower() or "not_found" in str(d).lower():
                        # Try raw field
                        raw = data.get("raw", "")
                        if "not found" in raw.lower():
                            return {
                                "ok": False,
                                "error": "No family data found",
                                "masked": masked,
                                "ms": int((time.time() - start) * 1000),
                                "raw": data
                            }
                    # Found data
                    result = {
                        "ok": True,
                        "data": d,
                        "masked": masked,
                        "ms": int((time.time() - start) * 1000),
                        "source": "utility-api"
                    }
                    api_cache_set("family", aadhaar_clean, result, ttl=43200)
                    return result
                else:
                    if isinstance(data, dict) and data.get("data"):
                        result = {
                            "ok": True,
                            "data": data["data"],
                            "masked": masked,
                            "ms": int((time.time() - start) * 1000),
                            "source": "utility-api"
                        }
                        api_cache_set("family", aadhaar_clean, result, ttl=43200)
                        return result
            except Exception as e:
                print(f"Family primary JSON parse error: {e}")
                pass
        # Cache not found? Try parsing anyway
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("data"):
                result = {
                    "ok": True,
                    "data": data["data"],
                    "masked": masked,
                    "ms": int((time.time() - start) * 1000),
                    "source": "utility-api"
                }
                api_cache_set("family", aadhaar_clean, result, ttl=43200)
                return result
        except:
            pass
    except Exception as e:
        print(f"Family primary API error: {e}")
    
    # v2.8: purana supabase fallback BAND (host NXDOMAIN/mar chuka) → sarkari ?=ration direct
    try:
        session = get_session()
        url = (f"https://api.sarkariallupdates.com/api?=ration&key=30day"
               f"&q={aadhaar_clean}&xr={aadhaar_clean}&term={aadhaar_clean}")
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            j = resp.json()
            d0 = j.get("data") if isinstance(j, dict) else None
            if isinstance(j, dict) and j.get("status") and isinstance(d0, dict) and d0:
                out = _normalize_ration(d0)
                if out:
                    result = {
                        "ok": True,
                        "data": out,
                        "masked": masked,
                        "ms": int((time.time() - start) * 1000),
                        "source": "sarkari-api"
                    }
                    api_cache_set("family", aadhaar_clean, result, ttl=43200)
                    return result
    except Exception as e:
        print(f"Family sarkari fallback error: {e}")
    
    return {
        "ok": False,
        "error": "No data found or API down",
        "masked": masked,
        "ms": int((time.time() - start) * 1000)
    }

def format_family_card(result: dict) -> str:
    """👪 FAMILY INFO — owner signature design (box + bold title + spaced fields)."""
    import html
    if not result.get("ok"):
        masked = result.get("masked", "XXXX-XXXX-XXXX")
        L = [box("👪", "FAMILY INFO"), ""]
        L.append(f"🔒 <b>Aadhaar:</b> <code>{html.escape(str(masked))}</code>\n\n")
        L.append("❌ No records found for this Aadhaar in any database.\n\n")
        L.append("📭 Database me iska koi record nahi hai.\n\n")
        L.append(sep())
        L.append(footer(result.get("ms", 0)))
        return "".join(L)

    data = result.get("data", {})
    masked = result.get("masked", "XXXX-XXXX-XXXX")
    ms = result.get("ms", 0)

    card_number = data.get("card_number") or data.get("rc_number") or data.get("ration_card") or ""
    card_type = data.get("card_type") or data.get("cardtype") or ""
    state_dist = data.get("statedist") or data.get("state_dist") or data.get("district") or ""
    fps_id = data.get("fps_id") or ""
    fps_name = data.get("fps_name") or data.get("fps") or ""
    family_count = data.get("family_count") or data.get("members") or ""
    full_address = data.get("full_address") or data.get("address") or ""
    ekyc = data.get("ekyc_status") or data.get("ekyc") or ""

    members = []
    for k in sorted(data.keys()):
        if k.startswith("name"):
            v = data.get(k)
            if v and str(v).strip() and str(v).lower() not in ["", "na", "n/a", "none"]:
                if str(v).strip() not in members:
                    members.append(str(v).strip())
    if not members and data.get("name"):
        members = [str(data.get("name")).strip()]

    L = [box("👪", "FAMILY INFO — RATION CARD"), ""]
    L.append(f"🔒 <b>Aadhaar:</b> <code>{html.escape(str(masked))}</code>\n\n")
    L.append(field("💳", "Ration Card No", str(card_number)[:30]))
    L.append(field("📋", "Card Type", card_type))
    L.append(field("🌍", "State/District", str(state_dist)[:100]))
    if fps_id or fps_name:
        fps = ""
        if fps_id:
            fps += f"ID: {html.escape(str(fps_id)[:20])}"
        if fps_name:
            fps += (f" · {html.escape(str(fps_name)[:80])}" if fps_id else html.escape(str(fps_name)[:80]))
        L.append(field("🏪", "FPS Shop", fps))
    L.append(field("👥", "Family Members", family_count))
    L.append(field("🏠", "Address", str(full_address)[:300]))
    if members:
        L.append(f"👨‍👩‍👧‍👦 <b>Members ({len(members)}):</b>\n\n")
        for i, m in enumerate(members[:10], 1):
            L.append(f"   {i}. {html.escape(m[:60])}\n")
        L.append("\n")
    L.append(field("✅", "eKYC", str(ekyc)[:50]))
    L.append(sep())
    L.append(f"📡 <b>Source:</b> {html.escape(str(result.get('source', 'api')))}\n")
    L.append(f"🔒 <b>Privacy:</b> Full Aadhaar never shown, only {html.escape(str(masked))}\n\n")
    L.append(footer(ms))
    return "".join([x for x in L if x])
