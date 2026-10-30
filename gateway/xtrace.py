# =====================================================================
#  xtrace.py — X_TRACE API PACK (v2.15)
# =====================================================================
#  Owner ne 5 demo endpoints diye:
#    num | tc (truecaller) | aadhar | vehicle_full | vehicle_num
#
#  LIVE MEASUREMENT (26 Oct 2026, production se):
#    num           : 10.3–10.5s  | 723B  | bahut rich (aadhar + email + 2 records)
#    tc            : 14.2s       | 1150B | UNIQUE data (IMEI/IP/MAC/locations)
#    aadhar        : 12.1s       | 2775B | 10 records, naam+address+pita
#    vehicle_full  : 13–15s      | 1350B | RICH (chassis/engine/insurance/owner)
#    vehicle_num   : 10.6s       | 354B  | data-dependent (kuch gaadi par 404)
#
#  Matlab: ye LOG data-quality me sabse best hai, par SPEED me sabse slow.
#  Isliye strategy:
#    • jahan humara native tool kaam karta + fast hai -> wahi PRIMARY
#      (num 1.8s, vehicle_num 6.7s, aadhaar 0.97s) aur ye FALLBACK
#    • jahan humara tool fail/slow hai -> ye PRIMARY
#      (vehicle_full: humara notfound deta tha; tc: humare paas hi nahi)
#    • familyinfo jo sarkari 500 se mara hua tha -> yahi bachaata hai
#
#  ⚠ Demo key: daily 500 credits + expiry 2026-11-03. Credits response me
#  aate hain (credits_left) — hum use track karte hain aur /api/status me
#  dikhate hain. Credits khatam/expiry par tool apne aap fallback ho jaata hai.
# =====================================================================
import json
import os
import threading
import time
import urllib.parse

import upstreams as _U

# KILL-SWITCH: XTRACE_ENABLED=0 kar do to ye pack poori tarah band (koi network
# call nahi) — credits khatam / provider expire ho jaye to kaam aata hai.
ENABLED = (os.environ.get("XTRACE_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no"))
BASE = (os.environ.get("XTRACE_BASE") or "https://x-trace-osinist-api-pannell.vercel.app/apis").rstrip("/")
TIMEOUT = float(os.environ.get("XTRACE_TIMEOUT", "20"))
# v2.15.1 — PERMANENT-FIRST: successful jawab 30 DIN tak cache (pehle 12h).
# Kyun: (a) credits bachte hain (same term dobara = 0 credit), (b) provider
# expire ho jaye to bhi purane jawab milte rahenge -> tool "marta" nahi.
TTL_OK = int(os.environ.get("XTRACE_CACHE_TTL", "2592000"))     # 30 din
TTL_FAIL = int(os.environ.get("XTRACE_CACHE_FAIL_TTL", "3600"))
STALE_MAX = int(os.environ.get("XTRACE_STALE_MAX", "7776000"))  # 90 din tak stale serve
CACHE_FILE = os.path.join(os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), "xcache.json")

# demo keys built-in (kaam kare turant); owner Render env se apni keys daal sakta hai
_KEYS = {
    "num": os.environ.get("XTRACE_KEY_NUM", "Demo_num"),
    "tc": os.environ.get("XTRACE_KEY_TC", "Demo_truecaller"),
    "aadhar": os.environ.get("XTRACE_KEY_AADHAR", "Demo_aadhar"),
    "vehicle_full": os.environ.get("XTRACE_KEY_VEHICLE", "Demo_rcinfo"),
    "vehicle_num": os.environ.get("XTRACE_KEY_VEHICLE_NUM", "Demo_rcnum"),
}

_CACHE: dict = {}
_LOCK = threading.Lock()
_STATE = {"credits_left": None, "expiry": None, "calls": 0, "ok": 0, "fail": 0,
          "last_error": "", "last_ms": 0}


def state() -> dict:
    with _LOCK:
        return dict(_STATE)


def _note_meta(j):
    if not isinstance(j, dict):
        return
    kd = j.get("key_details") or {}
    credits = kd.get("credits_left", j.get("credits_left"))
    exp = kd.get("expiry_date", j.get("expiry_date"))
    with _LOCK:
        if credits is not None:
            try:
                _STATE["credits_left"] = int(credits)
            except (TypeError, ValueError):
                pass
        if exp:
            _STATE["expiry"] = str(exp)


def credits_ok(minimum: int = 5) -> bool:
    with _LOCK:
        c = _STATE["credits_left"]
    return c is None or c > minimum


def _call(endpoint: str, query: str):
    """-> (json, ms) ya (None, ms)."""
    if not ENABLED:
        return None, 0
    key = _KEYS.get(endpoint) or ""
    url = f"{BASE}/{endpoint}?key={urllib.parse.quote(key)}&query={urllib.parse.quote(str(query))}"
    t0 = time.time()
    code, body = _U.http_get(url, timeout=TIMEOUT)
    ms = int((time.time() - t0) * 1000)
    with _LOCK:
        _STATE["calls"] += 1
        _STATE["last_ms"] = ms
    if code != 200 or not body:
        with _LOCK:
            _STATE["fail"] += 1
            _STATE["last_error"] = f"http {code}"
        try:
            j = json.loads(body) if body else None
            _note_meta(j)
        except Exception:
            pass
        return None, ms
    try:
        j = json.loads(body)
        _note_meta(j)
        with _LOCK:
            _STATE["ok"] += 1
        return j, ms
    except Exception:
        with _LOCK:
            _STATE["fail"] += 1
            _STATE["last_error"] = "bad json"
        return None, ms


def _save_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with _LOCK:
            items = {f"{k[0]}|{k[1]}": v for k, v in list(_CACHE.items())[-3000:]}
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def load_cache():
    """Boot par purane results wapas laao (VPS/restart proof)."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        n = 0
        with _LOCK:
            for k, v in raw.items():
                if "|" not in k or not isinstance(v, list) or len(v) != 3:
                    continue
                ep, q = k.split("|", 1)
                _CACHE[(ep, q)] = (float(v[0]), v[1], int(v[2]))
                n += 1
        if n:
            print(f"[xtrace] cache restored: {n} results", flush=True)
        return n
    except Exception:
        return 0


def start_maintenance():
    """Boot par cache load + har 10 min me disk par save (restart proof)."""
    load_cache()

    def _loop():
        while True:
            time.sleep(600)
            _save_cache()
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def _cached(endpoint: str, query: str):
    with _LOCK:
        hit = _CACHE.get((endpoint, query))
        if hit and time.time() - hit[0] < hit[2]:
            out = dict(hit[1])
            out["cached"] = True
            out["cache_age_s"] = int(time.time() - hit[0])
            return out
    return None


def _store(endpoint: str, query: str, res: dict):
    # v2.18 FIX: transport failure (network/timeout) ko cache NAHI karte.
    # Pehle ek hi transient network error 1 ghante tak tool ko "down" dikha deta tha
    # (bot audit me exactly yahi hua: cached fail, par provider direct chal raha tha).
    if not res.get("ok") and str(res.get("why") or "") in ("upstream", "timeout", "error"):
        return
    with _LOCK:
        if len(_CACHE) > 4000:
            _CACHE.clear()
        _CACHE[(endpoint, query)] = (time.time(), res, TTL_OK if res.get("ok") else TTL_FAIL)


def _stale(endpoint: str, query: str, why: str, msg: str, ms: int = 0):
    """Provider band/expire ho jaye to purana (stale) jawab do — tool zinda rahe."""
    with _LOCK:
        hit = _CACHE.get((endpoint, query))
        if hit and (time.time() - hit[0]) < STALE_MAX:
            out = dict(hit[1])
            out["cached"] = True
            out["stale"] = True
            out["cache_age_s"] = int(time.time() - hit[0])
            out["provider_note"] = f"provider ne jawab nahi diya ({why}) — purana saved jawab diya gaya"
            out["ms"] = ms
            return out
    return None


def _wrap(endpoint: str, query: str, data: dict, ms: int, raw=None) -> dict:
    out = {"ok": True, "data": data, "source": "xtrace", "provider": endpoint, "ms": ms}
    if raw is not None:
        out["raw"] = raw
    _store(endpoint, query, out)
    return out


def _clean(v):
    t = str(v if v is not None else "").strip()
    return "" if t.lower() in ("n/a", "na", "none", "null", "-", "0", "") else t


# ---------------------------------------------------------------- handlers
def _disabled():
    return {"ok": False, "why": "disabled",
            "msg": "xtrace pack band hai (XTRACE_ENABLED=0)", "source": "xtrace", "ms": 0}


def _h_num(term: str) -> dict:
    if not ENABLED:
        return _disabled()
    hit = _cached("num", term)
    if hit:
        return hit
    j, ms = _call("num", term)
    if not isinstance(j, dict):
        st = _stale("num", term, "upstream", "num source jawab nahi de raha", ms)
        return st or {"ok": False, "why": "upstream", "msg": "num source jawab nahi de raha", "source": "xtrace", "ms": ms}
    rows = j.get("result") or []
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "why": "notfound", "msg": "is number ka record nahi mila", "source": "xtrace", "ms": ms}
    f = rows[0] if isinstance(rows[0], dict) else {}
    d = {
        "owner_name": _clean(f.get("name")),
        "father_name": _clean(f.get("fname")),
        "mobile_no": _clean(f.get("mobile")) or term,
        "alt_mobile": _clean(f.get("alt")),
        "address": _clean(f.get("address")),
        "circle": _clean(f.get("circle")),
        "aadhar_card_no": _clean(f.get("aadhar")),
        "email": _clean(f.get("email")),
        "total_records": len(rows),
    }
    if len(rows) > 1 and isinstance(rows[1], dict):
        d["owner_name_1"] = _clean(rows[1].get("name"))
    d = {k: v for k, v in d.items() if v not in ("", None)}
    if not d.get("owner_name") and not d.get("mobile_no"):
        return {"ok": False, "why": "notfound", "msg": "is number ka record nahi mila", "source": "xtrace", "ms": ms}
    return _wrap("num", term, d, ms, raw=rows[:2])


def _h_tc(term: str) -> dict:
    if not ENABLED:
        return _disabled()
    """Truecaller — duniya me sirf provider se milta hai, humare paas pehle nahi tha."""
    hit = _cached("tc", term)
    if hit:
        return hit
    if not credits_ok():
        st = _stale("tc", term, "credits", "provider credits khatam")
        if st:
            return st
        return {"ok": False, "why": "credits", "msg": "truecaller provider ke credits khatam — owner ko nayi key chahiye",
                "source": "xtrace", "ms": 0}
    j, ms = _call("tc", term)
    if not isinstance(j, dict):
        st = _stale("tc", term, "upstream", "truecaller source jawab nahi de raha", ms)
        return st or {"ok": False, "why": "upstream", "msg": "truecaller source jawab nahi de raha", "source": "xtrace", "ms": ms}
    data = ((j.get("result") or {}).get("data") or {}) if isinstance(j.get("result"), dict) else {}
    if not isinstance(data, dict) or not data:
        return {"ok": False, "why": "notfound", "msg": "is number ki truecaller info nahi mili", "source": "xtrace", "ms": ms}
    d = {k: _clean(v) if not isinstance(v, list) else ", ".join(str(x) for x in v)
         for k, v in data.items()}
    d = {k: v for k, v in d.items() if v not in ("", None)}
    return _wrap("tc", term, d, ms)


def _h_aadhar(term: str) -> dict:
    if not ENABLED:
        return _disabled()
    """Aadhaar number -> us aadhaar se jude records (naam/address/pita/mobile)."""
    hit = _cached("aadhar", term)
    if hit:
        return hit
    if not credits_ok():
        st = _stale("aadhar", term, "credits", "provider credits khatam")
        if st:
            return st
        return {"ok": False, "why": "credits", "msg": "aadhar provider credits khatam", "source": "xtrace", "ms": 0}
    j, ms = _call("aadhar", term)
    if not isinstance(j, dict):
        st = _stale("aadhar", term, "upstream", "aadhar source jawab nahi de raha", ms)
        return st or {"ok": False, "why": "upstream", "msg": "aadhar source jawab nahi de raha", "source": "xtrace", "ms": ms}
    rows = j.get("result") or []
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "why": "notfound", "msg": "is aadhaar ka record nahi mila", "source": "xtrace", "ms": ms}
    recs = []
    for r in rows[:10]:
        if not isinstance(r, dict):
            continue
        recs.append({
            "name": _clean(r.get("name")),
            "mobile": _clean(r.get("mobile")),
            "father_name": _clean(r.get("fname")),
            "alt_mobile": _clean(r.get("alt")),
            "address": _clean(r.get("address")),
            "circle": _clean(r.get("circle")),
            "email": _clean(r.get("email")),
        })
    first = recs[0] if recs else {}
    d = {"aadhaar": term, "count": len(recs), "records": recs,
         "primary": first}
    return _wrap("aadhar", term, d, ms)


def _h_vehicle_full(term: str) -> dict:
    if not ENABLED:
        return _disabled()
    hit = _cached("vehicle_full", term)
    if hit:
        return hit
    if not credits_ok():
        st = _stale("vehicle_full", term, "credits", "provider credits khatam")
        if st:
            return st
        return {"ok": False, "why": "credits", "msg": "vehicle provider credits khatam", "source": "xtrace", "ms": 0}
    j, ms = _call("vehicle_full", term)
    if not isinstance(j, dict):
        st = _stale("vehicle_full", term, "upstream", "vehicle source jawab nahi de raha", ms)
        return st or {"ok": False, "why": "upstream", "msg": "vehicle source jawab nahi de raha", "source": "xtrace", "ms": ms}
    if str(j.get("status")) not in ("100", "success", "200"):
        return {"ok": False, "why": "notfound", "msg": "is vehicle ka record nahi mila", "source": "xtrace", "ms": ms}
    d = {
        "registration_number": term,
        "owner_name": _clean(j.get("owner")),
        "father_name": _clean(j.get("ownerFatherName")),
        "address": _clean(j.get("permAddress")),
        "pincode": _clean(j.get("pincode")),
        "vehicle_class": _clean(j.get("vehicleClassDesc") or j.get("vehicleClass")),
        "manufacturer": _clean(j.get("manufacturer")),
        "model": _clean(j.get("vehicleModel") or j.get("model")),
        "fuel_type": _clean(j.get("fuelType")),
        "chassis_number": _clean(j.get("chassis")),
        "engine_number": _clean(j.get("engine")),
        "cubic_capacity": j.get("cubicCapacity"),
        "registration_date": _clean(j.get("registrationDate") or j.get("regDate")),
        "insurance_company": _clean(j.get("insuranceCompanyName")),
        "insurance_policy": _clean(j.get("insurancePolicyNumber")),
        "insurance_upto": _clean(j.get("insuranceUpto")),
        "insurance_expired": j.get("insuranceExpired"),
        "financer": _clean(j.get("financerName")),
        "is_commercial": j.get("isCommercial"),
        "manufacturer_month_year": _clean(j.get("manufacturerMonthYear")),
    }
    d = {k: v for k, v in d.items() if v not in (None, "", [], {})}
    return _wrap("vehicle_full", term, d, ms, raw=j)


def _h_vehicle_num(term: str) -> dict:
    if not ENABLED:
        return _disabled()
    hit = _cached("vehicle_num", term)
    if hit:
        return hit
    if not credits_ok():
        st = _stale("vehicle_num", term, "credits", "provider credits khatam")
        if st:
            return st
        return {"ok": False, "why": "credits", "msg": "vehicle-number provider credits khatam", "source": "xtrace", "ms": 0}
    j, ms = _call("vehicle_num", term)
    if not isinstance(j, dict):
        st = _stale("vehicle_num", term, "upstream", "vehicle_num source jawab nahi de raha", ms)
        return st or {"ok": False, "why": "upstream", "msg": "vehicle_num source jawab nahi de raha", "source": "xtrace", "ms": ms}
    mob = j.get("mobileNumber") or ((j.get("data") or {}).get("data") or {}).get("mobile_number")
    if not mob:
        return {"ok": False, "why": "notfound", "msg": "is vehicle se mobile nahi mila", "source": "xtrace", "ms": ms}
    return _wrap("vehicle_num", term,
                 {"registration_number": term, "mobile_number": str(mob), "mobile": str(mob)}, ms)


HANDLERS = {
    "num": _h_num,
    "tc": _h_tc,
    "aadhar": _h_aadhar,
    "vehicle_full": _h_vehicle_full,
    "vehicle_num": _h_vehicle_num,
}
