# -*- coding: utf-8 -*-
"""
🔌 USER API ENGINE — 🚗 RC + Challan   [v110]
===========================================================================
Ye tool user ki APNI API par chalta hai. Default pehle se laga hua hai:

    GET https://jgvicvhgfwcfndgookul.supabase.co/functions/v1/bot-api
        ?key=<MYNUM_API_KEY>&tool=vehicle&term={gaadi-number}

(Yahi host NUMBER INFO aur FAMILY INFO bhi use karte hain.)

Render me kuch daalna ZAROORI NAHI — par badalna ho to:
    RC_API_URL / RC_API_KEY / RC_API_TIMEOUT
Aur agar API bilkul alag tarah maange to ye optional knobs:
    RC_API_TOOL · RC_API_PARAM · RC_API_KEYPARAM · RC_API_HEADERS · RC_API_PATH

---------------------------------------------------------------------------
Response ka koi bhi shape chalega
---------------------------------------------------------------------------
  • nested  {"data":{"owner":{"name":"..."}}}        ✅
  • flat    {"owner_name":"..."}                      ✅
  • list    {"result":[{...}]}                        ✅
  • ajeeb naam (ownerName / owner_nm / OwnerFullName) ✅
  • sirf text  {"raw":"...poora result text..."}      ✅

`_flatten()` poore JSON ko khol deta hai, `_pick()` har field ke 8-10 naam
try karta hai. Jo field phir bhi na pehchana jaye wo "Baaki details" me
jyon-ka-tyon dikh jata hai — API ka koi data kabhi chhupta nahi.

RAM cost = +0 MB (sirf re/os/json + pehle se maujood modules.core.net).
"""


from __future__ import annotations

import contextlib
import os
import re
import threading

from modules.core.net import NetError, http_get

# ---------------------------------------------------------------- cache
try:
    from modules.core.cache import TTLCache as _TTLCache
    _INFO = _TTLCache(maxsize=1024, default_ttl=3600)
    _CACHED = True
except Exception:                                         # pragma: no cover
    _INFO = None
    _CACHED = False


def clear_cache() -> None:
    if _CACHED:
        try:
            _INFO.clear()
        except Exception:
            pass


# v117 (speed): "no record found" / "maintenance" jaisa jawab bhi cache karo.
# Pehle sirf SAFAL result cache hota tha, isliye wahi galat number dubara
# bhejne par har baar provider ko 7-8 second lagte the. Ab short TTL ka
# negative-cache hai — turant jawab, provider par bhi kam load.
_NEG_TTL = 180          # 3 minute (success ka 3600 hai)

# provider har tool me alag likhta hai: "No Number found", "No data found",
# "No record found" — sabko ek hi regex se pakad lo.
_NOTFOUND_RE = re.compile(r"no\s+[a-z ]{1,24}?\s*found", re.I)

# ════════════════════════════════════════════════════════════════════
# 🛡️ v118 — PROVIDER DATA-MIXING GUARD
# ════════════════════════════════════════════════════════════════════
# 11 Oct 2026 ko test karte waqt ek BAHUT badi gadbad pakdi gayi.
#
# Jab provider ko kai request EK SAATH bheji jaati hain, to wo jawab
# aapas me mila deta hai. Asli naapa hua nateeja (6 parallel request):
#
#   maanga telegramtonumber  ->  mila PAKISTAN ka data
#   maanga advpan            ->  mila PAKISTAN ka data
#
# Response ke `tool` aur `query` field to sahi aate hain (wo sirf echo
# hote hain), par andar ka ASLI `data` kisi dusre ke request ka hota hai.
# Matlab bot par 2 log ek saath alag tool chalayein to EK KO DUSRE KA
# RESULT dikh sakta hai. Ye privacy ka bhi masla hai aur galat jawab ka bhi.
#
# Bachav teen parton me:
#   1. _provider_slot() — apni requests ek-ek karke jati hain, to apne
#      hi do user aapas me kabhi nahi takrayenge.
#   2. _wrong_tool()    — phir bhi agar jawab kisi DUSRE tool ka nikla
#      (koi aur client takra gaya), to pakad lo.
#   3. lookup() me ek baar dobara koshish; tab bhi galat to saaf error.
#      Galat data user ko kabhi nahi dikhaya jayega.

# Har tool ka apna pehchaan-chinh (11 Oct 2026 ko live naapa hua).
_SIG = {
    # Ye do userapi ke tools nahi hain, par provider inhe bhi serve karta
    # hai — isliye inke chinh bhi jaanna zaroori hai, warna inka data
    # chupke se kisi aur tool ke card me ghus sakta hai.
    "familyinfo": re.compile(r"family[\s_]*info", re.I),
    "numinfo":    re.compile(r"number[\s_]*info", re.I),
}

_PROVIDER_LOCK = threading.Lock()
_LOCK_WAIT = 15.0        # itna ruk kar bhi na mile to aage badh jao
# (bina-rukawat chalne par bhi _wrong_tool ka guard to lagega hi —
#  yaani bot kabhi hamesha ke liye atkega nahi, bas suraksha ek
#  parat kam ho jayegi. Worst case: 15 + 22 + 15 + 22 = ~74s,
#  isliye bot.py me asyncio guard 75s rakha gaya hai.)


@contextlib.contextmanager
def _provider_slot():
    """Provider ko ek waqt par sirf EK request — data mixing na ho."""
    got = _PROVIDER_LOCK.acquire(timeout=_LOCK_WAIT)
    try:
        yield got
    finally:
        if got:
            _PROVIDER_LOCK.release()


def _wrong_tool(kind: str, dblock, rawtxt: str) -> bool:
    """
    True sirf tab jab jawab SAAF TAUR PAR kisi DUSRE tool ka ho.

    Jaan-boojh kar bahut hi ehtiyat se likha hai: agar jawab me kisi bhi
    tool ka chinh nahi milta (naya/anjaan format), to use SAHI maan lete
    hain. Isse kabhi koi sahi jawab galti se block nahi hoga.
    """
    keys = ""
    if isinstance(dblock, dict):
        keys = " ".join(str(k) for k in dblock)
    elif isinstance(dblock, list):
        keys = " ".join(str(k) for d in dblock if isinstance(d, dict) for k in d)
    blob = f"{rawtxt or ''} {keys}".strip()
    if not blob:
        return False                       # khali jawab — yahan ka kaam nahi

    mine = _SIG.get(kind)
    if mine and mine.search(blob):
        return False                       # apna hi chinh mila, sab theek

    for other, rx in _SIG.items():
        if other != kind and rx.search(blob):
            return True                    # kisi DUSRE tool ka nikla 🚨
    return False


UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}

# =====================================================================
#  TOOL REGISTRY
# =====================================================================
# Aapka provider (NUMBER INFO / FAMILY INFO wahi host use karte hain)
# v2.17: ye supabase host MAR chuka hai (NXDOMAIN / "unknown tool").
# Pehle /rc har baar isi par jaata tha -> tool toota hua tha + 5s waste.
# Ab default = owner ka apna gateway.
_BUILTIN_URL = (os.environ.get("RC_API_URL") or "https://utility-api-v2.onrender.com/api").strip()
_BUILTIN_KEY = (os.environ.get("RC_API_KEY") or os.environ.get("GATEWAY_KEY") or os.environ.get("MYNUM_API_KEY") or "").strip()

TOOLS = {
    "rc": {
        "env": "RC",
        "icon": "🚗",
        "label": "RC + CHALLAN",
        "tool": "lynxosint__vehicle",
        "url": _BUILTIN_URL,
        "key": _BUILTIN_KEY,
        "example": "BR30AR0802",
        "what": "gaadi number",
    },
    # v127 — NEW TOOLS via own gateway (vehicle_full primary)
    "vehicletomobile": {
        "env": "VEHICLETOMOBILE",
        "icon": "📲",
        "label": "VEHICLE TO NUMBER",
        "tool": "vehicletomobile",
        "url": _BUILTIN_URL,
        "key": _BUILTIN_KEY,
        "example": "KL41V3504",
        "what": "gaadi number se mobile",
    },
    "aadhaar2num": {
        "env": "AADHAAR2NUM",
        "icon": "🪪",
        "label": "AADHAAR TO NUMBER",
        "tool": "aadhaar2num",
        "url": _BUILTIN_URL,
        "key": _BUILTIN_KEY,
        "example": "401635555849",
        "what": "Aadhaar number se mobile list",
    },
}


def _env(kind: str, name: str, default: str = "") -> str:
    pre = TOOLS[kind]["env"]
    return (os.environ.get(f"{pre}_API_{name}") or default).strip()


def api_url(kind: str) -> str:
    """Render me RC_API_URL set ho to wo, warna built-in default."""
    return _env(kind, "URL", TOOLS[kind].get("url", ""))


def api_key(kind: str) -> str:
    return _env(kind, "KEY", TOOLS[kind].get("key", ""))


def api_timeout(kind: str) -> int:
    """v117 (speed): default 40 -> 22 second.

    Naap kar dekha (11 Oct 2026): ye provider 7-8 second me jawab de deta
    hai. 40s ka intezaar sirf tab kaam aata tha jab API already mar chuki
    ho — aur tab user ko bekaar me 40s (+ retry ke saath 80s!) rukna padta
    tha. 22s me asli jawab aaram se aa jata hai, aur fail hone par user ko
    teen guna jaldi pata chal jata hai.
    """
    try:
        return max(5, min(90, int(_env(kind, "TIMEOUT", "22") or 22)))
    except Exception:
        return 22


def is_configured(kind: str) -> bool:
    """API lagi hui hai ya nahi — URL hona zaroori hai."""
    return bool(api_url(kind))


# =====================================================================
#  INPUT VALIDATION
# =====================================================================
def clean_plate(plate) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(plate or "")).upper()


def validate_plate(text) -> tuple:
    """(ok, clean, error)"""
    c = clean_plate(text)
    if len(c) < 6 or len(c) > 11:
        return False, c, ("Gaadi number 6 se 11 character ka hota hai.\n"
                          "📌 Jaise: <code>BR01AB1234</code> · <code>DL8CAF5030</code>")
    if not re.match(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{1,4}$", c):
        return False, c, ("Gaadi number ka format galat lag raha hai.\n"
                          "📌 Sahi: <b>2 letter + 1-2 digit + 1-3 letter + 4 digit</b>\n"
                          f"📌 Jaise: <code>BR01AB1234</code>\nAapne bheja: <code>{c}</code>")
    return True, c, ""


try:
    from modules.core.nodata import no_data as _no_data
except Exception:                                                # noqa: BLE001
    def _no_data(term="", what=""):                              # fallback
        return "❌ <b>Data not found in any database.</b>"

def _digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def validate_aadhaar2num(text) -> tuple:
    """Aadhaar 12 digit"""
    d = re.sub(r"\D", "", str(text or ""))
    if len(d) != 12:
        return False, d, (f"Aadhaar 12 digit ka hota hai, aapne {len(d)} bheje.\n📌 Jaise: <code>401635555849</code>")
    return True, d, ""

def validate_vehicletomobile(text) -> tuple:
    """Vehicle RC number validation (same as plate)"""
    return validate_plate(text)


def validate_dl(text) -> tuple:
    """DLNUMBER DD-MM-YYYY"""
    t = re.sub(r"\s+", " ", str(text or "").strip().upper())
    if re.match(r"^[A-Z]{2}[0-9]{0,2}[ A-Z0-9]{3,18} [0-9]{2}-[0-9]{2}-[0-9]{4}$", t):
        return True, t, ""
    return False, t, "Format: DLNUMBER DD-MM-YYYY — jaise: <code>UP25 20210027727 01-01-2001</code>"

def validate_pan(text) -> tuple:
    t = re.sub(r"[^A-Z0-9]", "", str(text or "").strip().upper())
    if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", t):
        return True, t, ""
    return False, t, "PAN 10 character ka hota hai — jaise: <code>AAAPL1234C</code>"

def validate_gstin(text) -> tuple:
    t = re.sub(r"[^A-Z0-9]", "", str(text or "").strip().upper())
    if re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$", t):
        return True, t, ""
    return False, t, "GSTIN 15 character ka hota hai — jaise: <code>27AAPFU0939F1ZV</code>"

def validate_imei(text) -> tuple:
    d = re.sub(r"\D", "", str(text or ""))
    if len(d) == 15:
        return True, d, ""
    return False, d, "IMEI 15 digit ka hota hai — jaise: <code>490154203237518</code>"

_VALIDATORS = {
    "rc": validate_plate,
    "vehicletomobile": validate_vehicletomobile,
    "aadhaar2num": validate_aadhaar2num,
    "dl": validate_dl,
    "fastag": validate_vehicletomobile,
    "imei": validate_imei,
    "aadhaar2pan": validate_aadhaar2num,
    "pantogst": validate_pan,
    "gst": validate_gstin,
}

def _gw3(kind: str, clean: str):
    """v2.21 — DL / FASTAG / IMEI gateway tools (multi-source chains wale)."""
    import time as _t
    t0 = _t.time()
    try:
        url = f"{_GATEWAY_BASE}/api"
        params = {"key": _GATEWAY_KEY, "tool": kind, "term": clean}
        with _provider_slot():
            r = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=(5, 55), retries=0)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok"):
                data = j.get("data") or {}
                ms = int((_t.time() - t0) * 1000)
                fields = []
                for k, v in data.items():
                    if isinstance(v, (str, int, float)) and str(v).strip():
                        lbl = k.replace("_", " ").strip().title()
                        val = str(v)
                        if len(val) > 90: val = val[:90] + "…"
                        fields.append((lbl, val))
                    if len(fields) >= 14: break
                return {"ok": True, "term": clean, "data": data, "fields": fields,
                        "extra": {}, "raw_text": "", "ms": ms, "src": "gateway", "gateway": True}
    except Exception:
        pass
    return None


def validate(kind: str, text) -> tuple:
    return _VALIDATORS.get(kind, validate_plate)(text)


# =====================================================================
#  JSON → flat dict  (kisi bhi shape ka API chalega)
# =====================================================================
def _norm(k) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _flatten(d, out=None, depth=0, parent=""):
    """
    Poore JSON ko ek flat dict me khol do.

    Har value DO naam se rakhi jaati hai:
      1. apna naam        → "validity"
      2. parent + apna    → "insurancevalidity"
    Isse `{"insurance":{"validity":"2026-11-01"}}` bhi "insurancevalidity"
    se match ho jata hai — warna wo field pehchani hi nahi jaati thi.
    """
    out = {} if out is None else out
    if depth > 6 or not isinstance(d, (dict, list)):
        return out
    if isinstance(d, list):
        for x in d[:5]:
            _flatten(x, out, depth + 1, parent)
        return out
    for k, v in d.items():
        kk = _norm(k)
        if isinstance(v, (dict, list)):
            # "data"/"result" jaise wrapper key ko parent mat banao
            nxt = "" if kk in _WRAPPERS else kk
            _flatten(v, out, depth + 1, nxt)
        elif v not in (None, "", [], {}):
            out.setdefault(kk, v)
            if parent:
                out.setdefault(parent + kk, v)
    return out


# Ye keys sirf "dabba" hoti hain — inka naam field ke naam me nahi jodna
_WRAPPERS = {"data", "result", "results", "response", "payload", "body",
             "details", "detail", "info", "output", "record", "records",
             "header", "items", "item", "vehicle", "device"}

_JUNK = {"", "null", "none", "na", "n/a", "-", "nil", "undefined", "false",
         "0000-00-00", "success", "ok", "true", "1", "found"}


def _pick(flat: dict, *names) -> str:
    for n in names:
        v = flat.get(_norm(n))
        if v is None:
            continue
        s = str(v).strip()
        if s.lower() not in _JUNK:
            return s
    return ""


# Field maps — har field ke liye jitne naam ho sakte hain
_RC_FIELDS = [
    ("reg_no",     "🔢 Number",        ("regno", "registrationno", "registrationnumber", "rcnumber", "vehiclenumber", "number", "regnumber", "licenseplate", "vehicleregno")),
    ("owner",      "👤 Owner",         ("owner", "ownername", "owner_name", "name", "ownerfullname")),
    ("father",     "👨 Father",        ("father", "fathername", "father_name", "ownerfather", "fatherhusband")),
    ("model",      "🚘 Model",         ("model", "vehiclemodel", "modelname", "makermodel", "maker_model")),
    ("maker",      "🏭 Maker",         ("maker", "makerdescription", "manufacturer", "brand", "makername")),
    ("vclass",     "🚙 Class",         ("vehicleclass", "class", "vclass", "type", "vehicletype", "bodytype")),
    ("fuel",       "⛽ Fuel",          ("fuel", "fueltype", "fuel_type")),
    ("color",      "🎨 Colour",        ("colour", "color", "vehiclecolour")),
    ("reg_date",   "📅 Reg. date",     ("registrationdate", "regdate", "date_of_registration", "regdt")),
    ("rto",        "🏢 RTO",           ("rto", "rtoname", "registeredat", "office", "rtocode")),
    ("state",      "📍 State",         ("state", "statename", "rtostate")),
    ("chassis",    "🔧 Chassis",       ("chassis", "chassisno", "chassisnumber", "vin")),
    ("engine",     "⚙️ Engine",        ("engine", "engineno", "enginenumber")),
    ("insurance",  "🛡️ Insurance",     ("insuranceupto", "insurancevalidity", "insuranceexpiry", "insurancevalidupto", "insurance", "validity")),
    ("insurer",    "🏦 Insurer",       ("insurancecompany", "insurer", "insurancecomp")),
    ("puc",        "🌱 PUC valid",     ("pucupto", "puc", "pucvalidity", "pucexpiry", "pollutionupto")),
    ("fitness",    "📋 Fitness",       ("fitnessupto", "fitness", "fitnessvalidity", "rcexpiry", "fit_upto")),
    ("tax",        "💰 Tax upto",      ("taxupto", "tax", "taxvalidity")),
    ("financer",   "🏛️ Financer",      ("financer", "financier", "financed", "hypothecation", "loan")),
    ("norms",      "📏 Norms",         ("normsdescription", "norms", "emissionnorms", "bharatstage")),
    ("seats",      "💺 Seats",         ("seatcapacity", "seats", "seating")),
    ("cc",         "🔩 CC",            ("cubiccapacity", "cc", "enginecc", "displacement")),
    ("status",     "✅ Status",        ("status", "rcstatus", "vehiclestatus", "regstatus")),
    ("challan",    "🚨 Challan",       ("challan", "challans", "totalchallan", "challancount", "pendingchallan")),
    ("challan_amt", "💸 Challan amount", ("challanamount", "totalamount", "pendingamount", "fineamount", "amount")),
]


# =====================================================================
#  v116 — naye tools ke field maps
# ---------------------------------------------------------------------
#  Provider ka JSON har tool me thoda alag hota hai, aur kabhi-kabhi wo
#  poora result ek hi text blob (`raw`) me bhej deta hai. Isliye:
#    • yahan jitne naam ho sakte hain sab alias me daal diye hain, aur
#    • field na bane to lookup() `raw_text` laut deta hai, jise card
#      <pre> block me dikha deta hai — data kabhi gayab nahi hota.
# =====================================================================
_VEHICLETOMOBILE_FIELDS = [
    ("mobile",   "📱 Number",       ("mobile", "mobileno", "number", "phone", "phoneno", "mobile_number")),
    ("reg",      "🔢 RC",           ("registration_number", "reg_no", "rc", "vehicle_number")),
]

_AADHAAR2NUM_FIELDS = [
    ("phone",    "📱 Phone",        ("phoneNumber", "phone", "mobile", "mobileno")),
    ("name",     "👤 Name",         ("name", "fullName")),
    ("aadhaar",  "🪪 Aadhaar",      ("aadharNumber", "aadhaar")),
]

FIELD_MAP = {
    "rc": _RC_FIELDS,
    "vehicletomobile": _VEHICLETOMOBILE_FIELDS,
    "aadhaar2num": _AADHAAR2NUM_FIELDS,
}


def _err_text(flat: dict) -> str:
    """API ne error bheja ho to usko dhoondo."""
    for n in ("error", "message", "msg", "errormessage", "reason", "detail", "description"):
        v = flat.get(_norm(n))
        if v and str(v).strip().lower() not in _JUNK:
            return str(v).strip()[:200]
    return ""


def _looks_failed(raw, flat: dict) -> bool:
    if isinstance(raw, dict):
        for n in ("success", "status", "ok"):
            v = raw.get(n)
            if v is False:
                return True
            if isinstance(v, str) and v.strip().lower() in ("false", "error", "failed", "fail"):
                return True
    return False


# =====================================================================
#  LOOKUP
# =====================================================================
def lookup(kind: str, term) -> dict:
    """
    kind = "rc"
    Hamesha dict lautata hai — kabhi exception nahi phenkta.
      ok=True  → {"ok":True,"term":..,"fields":[(label,value)..],"extra":{..},"ms":..}
      ok=False → {"ok":False,"error":"<HTML-safe hinglish>"}
    """
    if kind not in TOOLS:
        return {"ok": False, "error": "Internal: unknown tool"}

    # v2.17 SPEED — pehle yahan KOI cache nahi tha: /rc, /v2n, /a2n har baar
    # network par jaate the (4-6s). Ab mem + SQLite cache (restart proof).
    try:
        from modules.fast_cache import api_cache_get as _cg, api_cache_set as _cs
        _ck = f"uapi:{kind}"
        _hit = _cg(_ck, str(term or ""))
        if isinstance(_hit, dict) and _hit.get("ok"):
            _out = dict(_hit)
            _out["cached"] = True
            _out["ms"] = 0
            return _out
    except Exception:
        _cg = _cs = None
    cfg = TOOLS[kind]

    ok, clean, err = validate(kind, term)
    if not ok:
        return {"ok": False, "error": err}

    if not is_configured(kind):
        return {"ok": False, "configured": False,
                "error": (f"{cfg['icon']} <b>{cfg['label']}</b> ki API abhi nahi lagi hai.\n\n"
                          f"Render → Environment me ye do daalo:\n"
                          f"• <code>{cfg['env']}_API_URL</code>\n"
                          f"• <code>{cfg['env']}_API_KEY</code>\n\n"
                          f"<i>Daalte hi ye tool apne aap chalu ho jayega.</i>")}

    hit = _cget_k(kind, clean)
    # v127.11+v127.13b: 7-8 Oct ki window me tgnum ke PUBLIC-card entries cache
    # hue the (30 din TTL). Kuch me src="t.me-public" hai, KUCH me src=None —
    # isliye pakka rule: tgnum cache hit tabhi valid jab usme "📱 Number" field
    # ho. Warna STALE maano aur dobara fetch karo.
    if hit is not None:
        return {**hit, "cached": True}

    # 🚘 v126.9 — RC ab apne gateway se (BR30AR0802 real example fix)
    if kind == "rc":
        _rg = _rc_gateway(clean)
        if _rg is not None:
            if _rg.get("ok"):
                _cput_k(kind, clean, _rg)
            return _rg

    # 📲 v127 — Vehicle to Number via gateway
    if kind == "vehicletomobile":
        _vm = _vehicletomobile_gateway(clean)
        if _vm is not None:
            if _vm.get("ok"):
                _cput_k(kind, clean, _vm)
            return _vm

    # 🪪 v127 — Aadhaar to Number via gateway
    if kind == "aadhaar2num":
        _an = _aadhaar2num_gateway(clean)
        if _an is not None:
            if _an.get("ok"):
                _cput_k(kind, clean, _an)
            return _an

    # 🛡️ v2.21 — DL / FASTAG / IMEI via gateway (auto-failover chains)
    if kind in ("dl", "fastag", "imei", "aadhaar2pan", "pantogst", "gst"):
        _g3 = _gw3(kind, clean)
        if _g3 is not None:
            if _g3.get("ok"):
                _cput_k(kind, clean, _g3)
            return _g3

    url = api_url(kind)
    tmo = api_timeout(kind)
    key = api_key(kind)

    # --- request banao (defaults = NUMBER INFO jaisa pattern) ---------
    params = {}
    keyparam = _env(kind, "KEYPARAM", "key")
    if key and keyparam:
        params[keyparam] = key
    tool_val = _env(kind, "TOOL", cfg["tool"])
    if tool_val:
        params["tool"] = tool_val
    term_param = _env(kind, "PARAM", "term")

    if _env(kind, "PATH", "").lower() in ("on", "1", "yes", "true"):
        url = url.rstrip("/") + "/" + clean          # {URL}/{term} style
    else:
        params[term_param] = clean

    headers = dict(UA_HEADERS)
    raw_h = _env(kind, "HEADERS", "")
    if raw_h:
        for part in raw_h.split("|"):
            if ":" in part:
                hk, hv = part.split(":", 1)
                headers[hk.strip()] = hv.strip().replace("{key}", key)

    import time as _t

    # 🛡️ v118: do koshish tak — agar provider kisi DUSRE tool ka data
    # bhej de (upar _wrong_tool ka note padho) to ek baar dobara maangte
    # hain. Ye SPEED wala retry nahi hai (wo abhi bhi band hai) — ye sirf
    # tab chalta hai jab jawab galat tool ka nikle, jo bahut kam hota hai.
    raw = None
    ms = 0
    for _attempt in (1, 2):
        t0 = _t.time()
        try:
            # v117 (speed): retries=1 ka matlab tha 2 koshish — yaani fail hone
            # par user ko 2 x timeout rukna padta tha (40+40 = 80 second!).
            # Ye provider slow hai par bharosemand — retry se kuch nahi milta,
            # sirf intezaar dugna hota hai. Isliye ab ek hi koshish.
            # v118: ab ye ek "slot" ke andar chalta hai — apne do user
            # ek saath provider ko hit nahi karenge.
            with _provider_slot():
                r = http_get(url, params=params, headers=headers,
                             timeout=tmo, retries=0)
        except NetError:
            return {"ok": False, "error": (f"API ne {tmo} second me jawab nahi diya.\n"
                                           "Thodi der baad dobara try karo.")}
        except Exception as e:
            return {"ok": False, "error": f"API tak pahuncha nahi ja saka: {str(e)[:90]}"}
        ms = int((_t.time() - t0) * 1000)

        if r.status_code == 401 or r.status_code == 403:
            return {"ok": False, "error": (f"API ne key reject kar di (HTTP {r.status_code}).\n"
                                           f"Render me <code>{cfg['env']}_API_KEY</code> check karo.")}
        if r.status_code == 404:
            return {"ok": False, "notfound": True, "error": _no_data(clean)}
        if r.status_code == 429:
            return {"ok": False, "error": "API ki limit khatam ho gayi (HTTP 429). Thodi der baad try karo."}
        if r.status_code >= 400:
            return {"ok": False, "error": (f"API server busy hai (HTTP {r.status_code}). "
                                           "Thodi der baad try karo.")}

        try:
            raw = r.json()
        except Exception:
            body = (r.text or "").strip()
            if not body:
                return {"ok": False, "error": "API ne khali jawab bheja."}
            return {"ok": False,
                    "error": f"API ne JSON nahi bheja. Jawab: <code>{_esc(body[:150])}</code>"}

        # ---- 🛡️ jawab kisi DUSRE tool ka to nahi? --------------------
        if isinstance(raw, dict) and _wrong_tool(kind, raw.get("data"),
                                                 str(raw.get("raw") or "")):
            if _attempt == 1:
                _t.sleep(0.7)          # thoda ruk kar saaf koshish
                continue
            # Dobara bhi galat — ab GALAT DATA DIKHANE SE ACHHA HAI
            # SAAF MANA KAR DENA. Ye cache me bhi nahi jayega.
            return {"ok": False, "mixed": True,
                    "error": ("⚠️ <b>Provider ne galat jawab bheja</b>\n\n"
                              "Unke server par abhi bheed hai aur wo ek tool ka "
                              "data dusre tool me bhej raha hai.\n\n"
                              "Aapko galat jaankari dikhane se achha hai ki main "
                              "mana kar doon — isliye ye lookup roka gaya hai.\n\n"
                              "<i>Thodi der baad dobara try karo.</i>")}
        break

    flat = _flatten(raw)

    # ---- aapke provider ka khaas jawab ------------------------------
    # Wo hamesha HTTP 200 + "success": true bhejta hai, chahe data ho ya na ho.
    # Asli haal `data` (dict) aur `raw` (text) me hota hai:
    #   {"success":true,"data":{},"raw":"🔧 Bot under maintance!", ...}
    _rawtxt = ""
    if isinstance(raw, dict):
        _rawtxt = str(raw.get("raw") or "").strip()
        _dblock = raw.get("data")
        _dempty = (not _dblock) or (isinstance(_dblock, (dict, list)) and len(_dblock) == 0)
        _low = _rawtxt.lower()
        if _dempty and ("maintan" in _low or "maintenan" in _low or "under maint" in _low):
            return _neg(kind, clean, {"ok": False, "maintenance": True,
                    "error": ("🔧 <b>API provider abhi maintenance par hai</b>\n\n"
                              f"Ye aapke bot ki galti nahi hai — jis service se {cfg.get('what', 'ye')} "
                              "ka data aata hai wo khud band hai.\n\n"
                              f"<i>Provider ka message:</i> <code>{_esc(_rawtxt[:120])}</code>\n\n"
                              "Thodi der baad dobara try karo.")})

        # ---- v116: provider ka apna crash -----------------------------
        # Kabhi-kabhi wo khud exception kha jata hai aur usko data me
        # bhej deta hai, jaise:
        #   data: {"api_error": "'list' object has no attribute 'get'"}
        # User ko python ka error dikhana bekar hai — saaf message do.
        _apierr = ""
        if isinstance(_dblock, dict):
            for _k in ("api_error", "apierror", "error"):
                if _dblock.get(_k):
                    _apierr = str(_dblock[_k]).strip()
                    break
        _only_err = (isinstance(_dblock, dict)
                     and bool(_apierr)
                     and all(str(_v).strip() in ("", "None") or _k2 in
                             ("api_error", "apierror", "error")
                             for _k2, _v in _dblock.items()))
        if _apierr and (_only_err or "api error" in _low):
            return _neg(kind, clean, {"ok": False, "provider_error": True,
                    "error": ("⚠️ <b>API provider se jawab theek nahi aaya</b>\n\n"
                              "Ye aapke bot ki galti nahi hai — provider ke "
                              "apne server par dikkat hai.\n\n"
                              f"<i>Unka message:</i> <code>{_esc(_apierr[:120])}</code>\n\n"
                              "Thodi der baad dobara try karo.")})

        # ---- v116: saaf 'record nahi mila' ----------------------------
        # Provider aise bolta hai: "✖️ GST: No data found for 27AAP..." ya
        # "✖️ Pakistan Database: No record found for 0300...". Ye FAIL hai,
        # success nahi — warna card khali aata hai.
        # v117: pehle sirf "no data/record found" pakadte the, par provider
        # har tool me alag shabd likhta hai —
        #   "✖️ No Number found for this Telegram ID"
        #   "✖️ Pakistan Database: No record found for ..."
        # Isliye ab `no <kuch> found` ka regex use karte hain.
        if (_NOTFOUND_RE.search(_low) or "not found" in _low
                or "koi record nahi" in _low):
            return _neg(kind, clean, {"ok": False, "notfound": True,
                    "error": _no_data(clean, cfg.get("what", ""))})

    if not flat and not _rawtxt:
        return {"ok": False, "notfound": True, "error": _no_data(clean, cfg.get("what", ""))}

    if _looks_failed(raw, flat):
        return {"ok": False, "error": _err_text(flat) or f"<code>{clean}</code> ka record nahi mila."}

    # --- pehchane hue fields ------------------------------------------
    fields, used = [], set()
    for _slug, label, names in FIELD_MAP[kind]:
        v = _pick(flat, *names)
        if v:
            fields.append((label, v[:120]))
            for n in names:
                used.add(_norm(n))

    # --- jo bacha, wo bhi dikhao (API ka koi data chhupe na) -----------
    skip = {"key", "tool", "term", "success", "status_code", "statuscode",
            "code", "time", "timestamp", "credits", "message", "msg",
            # aapke provider ka apna junk — user ko dikhane ki zaroorat nahi
            "raw", "credit", "note", "query"}
    shown = {v.strip().lower() for _l, v in fields}      # jo upar dikh chuka
    extra = {}
    for k, v in flat.items():
        if k in used or k in skip:
            continue
        s = str(v).strip()
        if s.lower() in _JUNK or len(s) > 120:
            continue
        if s.lower() in shown:        # wahi value dobara mat dikhao
            continue
        extra[k] = s
        shown.add(s.lower())
        if len(extra) >= 14:
            break

    if not fields and not extra and not _rawtxt:
        _nf = {"ok": False, "notfound": True, "error": _no_data(clean, cfg.get("what", ""))}
        try:
            if _cs:
                _cs(f"uapi:{kind}", clean, _nf, ttl=1800)
        except Exception:
            pass
        return _nf

    out = {"ok": True, "kind": kind, "term": clean,
           "fields": fields, "extra": extra, "ms": ms,
           # kuchh provider poora result ek hi text blob me bhejte hain.
           # Field na bane to bhi ye text user ko dikha denge.
           "raw_text": _rawtxt if (not fields and not extra) else ""}
    _cput_k(kind, clean, out)
    # v2.17 — 12h persistent cache (repeat = 0ms, network hi nahi)
    try:
        if _cs:
            _cs(f"uapi:{kind}", clean, out, ttl=int(os.environ.get("BOT_CACHE_TTL", "43200")))
    except Exception:
        pass
    return out


def _cget_k(kind, clean):
    if _CACHED:
        hit = _INFO.get(f"{kind}:{clean}")
        if hit is not None:
            return hit
    # v127.7 PERSISTENT CACHE: restart/deploy ke baad bhi hit (0 ms)
    try:
        from database import cache_get as _pg
        pc = _pg(f"udb:{kind}:{clean}")
        if isinstance(pc, dict) and pc.get("ok"):
            if _CACHED:
                try:
                    _INFO.put(f"{kind}:{clean}", pc, 21600)
                except Exception:                            # noqa: BLE001
                    pass
            return pc
    except Exception:                                        # noqa: BLE001
        pass
    return None


_GATEWAY_BASE = (os.environ.get("GATEWAY_URL") or os.environ.get("UTILITY_API_URL") or "https://utility-api-v2.onrender.com").strip().rstrip("/")
# v2.17 SECURITY: pehle yahan owner ki ASLI gateway key hardcoded thi aur repo
# PUBLIC hai -> koi bhi copy kar sakta tha. Ab sirf env se (Render dashboard).
_GATEWAY_KEY = (os.environ.get("GATEWAY_KEY") or os.environ.get("MYNUM_API_KEY")
                or os.environ.get("KEY_VEHICLE") or "").strip()
if not _BUILTIN_KEY:                       # v2.17: pehla attempt hi valid key se jaaye
    _BUILTIN_KEY = _GATEWAY_KEY

def _rc_gateway(clean: str):
    """
    🚘 Vehicle & Challan via own gateway v2.6 (vehicle_full primary unmasked + fallback).
    Tries: 1) sarkari vehicle_full direct (unmasked + mobile), 2) gateway vehicle (masked + challan), 3) fallback old provider via lookup().
    """
    import time as _t
    t0 = _t.time()
    # 1) Try sarkari vehicle_full direct for unmasked data (KL41V3504)
    try:
        full_url = f"https://api.sarkariallupdates.com/api?=vehicle_full&key=30day&xr={clean}"
        with _provider_slot():
            r = http_get(full_url, headers=dict(UA_HEADERS), timeout=12, retries=0)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("success"):
                # Build data dict compatible with vehicle_rc_card
                data = {
                    "registration_number": j.get("registration_number") or j.get("rc_number") or clean,
                    "owner_name": j.get("owner_name") or "",
                    "owner_father_name": j.get("owner_father_name") or j.get("father_name") or "",
                    "mobile_number": j.get("mobile_number") or j.get("mobile") or "",
                    "mobile": j.get("mobile_number") or "",
                    "manufacturer": j.get("manufacturer") or j.get("maker") or "",
                    "vehicle": j.get("vehicle") or j.get("model") or "",
                    "model": j.get("vehicle") or j.get("model") or "",
                    "rto": j.get("rto") or "",
                    "financer": j.get("financer") or "",
                    "insurance_company": j.get("insurance_company") or "",
                    "insurance_policy_number": j.get("insurance_policy_number") or "",
                    "insurance_upto": j.get("insurance_upto") or "",
                    "pucc_number": j.get("pucc_number") or j.get("puc_number") or "",
                    "pucc_upto": j.get("pucc_upto") or j.get("puc_upto") or "",
                    "chassis_number": j.get("chassis_number") or j.get("chassis") or "",
                    "engine_number": j.get("engine_number") or j.get("engine") or "",
                    "registration_date": j.get("registration_date") or j.get("reg_date") or "",
                    "vehicle_age": j.get("vehicle_age") or "",
                    "fuel_type": j.get("fuel_type") or "",
                    "norms_type": j.get("norms_type") or "",
                    "seat_capacity": j.get("seat_capacity") or "",
                    "sleeper_capacity": j.get("sleeper_capacity") or "",
                    "standing_capacity": j.get("standing_capacity") or "",
                    "cubic_capacity": j.get("cubic_capacity") or "",
                    "color": j.get("color") or "",
                    "variant": j.get("variant") or "",
                    "manufacturing_month_year": j.get("manufacturing_month_year") or "",
                    # try to get challan via gateway second call? We'll merge later if needed
                }
                # If we have owner, return immediately but also try to enrich with challan from gateway
                if data.get("owner_name"):
                    # Try to enrich challan from gateway (non-blocking best effort)
                    try:
                        url = f"{_GATEWAY_BASE}/api"
                        params = {"key": _GATEWAY_KEY, "tool": "vehicle", "term": clean}
                        with _provider_slot():
                            rg = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=8, retries=0)
                        if rg.status_code == 200:
                            gj = rg.json()
                            if isinstance(gj, dict) and gj.get("ok"):
                                gd = gj.get("data") or {}
                                # Merge challan details if present
                                if gd.get("challan_details"):
                                    data["challan_details"] = gd["challan_details"]
                                if gd.get("challan_summary"):
                                    data["challan_summary"] = gd["challan_summary"]
                    except Exception:
                        pass
                    ms = int((_t.time() - t0) * 1000)
                    fields = [("👤 Owner", data["owner_name"]), ("🚘 Model", data.get("model") or data.get("vehicle") or "")]
                    return {"ok": True, "term": clean, "data": data, "fields": fields, "extra": {}, "raw_text": "", "ms": ms, "src": "sarkari-vehicle_full", "gateway": False}
    except Exception:
        pass
    # 2) Fallback to gateway vehicle (masked but has challan + BR30AR0802 support)
    try:
        url = f"{_GATEWAY_BASE}/api"
        params = {"key": _GATEWAY_KEY, "tool": "vehicle", "term": clean}
        with _provider_slot():
            r = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=15, retries=0)
        ms = int((_t.time() - t0) * 1000)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok"):
                data = j.get("data") or {}
                if data.get("registration_number") or data.get("owner_name"):
                    fields = []
                    if data.get("owner_name"):
                        fields.append(("👤 Owner", str(data.get("owner_name"))))
                    if data.get("model"):
                        fields.append(("🚘 Model", str(data.get("model"))))
                    return {"ok": True, "term": clean, "data": data, "fields": fields, "extra": {}, "raw_text": "", "ms": ms, "src": "gateway", "gateway": True}
    except Exception:
        pass
    return None


# ======================================================================
#  v127.8 — TG NUM Ka ASLI FIX
# ======================================================================
#  18 Oct 2026 ko pata chala: sarkariallupdates ka ?=tg endpoint AB PAID hai
#  (HTTP 403: "CHALO AB BUY KRO @RONAK_API"). Supabase wala raasta bhi band
#  ("This key is not allowed for tool 'telegramtonumber' ... DM to buy").
#  Isliye har tgnum request par "record nahi mila - API ne jawab nahi diya"
#  aa raha tha — jo JHOOTH tha (record nahi, SOURCE band hai).
#
#  Ab: Telegram ki apni PUBLIC profile (t.me/<username>) se kaam chalta hai:
#  username exist karta hai to naam + profile link milta hai (~0.5 sec,
#  bilkul free, bilkul legal — jo info t.me par public hai wahi).
#  Mobile-number DB paid hone ki wajah se number ab sach me nahi mil sakta,
#  isliye card me SAAF likha hota hai ki source paid hai — user ko bewajah
#  "record nahi mila" nahi dikhta.
# ======================================================================


def _vehicletomobile_gateway(clean: str):
    """📲 Vehicle → Mobile v127.2 — 1) gateway v2.6, 2) sarkari vehicle_to_number direct, 3) vehicle_full mobile, 4) old RC fallback (owner/model) so user gets at least RC info."""
    import time as _t, json as _js
    t0 = _t.time()
    # 1) try gateway first (v2.6)
    try:
        url = f"{_GATEWAY_BASE}/api"
        params = {"key": _GATEWAY_KEY, "tool": "vehicletomobile", "term": clean}
        with _provider_slot():
            r = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=15, retries=0)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok"):
                data = j.get("data") or {}
                ms = int((_t.time() - t0) * 1000)
                if data.get("mobile_number") or data.get("mobile"):
                    return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "gateway", "gateway": True}
    except Exception:
        pass
    # 2) direct sarkari vehicle_to_number
    try:
        import fasthttp as _req
        full_url = f"https://api.sarkariallupdates.com/api?=vehicle_to_number&key=30day&xr={clean}"
        with _provider_slot():
            r = _req.get(full_url, headers=dict(UA_HEADERS), timeout=15)
        ms = int((_t.time() - t0) * 1000)
        if r.status_code == 200 and r.text:
            body = r.text.strip()
            try:
                j = _js.loads(body)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                try:
                    j = _js.loads(body_clean)
                except Exception:
                    j = {}
            if isinstance(j, dict) and j.get("success") and j.get("mobile_number"):
                data = {"registration_number": j.get("registration_number") or clean, "mobile_number": j.get("mobile_number") or "", "mobile": j.get("mobile_number") or ""}
                return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "sarkari-direct", "gateway": False}
    except Exception:
        pass
    # 3) fallback: vehicle_full for mobile (KL41V3504 has mobile in vehicle_full)
    try:
        import fasthttp as _req
        full_url2 = f"https://api.sarkariallupdates.com/api?=vehicle_full&key=30day&xr={clean}"
        with _provider_slot():
            r2 = _req.get(full_url2, headers=dict(UA_HEADERS), timeout=15)
        ms = int((_t.time() - t0) * 1000)
        if r2.status_code == 200 and r2.text:
            try:
                j2 = _js.loads(r2.text)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', r2.text)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                try:
                    j2 = _js.loads(body_clean)
                except Exception:
                    j2 = {}
            if isinstance(j2, dict) and j2.get("success") and j2.get("mobile_number"):
                data = {"registration_number": j2.get("registration_number") or clean, "mobile_number": j2.get("mobile_number") or "", "mobile": j2.get("mobile_number") or ""}
                return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "sarkari-vehicle_full-fallback", "gateway": False}
    except Exception:
        pass
    # 4) v127.2 NEW: old RC gateway fallback — at least show owner/model when mobile not in DB (BR30AR0802 case)
    # User wants "saare vehicle se number aaye" — if mobile truly not in DB, we show RC details instead of pure NO DATA
    try:
        url = f"{_GATEWAY_BASE}/api"
        params = {"key": _GATEWAY_KEY, "tool": "vehicle", "term": clean}
        with _provider_slot():
            r = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=15, retries=0)
        ms = int((_t.time() - t0) * 1000)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok"):
                data = j.get("data") or {}
                if data.get("owner_name") or data.get("registration_number"):
                    # Mark as no-mobile but RC found
                    data["mobile_number"] = data.get("mobile_number") or data.get("phone") or ""
                    data["_no_mobile"] = True
                    return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "gateway-rc-fallback", "gateway": True, "no_mobile": True}
    except Exception:
        pass
    # 5) final — explicit NO DATA
    ms = int((_t.time() - t0) * 1000)
    return {"ok": False, "notfound": True, "error": _no_data(clean, "vehicle to number"), "ms": ms}

def _aadhaar2num_gateway(clean: str):
    """🪪 Aadhaar → Number via gateway v2.6 + direct sarkari fallback (v127.1 robust)"""
    import time as _t, json as _js
    t0 = _t.time()
    # 1) gateway v2.6
    try:
        url = f"{_GATEWAY_BASE}/api"
        params = {"key": _GATEWAY_KEY, "tool": "aadhaar2num", "term": clean}
        with _provider_slot():
            r = http_get(url, params=params, headers=dict(UA_HEADERS), timeout=15, retries=0)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok"):
                data = j.get("data") or {}
                ms = int((_t.time() - t0) * 1000)
                # ensure data has records
                if data.get("count") or data.get("data") or data.get("records"):
                    return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "gateway", "gateway": True}
    except Exception:
        pass
    # 2) direct sarkari ?=addhaar2num — use requests directly (bypass http_get ? encoding issues)
    try:
        import fasthttp as _req
        full_url = f"https://api.sarkariallupdates.com/api?=addhaar2num&key=30day&q={clean}"
        with _provider_slot():
            r = _req.get(full_url, headers=dict(UA_HEADERS), timeout=20)
        ms = int((_t.time() - t0) * 1000)
        if r.status_code == 200 and r.text:
            body = r.text.strip()
            try:
                j = _js.loads(body)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                try:
                    j = _js.loads(body_clean)
                except Exception:
                    j = {}
            if isinstance(j, dict) and (j.get("status") == "success" or j.get("success")):
                dlist = j.get("data") or []
                cnt = j.get("count") or (len(dlist) if isinstance(dlist, list) else 0)
                if cnt and isinstance(dlist, list) and len(dlist) > 0:
                    data = {"aadhaar": clean, "count": cnt, "data": dlist, "records": dlist}
                    return {"ok": True, "term": clean, "data": data, "fields": [], "extra": {}, "raw_text": "", "ms": ms, "src": "sarkari-direct", "gateway": False}
                # empty list = notfound
                if isinstance(dlist, list) and len(dlist) == 0:
                    return {"ok": False, "notfound": True, "error": _no_data(clean, "Aadhaar to Number"), "ms": ms}
            # explicit failure
            if isinstance(j, dict) and j.get("status") == "error":
                return {"ok": False, "notfound": True, "error": _no_data(clean, "Aadhaar to Number"), "ms": ms}
    except Exception:
        pass
    return None

def _cput_k(kind, clean, val):
    if _CACHED:
        _INFO.put(f"{kind}:{clean}", val, 3600)
    # v127.7: success data DB me 30 din (sirf card ke liye zaroori hissa —
    # DB halka rehta hai, bhaari raw payload nahi jaata)
    try:
        if isinstance(val, dict) and val.get("ok") and val.get("fields"):
            from database import cache_put as _pp
            _pp(f"udb:{kind}:{clean}",
                {"ok": True, "term": val.get("term") or clean,
                 "fields": val.get("fields") or [],
                 "extra": val.get("extra") or {},
                 "ms": val.get("ms") or 0},
                2592000)
    except Exception:                                        # noqa: BLE001
        pass


def _neg(kind, clean, payload: dict) -> dict:
    """v117: fail wala jawab bhi short TTL ke liye cache karo, phir lauta do.

    Isse wahi galat value dubara bhejne par 7-8 second ka API call nahi
    hota — jawab turant milta hai.
    """
    if _CACHED:
        try:
            _INFO.put(f"{kind}:{clean}", payload, _NEG_TTL)
        except Exception:
            pass
    return payload


def _esc(t) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# =====================================================================
#  /rcapi ke liye status card
# =====================================================================
def status_card(kind: str) -> str:
    cfg = TOOLS[kind]
    url, key = api_url(kind), api_key(kind)
    if not url:
        return (f"{cfg['icon']} <b>{cfg['label']} — API</b>\n\n"
                f"❌ <b>ABHI NAHI LAGI</b>\n\n"
                f"Render → Environment me daalo:\n"
                f"• <code>{cfg['env']}_API_URL</code> — API ka poora URL\n"
                f"• <code>{cfg['env']}_API_KEY</code> — aapki key\n\n"
                f"<i>Save karte hi bot restart hoga aur tool chalu ho jayega.</i>")
    host = re.sub(r"^https?://", "", url).split("/")[0]
    key_line = f"✅ set hai (<code>{key[:4]}…{key[-3:]}</code>)" if len(key) > 8 else (
        "✅ set hai" if key else "⚪ khali (shayad zaroorat nahi)")
    return (f"{cfg['icon']} <b>{cfg['label']} — API</b>\n\n"
            f"✅ <b>SET HAI</b>\n"
            f"• <b>Host:</b> <code>{_esc(host)}</code>\n"
            f"• <b>Key:</b> {key_line}\n"
            f"• <b>Timeout:</b> {api_timeout(kind)}s\n\n"
            f"🧪 Test: <code>/rcapi {cfg['example']}</code>")
