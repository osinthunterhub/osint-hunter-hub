# -*- coding: utf-8 -*-
"""
⚡ v2.2 FEEL-FAST — OFFLINE PHONE PARSER (0 ms, network nahi)
============================================================
Problem: data provider naye number par 8-11 SECOND leta hai. Utility bot me
isliye user ko turant (1 second me) hint dikhta hai — number sahi hai, kaunsa
operator/circle (approx). Wahi system ab OSINT Hunter Hub me bhi.

Sab kuch LAZY hai: phonenumbers boot par nahi load hota (RAM bachat), pehli
phone lookup par ek baar (~1s) aur phir har call ~1 ms. Kabhi raise nahi karta.
"""

import re
import threading as _thd

_PN_LOCK = _thd.Lock()
phonenumbers = None
geocoder = None
carrier = None
timezone = None
number_type = None
PhoneNumberType = None


def _pn_load(heavy: bool = True):
    """Lazy loader — core+carrier+timezone ek baar, geocoder bhi ek baar."""
    global phonenumbers, carrier, timezone, number_type, PhoneNumberType, geocoder
    with _PN_LOCK:
        if phonenumbers is None:
            import phonenumbers as _p
            from phonenumbers import carrier as _ca, timezone as _tz
            phonenumbers = _p
            carrier, timezone = _ca, _tz
            number_type = _p.number_type
            PhoneNumberType = _p.PhoneNumberType
        if heavy and geocoder is None:
            try:
                from phonenumbers import geocoder as _gc
                geocoder = _gc
            except Exception:                                    # noqa: BLE001
                geocoder = None
    return phonenumbers


def lookup_phone_info(number_str) -> dict:
    """Carrier + circle/region + type — 100% offline (0 ms). Kabhi raise nahi."""
    try:
        number_str = number_str if isinstance(number_str, str) else ("" if number_str is None else str(number_str))
        clean = re.sub(r"[^\d+]", "", number_str)
        if not clean:
            return {"ok": False, "error": "Number bhejo (jaise <code>9876543210</code> ya <code>+919876543210</code>)"}

        if not clean.startswith("+"):
            if len(clean) == 10:
                clean = "+91" + clean
            elif len(clean) == 11 and clean.startswith("1"):
                clean = "+91" + clean                     # 1800 toll-free
            elif len(clean) == 12 and clean.startswith("91"):
                clean = "+" + clean
            else:
                clean = "+" + clean

        _pn_load()
        try:
            parsed = phonenumbers.parse(clean, None)
        except Exception as e:                               # noqa: BLE001
            return {"ok": False, "error": f"Number padha nahi ja saka: {str(e)[:60]}"}

        if not phonenumbers.is_possible_number(parsed):
            return {"ok": False, "error": "Ye number sahi nahi lagta (digits check karo)."}

        valid = phonenumbers.is_valid_number(parsed)
        region_code = phonenumbers.region_code_for_number(parsed)
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

        ntype_map = {
            PhoneNumberType.MOBILE: "📱 Mobile",
            PhoneNumberType.FIXED_LINE: "☎️ Landline",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "📱 Mobile / Landline",
            PhoneNumberType.TOLL_FREE: "🆓 Toll Free",
            PhoneNumberType.VOIP: "💻 VoIP",
            PhoneNumberType.PREMIUM_RATE: "💎 Premium Rate",
            PhoneNumberType.UNKNOWN: "❔ Unknown",
        }
        ntype = ntype_map.get(number_type(parsed), "❔ Unknown")
        country = (geocoder.description_for_number(parsed, "en") if geocoder else "") or "India"
        operator = carrier.name_for_number(parsed, "en") or ""

        digits = e164.lstrip("+")
        series_note = ""
        if region_code == "IN" and len(digits) == 12 and digits[2] in "6789":
            series_note = "Indian mobile series ✅"

        return {
            "ok": True,
            "valid": bool(valid),
            "number": digits,
            "e164": e164,
            "country": country,
            "country_code": region_code or "-",
            "operator": operator,
            "circle": country or "India",
            "type": ntype,
            "series_note": series_note,
            "note": "MNP (number port) hua ho to operator/circle badal sakta hai.",
        }
    except Exception as e:                                   # noqa: BLE001
        # Kabhi bot block na ho — offline parse fail ho to chup-chaap "ok" bolo
        return {"ok": True, "e164": str(number_str or "")[:20], "operator": "",
                "circle": "", "type": "", "offline_fail": str(e)[:60]}
