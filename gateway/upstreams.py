# =====================================================================
#  Utility API — upstreams (native free + new providers)  v2.6.0
#  v2.6.0: vehicle_full (sarkariallupdates) primary + fallback osint-apis-hub, new tools vehicle_to_number + aadhaar2num, TG API
#  No heavy libs — stdlib only, RAM ~30MB
#  NATIVE: ip, gst, pantogst, ifsc, pincode, vehicle, tgnum, vehicletomobile, aadhaar2num,
#           num, familyinfo, paknum  (v2.7 — purana provider mar chuka tha)
#  PROXY: aadhar, pakvehicle   (advpan + upi PERMANENTLY DELETED — owner order v2.7.1)
# =====================================================================
import gzip
import http.client
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import zlib

VERSION = "2.26.3"

VEHICLE_API_BASE = os.environ.get(
    "VEHICLE_API_URL",
    "https://osint-apis-hub.onrender.com/api",
).strip().rstrip("/")
VEHICLE_API_KEY = (os.environ.get("VEHICLE_API_KEY") or os.environ.get("KEY_VEHICLE") or "1week").strip()

GST_API_BASE = os.environ.get(
    "GST_API_URL",
    "https://osint-apis-hub.onrender.com/api",
).strip().rstrip("/")
GST_API_KEY = (os.environ.get("GST_API_KEY") or os.environ.get("KEY_GST") or "1week").strip()

TG_API_BASE = os.environ.get(
    "TG_API_URL",
    "https://api.sarkariallupdates.com/api",
).strip().rstrip("/")
TG_API_KEY = (os.environ.get("TG_API_KEY") or os.environ.get("KEY_TGNUM") or "30day").strip()

SARKARI_API_BASE = os.environ.get(
    "SARKARI_API_URL",
    "https://api.sarkariallupdates.com/api",
).strip().rstrip("/")
SARKARI_API_KEY = (os.environ.get("SARKARI_API_KEY") or os.environ.get("KEY_SARKARI") or "30day").strip()

# v2.4.0 — ALLPRINTHub MIRROR FALLBACK (sarkariallupdates 509 hone par ye zinda rehta hai)
APH_API_BASE = os.environ.get("APH_API_URL", "https://api.allprinthub.in/api").strip().rstrip("/")
APH_API_KEY = (os.environ.get("APH_API_KEY") or "30day").strip()

def _aph_get(ep: str, params: str, timeout: float = 10.0):
    """allprinthub mirror call — dict ya None. Fallback-only (primary pehle)."""
    try:
        url = f"{APH_API_BASE}?={ep}&key={urllib.parse.quote(APH_API_KEY)}&{params}"
        code, body = http_get(url, timeout=timeout)
        if code != 200 or not body:
            return None
        return json.loads(body)
    except Exception:
        return None

# v2.4.2 — APH-FIRST mode: sarkariallupdates hang ho raha hai (509 se bhi bad),
# isliye allprinthub mirror PEHLE (6-8s), sarkari fallback. Env APH_FIRST=0 se revert.
def _aph_first() -> bool:
    return (os.environ.get("APH_FIRST", "1").strip().lower() not in ("0", "false", "off", "no"))

def _aph_v2n(term):
    aj = _aph_get("vehicle_to_number", f"xr={urllib.parse.quote(term)}", timeout=7)
    if isinstance(aj, dict) and aj.get("success") and aj.get("mobile_number"):
        return {"ok": True, "data": {
            "registration_number": aj.get("registration_number") or term,
            "mobile_number": aj.get("mobile_number") or "",
            "mobile": aj.get("mobile_number") or ""}}
    return None


def _aph_a2num(term):
    aj = _aph_get("addhaar2num", f"num={urllib.parse.quote(term)}", timeout=7)
    if isinstance(aj, dict) and aj.get("status") == "success" and isinstance(aj.get("data"), list) and aj.get("data"):
        data_list = aj["data"][:5]
        merged = {"aadhaar": term, "count": aj.get("count") or len(data_list),
                  "records": data_list, "primary": data_list[0] if data_list else {}}
        if data_list:
            first = data_list[0]
            merged["mobile"] = first.get("phoneNumber") or first.get("mobile") or ""
            merged["name"] = first.get("name") or ""
            merged["address"] = first.get("address") or ""
        return {"ok": True, "data": merged}
    return None


def _aph_family(term):
    aj = _aph_get("family", f"q={urllib.parse.quote(str(term).strip())}", timeout=8)
    if isinstance(aj, dict) and (aj.get("status") is True or aj.get("status") == "success"):
        d0 = aj.get("data")
        mem = d0.get("members") if isinstance(d0, dict) else (d0 if isinstance(d0, list) else None)
        if mem:
            norm = []
            for m in mem:
                if isinstance(m, dict):
                    norm.append({"name": m.get("name") or "",
                                 "relation": m.get("relation") or m.get("relation_name") or "",
                                 "member_id": m.get("member_id") or m.get("member_id_no") or "",
                                 "aadhaar_linked": m.get("aadhaar_linked") or m.get("aadhaar") or ""})
            if norm:
                dd = d0 if isinstance(d0, dict) else {}
                return {"ok": True, "data": {
                    "head_of_family": dd.get("head_of_family") or (norm[0].get("name") or ""),
                    "ration_card_number": dd.get("ration_card_number") or "",
                    "scheme": dd.get("scheme") or "", "state": dd.get("state") or "",
                    "district": dd.get("district") or "", "fps_id": dd.get("fps_id") or "",
                    "total_members": dd.get("total_members") or len(norm),
                    "members": norm, "source_note": "public ledger (mirror)"}}
    return None




# v2.11 — E-Challan source (env se kabhi bhi swap ho sakta hai).
# tower/fastag vendor envs PERMANENTLY DELETED (tools hi nahi rahe).

TOOLS = {
    "ip":              "IP / domain ki poori jankari (free, koi key nahi)",
    "gst":             "GSTIN se company ki jankari (v2.4 new: gst-direct mastersindia, fallback jamku free)",
    "pantogst":        "PAN se GSTIN list + full company details (pan-to-gst-v4 v2.4)",
    "ifsc":            "IFSC se bank branch (free)",
    "pincode":         "Pincode se post offices (free)",
    "familyinfo":      "Aadhaar se poore parivaar ki jankari (v2.7 NATIVE — sarkari ration, muft)",
    "num":             "Mobile number se owner ki jankari (v2.7 NATIVE — sarkari num, muft)",
    "tginfo":          "Telegram LIVE info (user/channel/group: name, bio, members, id, photo) — t.me + Bot-API, no-key",
    "tgnum":           "Telegram username/ID/phone — owner ka apna99k local dataset pehle, phir sarkari/supabase chain (v2.9)",
    "vehicle":         "Vehicle full report (owner, model, RTO, insurance, PUC) — LakshitX rtoData primary + osint fallback",
    "weather":         "Mausam report (koi bhi city/lat,lon) — current + 3-day forecast (Open-Meteo, no-key)",
    "dns":             "Domain DNS records (A/AAAA/MX/TXT/NS) — dns.google DoH, no-key",
    "vehicletomobile": "Vehicle number se mobile number (sarkariallupdates vehicle_to_number — v2.6 new)",
    "aadhaar2num":     "Aadhaar number se mobile numbers (sarkariallupdates addhaar2num — v2.6 new)",
    "phonebiz":        "NEW v2.10: PHONE -> Business info — owner ka apna 1.72M local B2B index (never-expire, local sqlite)",
    # v2.12 — OWNER KE APNE TOOLS: kisi paid/demo API par depend nahi
    "plate":           "v2.12 NATIVE OFFLINE: RC plate decode — state/RTO/city (0ms, never expire)",
    "aadhaarcheck":    "v2.12 NATIVE OFFLINE: Aadhaar Verhoeff checksum (0ms, koi personal data nahi)",
    "upi":             "UPI ID se payer/bank info (v2.24.6 LakshitX provider)",
    "insta":           "Instagram profile info — followers/bio/pic/verified (v2.24.7 NATIVE)",
    "gstcheck":        "v2.12 NATIVE OFFLINE: GSTIN checksum validate (0ms)",
    "webextract":      "v2.12 NATIVE SELF-HOSTED: URL -> title/desc/emails/phones/links (koi third party nahi)",
    "aadhaar2pan":     "Aadhaar se PAN (masked) — sarkariallupdates aadhaar2pan (v2.23 NEW, Ronak source)",
    "imei":            "IMEI forensics — Luhn check + reporting body (NATIVE OFFLINE, 0ms, never expire)",
    "fastag":          "FASTag NETC info — RC se tags, bank, status, issue date",
    # v2.15 — X_TRACE pack (comparison ke baad)
    "tc":              "v2.15 TRUE CALLER: number -> owner name/address/IMEI/locations (X_TRACE). Duniya me sirf provider se milta hai",
    "aadhaar":         "v2.15 RICH AADHAAR: aadhaar -> us se jude saare records (naam/address/pita/mobile) — X_TRACE, ghar ke kuTumb ke liye",
}

NATIVE = ("ip", "gst", "pantogst", "ifsc", "pincode", "vehicle", "tgnum", "vehicletomobile", "aadhaar2num",
          "num", "familyinfo", "phonebiz",
          "plate", "aadhaarcheck", "gstcheck", "webextract",
          "tc", "aadhaar")   # v2.15 — X_TRACE pack

# har tool ka ek ready example (key-studio ke URLs aur docs ke liye)
_EXAMPLES = {
    "ip": "8.8.8.8", "ifsc": "SBIN0000001", "pincode": "800001",
    "gst": "27AAPFU0939F1ZV", "pantogst": "AAACR5055K", "plate": "HR26DK8337",
    "aadhaarcheck": "234567890123", "gstcheck": "27AAPFU0939F1ZV",
    "webextract": "https://example.com", "num": "9006640786",
    "familyinfo": "123412341234", "aadhaar": "123412341234",
    "aadhaar2num": "123412341234", "tgnum": "@telegram",
    "tc": "8448140878", "vehicle": "UP16FB2070", "vehicletomobile": "UP16FB2070",
    "phonebiz": "9876543210",
}

ALIASES = {
    "tguser": "tginfo",
    "tgchat": "tginfo",
    "tggroup": "tginfo",
    "channelinfo": "tginfo",
    "weather_info": "weather",
    "mausam": "weather",
    "mausam_info": "weather",
    "dns_lookup": "dns",
    "resolve": "dns",
    "lynxosint__vehicle": "vehicle",
    "lynxosint__ip": "ip",
    "telegramtonumber": "tgnum",
    "rc": "vehicle",
    "vehicle_rc": "vehicle",
    "vehicle_full": "vehicle",
    "gst_info": "gst",
    "pan_to_gst": "pantogst",
    "pantogst_v4": "pantogst",
    "vehicle_to_number": "vehicletomobile",
    "vehicle_to_mobile": "vehicletomobile",
    "rc_to_mobile": "vehicletomobile",
    "rc2mobile": "vehicletomobile",
    "vehicle2mobile": "vehicletomobile",
    "addhaar2num": "aadhaar2num",
    "aadhar2num": "aadhaar2num",
    "aadhaar_to_number": "aadhaar2num",
    "aadhar_to_number": "aadhaar2num",
    "aadhaar_to_mobile": "aadhaar2num",
    "truecaller": "tc",
    "true_caller": "tc",
    "tc_info": "tc",
    "aadhaar_info": "aadhaar",
    "aadharinfo": "aadhaar",
    "aadhar_full": "aadhaar",
    "aadhar": "aadhaar",
    "phone2biz": "phonebiz", "bizinfo": "phonebiz", "businessinfo": "phonebiz",
    "landlineinfo": "phonebiz", "business": "phonebiz", "biz": "phonebiz",
    # v2.12 aliases
    "rcplate": "plate", "platedecode": "plate", "rto": "plate", "rcdecode": "plate",
    "aadhaarvalidate": "aadhaarcheck", "aadharcheck": "aadhaarcheck", "uidcheck": "aadhaarcheck",
    "gstvalidate": "gstcheck", "gstincheck": "gstcheck",
    "webextract": "webextract", "scrape": "webextract", "emailextract": "webextract",
    "linkextract": "webextract", "extract": "webextract",
}

_RE = {
    "ip":              re.compile(r"^([0-9]{1,3}\.){3}[0-9]{1,3}$|^[0-9a-fA-F:]+$|^[a-z0-9.-]+\.[a-z]{2,}$"),
    "gst":             re.compile(r"^[0-9A-Z]{15}$"),
    "pantogst":        re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$|^[A-Z]{4}[0-9]{5}[A-Z0-9]?$"),
    "ifsc":            re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$"),
    "pincode":         re.compile(r"^[1-9][0-9]{5}$"),
    "familyinfo":      re.compile(r"^[0-9]{12}$"),
    "num":             re.compile(r"^[6-9][0-9]{9}$"),
    "tginfo":          re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{2,31}$|^https?://t\.me/\S+$|^tg://\S+$|^-?[0-9]{5,20}$"),
    "tgnum":           re.compile(r"^@?[A-Za-z0-9_]{3,32}$|^[0-9]{5,15}$"),
    "vehicle":         re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{3,4}$"),
    "vehicletomobile": re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{3,4}$"),
    "aadhaar2num":     re.compile(r"^[0-9]{12}$"),
    "aadhaar2pan":     re.compile(r"^[0-9]{12}$"),
    "phonebiz":        re.compile(r"^\+?[0-9][0-9 \-]{6,17}$"),
    "fastag":          re.compile(r"^[A-Za-z]{2}[0-9]{1,2}[A-Za-z]{0,3}[0-9]{3,4}$"),
    # v2.12 — offline tools (validation khud handler me bhi hai)
    "plate":           re.compile(r"^[A-Za-z0-9 \-]{4,16}$"),
    "aadhaarcheck":    re.compile(r"^[0-9 \-]{12,16}$"),
    "upi":             re.compile(r"^[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}$"),
    "insta":           re.compile(r"^@?[A-Za-z0-9._]{1,30}$"),
    "gstcheck":        re.compile(r"^[A-Za-z0-9]{15}$"),
    "webextract":      re.compile(r"^(https?://)?[A-Za-z0-9.\-]+\.[A-Za-z]{2,}.*$"),
    "tc":              re.compile(r"^[6-9][0-9]{9}$"),
    "weather":         re.compile(r"^(?:(?:weather|mausam)[\s:]+)?[A-Za-z][A-Za-z .,'\-]{1,39}$|^[-0-9.,]+,[-0-9.,]+$"),
    "dns":             re.compile(r"^(?:(?:dns|resolve)[\s:]+)?[A-Za-z0-9][A-Za-z0-9.\-]{2,60}$"),
    "aadhaar":         re.compile(r"^[0-9]{12}$"),
}

def validate(tool: str, term: str) -> str:
    t = (term or "").strip().upper() if tool in ("ifsc", "gst", "vehicle", "pantogst", "fastag") else (term or "").strip()
    if tool in ("familyinfo", "num", "pincode"):
        t = re.sub(r"[^0-9]", "", t)
    if tool == "tgnum" and t.startswith("@"):
        t = term.strip()
    if tool == "pantogst":
        t = re.sub(r"[^A-Z0-9]", "", t.upper())
    if not t or len(t) > 64:
        return ""
    rx = _RE.get(tool)
    if rx and not rx.match(t):
        # for pantogst allow 10 char PAN
        if tool == "pantogst" and len(t) == 10:
            return t
        if tool != "pantogst":
            return ""
    return t

# =====================================================================
#  v2.14 SPEED — HTTP client with KEEP-ALIVE CONNECTION POOL
#  Pehle har call par naya TLS handshake hota tha (~250-500ms waste).
#  Ab connection reuse hota hai: 2nd+ call ~40-100ms faster.
# =====================================================================
_POOL: dict = {}
_POOL_LOCK = threading.Lock()
_POOL_MAX_PER_HOST = int(os.environ.get("POOL_MAX_PER_HOST", "6"))
_UA = "Mozilla/5.0 (utility-api/2.14)"


def _pool_key(u):
    return (u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80))


def _new_conn(u, timeout):
    if u.scheme == "https":
        return http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)


def _conn_take(u, timeout):
    k = _pool_key(u)
    with _POOL_LOCK:
        lst = _POOL.get(k)
        if lst:
            return k, lst.pop()
    return k, _new_conn(u, timeout)


def _conn_give(k, conn):
    with _POOL_LOCK:
        lst = _POOL.setdefault(k, [])
        if len(lst) < _POOL_MAX_PER_HOST:
            lst.append(conn)
            return
    try:
        conn.close()
    except Exception:
        pass


def _decode(resp, raw):
    enc = (resp.getheader("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    return raw.decode("utf-8", "replace")


def http_get(url: str, timeout: float = 25.0, headers=None, _redirects: int = 5):
    """Keep-alive pooled GET. Same return contract: (status, text)."""
    hdrs = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    if headers:
        hdrs.update(headers)
    cur = url
    for _hop in range(_redirects + 1):
        try:
            u = urllib.parse.urlsplit(cur)
        except Exception:
            return None, ""
        if u.scheme not in ("http", "https") or not u.hostname:
            return None, ""
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        for attempt in (0, 1):
            k, conn = _conn_take(u, timeout)
            try:
                conn.request("GET", path, headers=hdrs)
                resp = conn.getresponse()
                raw = resp.read()
                code = resp.status
                loc = resp.getheader("Location")
                if code in (301, 302, 303, 307, 308) and loc:
                    _conn_give(k, conn)
                    cur = urllib.parse.urljoin(cur, loc)
                    break
                _conn_give(k, conn)
                return code, _decode(resp, raw)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 0:
                    continue          # pooled connection mar gaya -> naya conn se ek retry
                return None, ""
    return None, ""

def _native_ip(term: str) -> dict:
    try:
        ip = term
        if not re.match(r"^([0-9]{1,3}\.){3}[0-9]{1,3}$", term) and ":" not in term:
            ip = socket.gethostbyname(term)
        if ipaddress.ip_address(ip).is_private:
            return {"ok": False, "why": "notfound", "msg": "private IP"}
    except Exception:
        return {"ok": False, "why": "validation", "msg": "IP/domain samajh nahi aaya"}
    f = ("status,message,country,countryCode,regionName,city,district,zip,"
         "lat,lon,timezone,currency,isp,org,as,asname,reverse,proxy,hosting,mobile")
    code, body = http_get(f"http://ip-api.com/json/{ip}?fields={f}", timeout=10)
    if code == 200 and body:
        try:
            j = json.loads(body)
            if j.get("status") == "success":
                return {"ok": True, "data": j}
        except Exception:
            pass
    code, body = http_get(f"https://ipwho.is/{ip}", timeout=10)
    if code == 200 and body:
        try:
            j = json.loads(body)
            if j.get("success"):
                c = j.get("connection") or {}
                return {"ok": True, "data": {
                    "country": (j.get("country") or {}).get("name") if isinstance(j.get("country"), dict) else j.get("country"),
                    "city": j.get("city"), "region": j.get("region"),
                    "isp": c.get("isp"), "org": c.get("org"),
                    "reverse": j.get("reverse"), "query": j.get("ip")}}
        except Exception:
            pass
    return {"ok": False, "why": "upstream", "msg": "ip-api + ipwho.is dono fail"}

def _native_ifsc(term: str) -> dict:
    code, body = http_get(f"https://ifsc.razorpay.com/{term}", timeout=10)
    if code == 200 and body:
        try:
            j = json.loads(body)
            if j.get("BANK"):
                return {"ok": True, "data": j}
        except Exception:
            pass
    return {"ok": False, "why": "notfound", "msg": "IFSC nahi mila"}


def _native_weather(term: str) -> dict:
    """Open-Meteo (no-key, kabhi-expire-nahi): city/lat,lon → current + 3-day forecast."""
    t = term.strip()
    for pre in ("weather", "mausam"):
        if t.lower().startswith(pre):
            t = t[len(pre):].strip(" :,-")
    # ---- provider-1: wttr.in (no-key, Render shared-IP pe bhi khula) ----
    try:
        w_code, w_body = http_get(f"https://wttr.in/{urllib.parse.quote(t)}?format=j1&lang=en", timeout=14,
                                  headers={"Accept-Encoding": "identity", "User-Agent": "curl/8.5.0"})
        if w_code == 200 and w_body:
            j = json.loads(w_body)
            cc = (j.get("current_condition") or [{}])[0]
            days = []
            for d in (j.get("weather") or [])[:3]:
                days.append({"date": d.get("date"), "max": d.get("maxtempC"), "min": d.get("mintempC"),
                             "sky": ((d.get("hourly") or [{}])[4].get("weatherDesc") or [{}])[0].get("value", "—")})
            if cc:
                return {"ok": True, "source": "wttr.in", "data": {
                    "place": t, "temp_c": cc.get("temp_C"), "feels_c": cc.get("FeelsLikeC"),
                    "humidity": cc.get("humidity"), "wind_kmph": cc.get("windspeedKmph"),
                    "sky": ((cc.get("weatherDesc") or [{}])[0].get("value") or "—"), "forecast_3d": days}}
    except Exception:
        pass
    # ---- provider-2: Open-Meteo (geocode + forecast) ----
    try:
        if "," in t and all(p.replace(".", "").isdigit() and p.strip() for p in t.split(",", 1)):
            la, lo = t.split(",", 1)
            lat, lon, place = float(la), float(lo), f"{la},{lo}"
        else:
            c_code, c_body = http_get(f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(t)}&count=1&language=en&format=json", timeout=12, headers={"Accept-Encoding": "identity"})
            g = (json.loads(c_body).get("results") or [{}])[0] if c_code == 200 and c_body else {}
            if not g:
                return {"ok": False, "why": "notfound", "msg": f"City '{t}' nahi mili — spelling check karo"}
            lat, lon = float(g["latitude"]), float(g["longitude"])
            place = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)
        w_code, w_body = http_get(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min,weather_code&timezone=auto&forecast_days=3", timeout=15, headers={"Accept-Encoding": "identity"})
        if w_code != 200 or not w_body:
            return {"ok": False, "why": "upstream", "msg": f"Open-Meteo HTTP {w_code}"}
        j = json.loads(w_body)
        WC = {0:"Clear ☀️",1:"Mostly clear 🌤️",2:"Partly cloudy ⛅",3:"Overcast ☁️",45:"Fog 🌫️",48:"Fog 🌫️",
              51:"Drizzle 🌦️",53:"Drizzle 🌦️",55:"Drizzle 🌦️",61:"Light rain 🌧️",63:"Rain 🌧️",65:"Heavy rain 🌧️",
              71:"Snow ❄️",73:"Snow ❄️",75:"Snow ❄️",80:"Showers 🌦️",81:"Showers 🌦️",82:"Showers ⛈️",
              95:"Thunderstorm ⛈️",96:"Thunderstorm ⛈️",99:"Thunderstorm ⛈️"}
        cu, dl = j.get("current", {}), j.get("daily", {})
        days = [{"date": d, "max": dl["temperature_2m_max"][i], "min": dl["temperature_2m_min"][i],
                 "sky": WC.get(dl["weather_code"][i], "—")} for i, d in enumerate(dl.get("time", []))]
        return {"ok": True, "source": "open-meteo", "data": {
            "place": place, "temp_c": cu.get("temperature_2m"), "feels_c": cu.get("apparent_temperature"),
            "humidity": cu.get("relative_humidity_2m"), "wind_kmph": cu.get("wind_speed_10m"),
            "sky": WC.get(cu.get("weather_code"), "—"), "forecast_3d": days}}
    except Exception as e:
        return {"ok": False, "why": "error", "msg": f"weather: {str(e)[:100]}"}


def _native_dns(term: str) -> dict:
    """DoH dns.google (no-key): domain → A/AAAA/MX/TXT/NS records."""
    d = term.strip().lower()
    for pre in ("dns", "resolve"):
        if d.startswith(pre + " "):
            d = d[len(pre):].strip()
    d = d.removeprefix("https://").removeprefix("http://").split("/")[0]
    if "." not in d or not all(ch.isalnum() or ch in "-." for ch in d):
        return {"ok": False, "why": "validation", "msg": "Domain nahi lag raha — jaise google.com"}
    recs = {}
    for rtype in ("A", "AAAA", "MX", "TXT", "NS"):
        try:
            code, body = http_get(f"https://dns.google/resolve?name={urllib.parse.quote(d)}&type={rtype}", timeout=10)
            if code != 200 or not body:
                recs[rtype] = []; continue
            ans = [a.get("data") for a in json.loads(body).get("Answer", []) if a.get("data")]
            recs[rtype] = ans[:8]
        except Exception:
            recs[rtype] = []
    if not any(recs.values()):
        return {"ok": False, "why": "notfound", "msg": f"'{d}' ke koi DNS-records nahi mile"}
    return {"ok": True, "source": "dns-google-doh", "data": {"domain": d,
            "A": recs["A"], "AAAA": recs["AAAA"], "MX": recs["MX"], "TXT": recs["TXT"], "NS": recs["NS"]}}


def _native_pincode(term: str) -> dict:
    code, body = http_get(f"https://api.postalpincode.in/pincode/{term}", timeout=10)
    if code == 200 and body:
        try:
            j = json.loads(body)
            if isinstance(j, list) and j and j[0].get("Status") == "Success":
                return {"ok": True, "data": j[0]}
        except Exception:
            pass
    return {"ok": False, "why": "notfound", "msg": "Pincode nahi mila"}

def _native_gst_free(term: str) -> dict:
    """v2.14 FIX: jamku ne format badal diya (ab lgnm nahi aata, success+gstin aata hai).
    Pehle har valid GSTIN par bhi 'notfound' milta tha."""
    code, body = http_get(f"https://gst.jamku.app/api/gstin/{term}", timeout=15)
    if code == 200 and body:
        try:
            j = json.loads(body)
            data = j.get("data") if isinstance(j, dict) else None
            if isinstance(data, dict) and (data.get("gstin") or data.get("lgnm") or j.get("success")):
                meta = data.get("meta") or {}
                out = {
                    "gstin": data.get("gstin") or term,
                    "pan": data.get("pan") or "",
                    "legal_name": data.get("lgnm") or "",
                    "trade_name": data.get("tradeNam") or data.get("tradeName") or "",
                    "status": data.get("status") or "",
                    "category": data.get("compCategory") or "",
                    "address": data.get("adr") or "",
                    "registration_date": data.get("rgdt") or "",
                    "last_return_sync": meta.get("syncReturnDate") or "",
                }
                out = {k: v for k, v in out.items() if v not in (None, "", [], {})}
                return {"ok": True, "data": out, "raw": data}
        except Exception:
            pass
    return {"ok": False, "why": "notfound", "msg": "GST record nahi mila"}

def _native_gst(term: str) -> dict:
    """v2.4: GST — new provider gst-direct (mastersindia) + fallback jamku free."""
    try:
        url = f"{GST_API_BASE}/gst-direct?key={urllib.parse.quote(GST_API_KEY)}&gstin={urllib.parse.quote(term)}"
        code, body = http_get(url, timeout=20)
        if code == 200 and body:
            j = json.loads(body)
            data = None
            if j.get("mastersindia", {}).get("data"):
                data = j["mastersindia"]["data"][0]
            elif j.get("data"):
                data = j["data"]
            if data and (data.get("lgnm") or data.get("gstin")):
                if data.get("tradeNam") and not data.get("tradeName"):
                    data["tradeName"] = data["tradeNam"]
                pradr = data.get("pradr", {}).get("addr") or {}
                if pradr and not data.get("adr"):
                    parts = [pradr.get("bno",""), pradr.get("flno",""), pradr.get("st",""), pradr.get("loc",""), pradr.get("dst",""), pradr.get("stcd",""), pradr.get("pncd","")]
                    data["adr"] = ", ".join([p for p in parts if p])
                    data["pincode"] = pradr.get("pncd") or data.get("pincode") or ""
                return {"ok": True, "data": {"gst_data": data, "raw": data}, "gst_data": data}
    except Exception:
        pass
    try:
        return _native_gst_free(term)
    except Exception:
        return {"ok": False, "why": "notfound", "msg": "GST record nahi mila"}

def _native_pantogst(term: str) -> dict:
    """v2.4: PAN to GST — v4 most detailed + fallback v3/v1."""
    pan = term.upper()
    for endpoint in ("pan-to-gst-v4", "pan-to-gst-v3", "pan-to-gst"):
        try:
            url = f"{GST_API_BASE}/{endpoint}?key={urllib.parse.quote(GST_API_KEY)}&pan={urllib.parse.quote(pan)}"
            code, body = http_get(url, timeout=20)
            if code == 200 and body:
                j = json.loads(body)
                if j.get("success"):
                    gd = j.get("gst_details")
                    if isinstance(gd, dict) and gd.get("gstin"):
                        return {"ok": True, "data": {"pan": pan, "gst_details": gd, "all_gstins": [gd.get("gstin")]}, "pan": pan}
                    if isinstance(gd, list) and gd:
                        gstins = [g.get("gstin") for g in gd if g.get("gstin")]
                        return {"ok": True, "data": {"pan": pan, "gst_details": gd[0] if len(gd)==1 else gd, "all_gstins": gstins}, "pan": pan}
        except Exception:
            continue
    return {"ok": False, "why": "notfound", "msg": "Is PAN par koi GSTIN nahi mila"}

    # v2.23.1 — SARKARI pan2gst fallback (purana provider expire ho gaya tha).
    # Source ka JSON broken hota hai (stray string lines) — isliye GSTIN regex-se nikaalte hain.
    try:
        url2 = (f"{SARKARI_API_BASE}?api=pan2gst&key={urllib.parse.quote(SARKARI_API_KEY)}"
                f"&pan2gst={urllib.parse.quote(pan)}")
        code2, body2 = http_get(url2, timeout=15)
        if code2 == 200 and body2:
            gstins = sorted(set(re.findall(
                r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b", body2)))
            if gstins:
                return {"ok": True, "source": "sarkari-pan2gst",
                        "data": {"pan": pan, "gstin_count": len(gstins),
                                 "gstins": gstins[:10],
                                 "tip": "Detail ke liye gst tool me GSTIN daalo"}}
            if '"status": "success"' in body2 or '"status":"success"' in body2:
                return {"ok": False, "why": "notfound", "msg": "Is PAN par koi GSTIN registered nahi hai"}
    except Exception:
        pass


def _tme_public(q: str, tmo: float = 8.0) -> dict:
    """v2.6.1 — Telegram ki PUBLIC profile (t.me/<username>): naam + photo + link.
    Free + legal + ~0.5s. Lautata: {"exists":True,"name":..,"photo":..,"url":..}
    ya {"exists":False} ya None (network fail)."""
    import html as _html
    uname = str(q or "").strip().lstrip("@").strip()
    if not uname or not _re_tme.match(uname):
        return None
    code, body = http_get(f"https://t.me/{uname}", timeout=tmo)
    if code != 200 or not body:
        return None
    mt = re.search(r'<meta property="og:title" content="([^"]*)"', body)
    mi = re.search(r'<meta property="og:image" content="([^"]*)"', body)
    title = _html.unescape(mt.group(1)) if mt else ""
    photo = mi.group(1) if mi else ""
    if not title or title.startswith("Telegram: Contact @") or "t_logo" in photo:
        return {"exists": False}
    return {"exists": True, "name": title,
            "photo": ("" if "t_logo" in photo else photo),
            "url": f"https://t.me/{uname}"}


_re_tme = re.compile(r"^[A-Za-z0-9_]{3,32}$")

_TG_PAID_NOTE = ("Mobile-number database ab PAID ho gaya hai (provider: 'CHALO AB BUY KRO') "
                 "- isliye number nahi mil sakta. Upar jo hai wo Telegram ki "
                 "PUBLIC profile info hai (t.me se).")


# ══════════════════════════════════════════════════════════════════
#  v2.9.0 — OWNER KA APNA LOCAL TG DATASET (99,247 records)
#  User ne files di (23 Oct 2026): har line "User ID | Phone | Username".
#  Repo ke andar datasets/tg_numbers.txt — kabhi expire nahi, koi paid
#  source nahi, boot par lazy-load (O(1) lookups baad me).
#  Miss hone par _native_tgnum ki PURANI chain chalti raheti hai.
# ══════════════════════════════════════════════════════════════════
_TG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets", "tg_numbers.txt")
_TG_RX = re.compile(r"User ID:\s*(\d+)\s*\|\s*Phone:\s*(\d+)\s*\|\s*Username:\s*(.*)$")
_TG = {"phone": {}, "uid": {}, "user": {}, "count": 0}
_TG_READY = False
_TG_LOCK = threading.Lock()


def _tg_ensure() -> None:
    """Lazy-load — boot fast, dataset sirf pehle TG call par parse (≈0.3s)."""
    global _TG_READY
    if _TG_READY:
        return
    with _TG_LOCK:
        if _TG_READY:
            return
        try:
            with open(_TG_FILE, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _TG_RX.match(line.strip())
                    if not m:
                        continue
                    uid, phone, uname = m.group(1), m.group(2), m.group(3).strip()
                    rec = (uid, phone, uname)
                    key10 = phone[2:] if (len(phone) == 12 and phone.startswith("91")) else phone
                    if len(key10) == 10:
                        _TG["phone"].setdefault(key10, []).append(rec)
                    _TG["uid"].setdefault(uid, []).append(rec)
                    if uname:
                        _TG["user"].setdefault(uname.lower(), []).append(rec)
                    _TG["count"] += 1
        except Exception:
            pass
        _TG_READY = True


def _tg_local(term: str):
    """Owner dataset: phone / TG-ID / username -> record dict, warna None."""
    _tg_ensure()
    t = str(term or "").strip()
    if not t:
        return None
    hits, via = [], ""
    if t.isdigit():
        d = t
        if len(d) == 12 and d.startswith("91"):
            d = d[2:]
        elif len(d) == 11 and d.startswith("0"):
            d = d[1:]
        if len(d) == 10 and d[0] in "6789":
            hits = _TG["phone"].get(d, [])
            via = "phone"
        if not hits:
            hits = _TG["uid"].get(t, [])
            via = "telegram_id"
        if not hits and len(d) == 10:
            hits = _TG["phone"].get(d, [])
            via = "phone"
    else:
        hits = _TG["user"].get(t.lstrip("@").lower(), [])
        via = "username"
    if not hits:
        return None
    uid, phone, uname = hits[0]
    ten = phone[2:] if (len(phone) == 12 and phone.startswith("91")) else phone
    return {
        "ok": True,
        "data": {
            "telegram_id": uid,
            "tg_id": uid,
            "mobile": ten,
            "owner_number": ten,
            "username": uname or "",
            "country_code": "+91",
            "matched_via": via,
            "total_matches": len(hits),
            "source": f"owner-local-dataset ({_TG['count']} records)",
        },
    }


def _native_tginfo(term: str) -> dict:
    """v2.26.3 - Telegram LIVE-info (user/channel/group): t.me-scrape (no-key, sabke liye)
    + Bot-API getChat/getChatMemberCount enrich (env TELEGRAM_BOT_TOKEN, channel/group deep).
    Ye wo kaam hai jo leaked member-dumps karta tha - par LIVE aur legal (kabhi-stale-nahi)."""
    t = term.strip()
    uname, numid = None, None
    m = re.match(r"^@([A-Za-z][A-Za-z0-9_]{2,31})$", t)
    if m:
        uname = m.group(1)
    else:
        m = re.search(r"t\.me/([A-Za-z][A-Za-z0-9_]{2,31})", t) or \
            re.search(r"tg://resolve\?domain=([A-Za-z][A-Za-z0-9_]{2,31})", t)
        if m:
            uname = m.group(1)
        elif re.match(r"^-?[0-9]{5,20}$", t):
            numid = t
    if not uname and not numid:
        return {"ok": False, "why": "validation", "msg": "Telegram @username / t.me-link / numeric-id do"}
    try:
        import html as _html
        data = {}
        # ---- 1) t.me public page (users ke liye MAIN source; channels ko bhi chalta) ----
        if uname:
            c, body = http_get(f"https://t.me/{uname}", timeout=15)
            if c == 200 and body and "tgme_page" in body:
                nm = re.search(r'og:title" content="([^"]*)"', body)
                bio = re.search(r'og:description" content="([^"]*)"', body)
                img = re.search(r'og:image" content="([^"]*)"', body)
                extra = re.search(r'tgme_page_extra">([^<]*)<', body)
                data = {
                    "username": uname,
                    "link": f"https://t.me/{uname}",
                    "display_name": _html.unescape(nm.group(1)).strip() if nm else "",
                    "bio": _html.unescape(bio.group(1)).strip() if bio else "",
                    "photo": img.group(1) if img else "",
                }
                ex = _html.unescape(extra.group(1)).strip() if extra else ""
                if ex:
                    data["extra"] = ex
                    mm = re.search(r"([0-9][0-9 ,.]*)\s*(subscriber|member)", ex.lower())
                    if mm:
                        cnt = int(re.sub(r"[^0-9]", "", mm.group(1)) or 0)
                        data["members"] = cnt
                        data["type"] = "channel" if "subscriber" in ex.lower() else "group"
        # ---- 2) Bot-API enrich (TELEGRAM_BOT_TOKEN env; channel/group deep-info) ----
        tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if tok and (uname or numid):
            cid = f"@{uname}" if uname else numid
            c2, b2 = http_get(f"https://api.telegram.org/bot{tok}/getChat?chat_id={urllib.parse.quote(cid)}", timeout=15)
            if c2 == 200 and b2:
                try:
                    j = json.loads(b2)
                except Exception:
                    j = {}
                if j.get("ok") and isinstance(j.get("result"), dict):
                    rr = j["result"]
                    data["id"] = rr.get("id")
                    data["type"] = rr.get("type") or data.get("type")
                    if rr.get("title"):
                        data["display_name"] = rr.get("title")
                    if rr.get("first_name"):
                        data["display_name"] = (data.get("display_name") or rr.get("first_name"))
                    if rr.get("username"):
                        data["username"] = rr.get("username")
                    if rr.get("bio") or rr.get("description"):
                        data["bio"] = rr.get("bio") or rr.get("description")
                    if rr.get("active_usernames"):
                        data["active_usernames"] = rr.get("active_usernames")
                    if rr.get("invite_link"):
                        data["invite_link"] = rr.get("invite_link")
                    if rr.get("linked_chat_id"):
                        data["linked_chat_id"] = rr.get("linked_chat_id")
                    c3, b3 = http_get(f"https://api.telegram.org/bot{tok}/getChatMemberCount?chat_id={urllib.parse.quote(cid)}", timeout=15)
                    if c3 == 200 and b3:
                        try:
                            j3 = json.loads(b3)
                            if j3.get("ok"):
                                data["members"] = j3.get("result")
                        except Exception:
                            pass
            elif c2 == 400 and "chat not found" in str(b2).lower() and not data:
                return {"ok": False, "why": "notfound", "msg": f"'{uname or numid}' nahi mila (private ya galat)"}
        if not data or not data.get("display_name"):
            return {"ok": False, "why": "notfound", "msg": "Telegram pe info nahi mili"}
        data.setdefault("type", "user")
        return {"ok": True, "source": "t.me+botapi", "data": data}
    except Exception as e:
        return {"ok": False, "why": "error", "msg": f"tginfo: {str(e)[:90]}"}


def _native_tgnum(term: str) -> dict:
    """v2.5 — Telegram username/ID -> mobile (sarkariallupdates.com).
    API: https://api.sarkariallupdates.com/api?=tg&key=30day&q=@Perfectamit65
    Returns: OwnerNumber, TG-ID, Country-Code
    """
    # Clean term: keep @ if present, else as is
    q = term.strip()

    # v2.9.0 (USER ORDER 23 Oct): owner ka apna local99k dataset SABSE PEHLE.
    # Phone / TG-ID / username teeno yahan — zero external API, kabhi expire
    # nahi hoga (repo: datasets/tg_numbers.txt). Miss par purani chain aage.
    loc = _tg_local(q)
    if loc:
        return loc
    # API expects q with or without @, both work — we keep as user typed
    # Ensure @ for username? API works with @Perfectamit65 and Perfectamit65 both, but @ is safer for username
    # For numeric ID, no @
    try:
        # URL encode q
        url = f"{TG_API_BASE}?=tg&key={urllib.parse.quote(TG_API_KEY)}&q={urllib.parse.quote(q)}"
        code, body = http_get(url, timeout=20)
        if code == 200 and body:
            # API returns invalid JSON with trailing comma — clean it
            try:
                j = json.loads(body)
            except Exception:
                # remove trailing commas before } or ]
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                j = json.loads(body_clean)
            # Success cases:
            # {"status":"success","data":{"OwnerNumber":"9310837765","TG -ID":"5685588580","Country-Code":"+91"}}
            # {"status":"success","data":[],"M-S-G":"No information found"}
            if j.get("status") == "success" or j.get("status") is True:
                data = j.get("data")
                if isinstance(data, dict) and data.get("OwnerNumber"):
                    # Normalize keys
                    owner = data.get("OwnerNumber") or ""
                    tg_id = data.get("TG -ID") or data.get("TG-ID") or data.get("tg_id") or ""
                    cc = data.get("Country-Code") or "+91"
                    merged = {
                        "owner_number": owner,
                        "tg_id": tg_id,
                        "country_code": cc,
                        "username": q,
                        "mobile": owner,
                        "telegram_id": tg_id,
                    }
                    return {"ok": True, "data": merged}
                # empty list -> not found
                if isinstance(data, list) and len(data)==0:
                    return {"ok": False, "why": "notfound", "msg": f"Telegram {q} ka record nahi mila"}
            # Some responses have status success but data empty
            if isinstance(j.get("data"), list) and len(j.get("data",[]))==0:
                return {"ok": False, "why": "notfound", "msg": f"Telegram {q} ka record nahi mila"}
    except Exception as e:
        # fallback to proxy if native fails
        pass
    # v2.6.2 — 18 Oct 2026: SUPABASE Demo route ZINDA hai (user ne URL di,
    # khud test kiya: key=Demo telegramtonumber par ALLOWED — username aur
    # numeric ID dono par asli data, ~8-10s). Pehle ye hub ke PROXY list me
    # tha par native (sarkari) ka notfound usse kabhi try hi nahi hone deta
    # tha. Ab order: sarkari -> supabase Demo -> t.me public -> honest error.
    # v2.7.1: purana supabase host NXDOMAIN (project delete) — ab URL env se.
    # Naya source mile toh sirf TG_SUPABASE_URL (+KEY) Render me daalo, code na chhuo.
    try:
        _sbase = os.environ.get("TG_SUPABASE_URL", "").strip().rstrip("/")
        _skey = os.environ.get("TG_SUPABASE_KEY") or "Demo"
        su = (f"{_sbase}?key={urllib.parse.quote(_skey)}&tool=telegramtonumber"
              f"&term={urllib.parse.quote(q)}") if _sbase else ""
        code2, body2 = http_get(su, timeout=25) if su else (None, "")
        if code2 == 200 and body2:
            j2 = json.loads(body2)
            d2 = j2.get("data") if isinstance(j2, dict) else None
            if isinstance(d2, dict) and (d2.get("phone_number") or d2.get("mobile")):
                return {"ok": True, "data": {
                    "mobile": d2.get("phone_number") or d2.get("mobile"),
                    "owner_number": d2.get("phone_number") or d2.get("mobile"),
                    "telegram_id": str(d2.get("telegram_id") or ""),
                    "username": q,
                    "country_code": d2.get("country") or "",
                    "verification": d2.get("verification") or "",
                    "source": "supabase-demo"}}
    except Exception:
        pass
    # v2.6.3 — USER ORDER 18 Oct: TG tool me t.me PUBLIC-profile card ab NAHI
    # dena. Chain: sarkari -> supabase Demo -> "data not found in any database".
    return {"ok": False, "why": "notfound",
            "msg": "Data not found in any database."}

def _native_aadhaar2num(term: str) -> dict:
    """v2.6 — Aadhaar to mobile numbers (sarkariallupdates addhaar2num)."""
    if _aph_first():
        _r = _aph_a2num(term)
        if _r and _r.get("ok"):
            return _r
    try:
        url = f"{SARKARI_API_BASE}?=addhaar2num&key={urllib.parse.quote(SARKARI_API_KEY)}&q={urllib.parse.quote(term)}"
        code, body = http_get(url, timeout=20)
        if code == 200 and body:
            # clean trailing commas if any
            try:
                j = json.loads(body)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                j = json.loads(body_clean)
            if j.get("status") == "success" and isinstance(j.get("data"), list) and j.get("data"):
                # Return first few records
                data_list = j["data"][:5]
                # Build merged
                merged = {
                    "aadhaar": term,
                    "count": j.get("count") or len(data_list),
                    "records": data_list,
                    "primary": data_list[0] if data_list else {},
                }
                # For compatibility, also add fields
                if data_list:
                    first = data_list[0]
                    merged["mobile"] = first.get("phoneNumber") or ""
                    merged["name"] = first.get("name") or ""
                    merged["address"] = first.get("address") or ""
                return {"ok": True, "data": merged}
            if isinstance(j.get("data"), list) and len(j.get("data",[]))==0:
                return {"ok": False, "why": "notfound", "msg": "Aadhaar par koi mobile nahi mila"}
    except Exception:
        pass
    # v2.4.0 APH mirror fallback (same response shape family)
    aj = _aph_get("addhaar2num", f"num={urllib.parse.quote(term)}", timeout=8)
    if isinstance(aj, dict):
        if aj.get("status") == "success" and isinstance(aj.get("data"), list) and aj.get("data"):
            data_list = aj["data"][:5]
            merged = {
                "aadhaar": term,
                "count": aj.get("count") or len(data_list),
                "records": data_list,
                "primary": data_list[0] if data_list else {},
            }
            if data_list:
                first = data_list[0]
                merged["mobile"] = first.get("phoneNumber") or first.get("mobile") or ""
                merged["name"] = first.get("name") or ""
                merged["address"] = first.get("address") or ""
            return {"ok": True, "data": merged}
        if aj.get("status") in ("not_found", False):
            return {"ok": False, "why": "notfound", "msg": "Aadhaar par koi mobile nahi mila"}
    return {"ok": False, "why": "notfound", "msg": "Aadhaar par koi mobile nahi mila"}


def _native_vehicletomobile(term: str) -> dict:
    """v2.6 — Vehicle number to mobile (sarkariallupdates vehicle_to_number)."""
    if _aph_first():
        _r = _aph_v2n(term)
        if _r and _r.get("ok"):
            return _r
    try:
        url = f"{SARKARI_API_BASE}?=vehicle_to_number&key={urllib.parse.quote(SARKARI_API_KEY)}&xr={urllib.parse.quote(term)}"
        code, body = http_get(url, timeout=7)   # v2.18.2: dead source 7s me pakdo (pehle 20s!)
        if code == 200 and body:
            try:
                j = json.loads(body)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                j = json.loads(body_clean)
            if j.get("success") and j.get("mobile_number"):
                merged = {
                    "registration_number": j.get("registration_number") or term,
                    "mobile_number": j.get("mobile_number") or "",
                    "mobile": j.get("mobile_number") or "",
                }
                return {"ok": True, "data": merged}
            if j.get("success") is False:
                return {"ok": False, "why": "notfound", "msg": "Vehicle se mobile nahi mila"}
    except Exception:
        pass
    # v2.4.0 APH mirror fallback
    aj = _aph_get("vehicle_to_number", f"xr={urllib.parse.quote(term)}", timeout=8)
    if isinstance(aj, dict) and aj.get("success") and aj.get("mobile_number"):
        return {"ok": True, "data": {
            "registration_number": aj.get("registration_number") or term,
            "mobile_number": aj.get("mobile_number") or "",
            "mobile": aj.get("mobile_number") or "",
            "source_note": "mirror",
        }}
    return {"ok": False, "why": "notfound", "msg": "Vehicle se mobile nahi mila"}


def _vahanx_rc(term: str) -> dict:
    """v2.26.2 - vahanx.in native-scraper (no-key): same-opener token-flow (test-verified 2x).
    rc-search page se VX_ST.rc token, phir /rc-search/{RC}?t=TOK same-jar+UA se -> 200 hrc-details-card."""
    rc = re.sub(r"[^A-Z0-9]", "", term.strip().upper())
    if not (4 <= len(rc) <= 11):
        return {"ok": False, "why": "validation", "msg": "RC-format galat"}
    try:
        import http.cookiejar as _cj
        UA = "Mozilla/5.0 (Linux; Android 10) Chrome/130.0 Mobile Safari/537.36"
        jar = _cj.CookieJar()
        class _NR(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        op = urllib.request.build_opener(_NR, urllib.request.HTTPCookieProcessor(jar))
        op.addheaders = [("User-Agent", UA)]
        html = op.open("https://vahanx.in/rc-search", timeout=20).read().decode("utf-8", "ignore")
        mt = re.search(r'rc:\s*"([^"]+)"', html)
        if not mt:
            return {"ok": False, "why": "upstream", "msg": "vahanx: token nahi mila"}
        r = op.open(f"https://vahanx.in/rc-search/{rc}?t={urllib.parse.quote(mt.group(1))}", timeout=25)
        body = r.read().decode("utf-8", "ignore")
        if r.status != 200 or "hrc-details-card" not in body:
            return {"ok": False, "why": "notfound", "msg": "vahanx: record nahi mila"}
        seg = body[body.find("hrc-details-card"):body.find("<footer")]
        toks = [t.strip() for t in (x.strip() for x in re.findall(r">([^<>]+)<", seg)) if t.strip()]
        d = {}
        LABELS = ["Owner Name", "Owner Serial No", "Registration Number", "Registered RTO",
                  "Model Name", "Maker Model", "Vehicle Class", "Fuel Type", "Chassis Number",
                  "Engine Number", "Insurance Expiry", "Insurance No", "Insurance Company",
                  "Registration Date", "Vehicle Age", "Fitness Upto", "Tax Upto", "PUC No", "PUC Upto",
                  "City Name", "Phone", "Website", "Address", "Fuel Norms", "Seating Capacity", "Color"]
        for i, t in enumerate(toks):
            if t in LABELS and i + 1 < len(toks):
                v = toks[i + 1]
                if v in LABELS:
                    continue
                d[t] = v
        if not d.get("Owner Name"):
            return {"ok": False, "why": "notfound", "msg": "vahanx: owner-field nahi mila"}
        return {"ok": True, "source": "vahanx-native", "data": {
            "registration_number": d.get("Registration Number") or rc,
            "owner_name": d.get("Owner Name") or "",
            "owner_serial": d.get("Owner Serial No") or "",
            "rto": d.get("Registered RTO") or "",
            "maker": d.get("Model Name") or "",
            "model": d.get("Maker Model") or "",
            "vehicle_class": d.get("Vehicle Class") or "",
            "fuel_type": d.get("Fuel Type") or "",
            "chassis_number": d.get("Chassis Number") or "",
            "engine_number": d.get("Engine Number") or "",
            "insurance_company": d.get("Insurance Company") or "",
            "insurance_upto": d.get("Insurance Expiry") or "",
            "insurance_policy_number": d.get("Insurance No") or "",
            "reg_date": d.get("Registration Date") or "",
            "vehicle_age": (d.get("Vehicle Age") or "").replace("&amp;", "&"),
            "fitness_upto": d.get("Fitness Upto") or "",
            "tax_upto": d.get("Tax Upto") or "",
            "pucc_number": d.get("PUC No") or "",
            "pucc_valid_upto": d.get("PUC Upto") or "",
            "city": d.get("City Name") or "",
            "rto_phone": d.get("Phone") or "",
            "rto_website": d.get("Website") or "",
            "rto_address": d.get("Address") or "",
            "source_note": "vahanx.in public page (chassis/engine/puc/policy masked-by-source)"}}
    except Exception as e:
        return {"ok": False, "why": "error", "msg": f"vahanx: {str(e)[:90]}"}


def _native_vehicle(term: str) -> dict:
    """v2.6 — RC + Challan: sarkariallupdates vehicle_full primary + osint-apis-hub fallback."""
    # v2.6 — Try new sarkariallupdates vehicle_full first (more detailed, unmasked)
    try:
        url_new = f"{SARKARI_API_BASE}?=vehicle_full&key={urllib.parse.quote(SARKARI_API_KEY)}&xr={urllib.parse.quote(term)}"
        code, body = http_get(url_new, timeout=20)
        if code == 200 and body:
            try:
                j = json.loads(body)
            except Exception:
                body_clean = re.sub(r',\s*}', '}', body)
                body_clean = re.sub(r',\s*]', ']', body_clean)
                j = json.loads(body_clean)
            if j.get("success") and j.get("registration_number"):
                # New API returns detailed unmasked data + mobile_number
                merged = {
                    "registration_number": j.get("registration_number") or term,
                    "owner_name": j.get("owner_name") or "",
                    "owner_father_name": j.get("owner_father_name") or "",
                    "model": f"{j.get('manufacturer','')} {j.get('vehicle','')}".strip() or j.get("vehicle") or "",
                    "vehicle": j.get("vehicle") or "",
                    "manufacturer": j.get("manufacturer") or "",
                    "vehicle_class": j.get("vehicle_class") or "",
                    "fuel_type": j.get("fuel_type") or "",
                    "rto": j.get("rto") or j.get("registered_at") or "",
                    "registered_at": j.get("registered_at") or "",
                    "address": j.get("permanent_address") or j.get("present_address") or "",
                    "permanent_address": j.get("permanent_address") or "",
                    "present_address": j.get("present_address") or "",
                    "phone": j.get("mobile_number") or "NA",
                    "mobile_number": j.get("mobile_number") or "",
                    "mobile": j.get("mobile_number") or "",
                    "reg_date": j.get("registration_date") or "",
                    "chassis_number": j.get("chassis_number") or "",
                    "engine_number": j.get("engine_number") or "",
                    "engine_cc": j.get("cubic_capacity") or "",
                    "cubic_capacity": j.get("cubic_capacity") or "",
                    "financer": j.get("financer") or "",
                    "insurance_company": j.get("insurance_company") or "",
                    "insurance_upto": j.get("insurance_upto") or "",
                    "insurance_policy_number": j.get("insurance_policy_number") or "",
                    "pucc_number": j.get("pucc_number") or "",
                    "pucc_valid_upto": j.get("pucc_valid_upto") or "",
                    "seat_capacity": j.get("seat_capacity") or "",
                    "variant": j.get("variant") or "",
                    "vehicle_type": j.get("vehicle_type") or "",
                    "rc_status": j.get("rc_status") or "ACTIVE",
                    "source": "sarkari_vehicle_full",
                }
                return {"ok": True, "data": merged}
    except Exception:
        pass

    # Fallback to old osint-apis-hub
    rc_data = None
    try:
        url_rc = f"{VEHICLE_API_BASE}/vehicle-rc?key={urllib.parse.quote(VEHICLE_API_KEY)}&number={urllib.parse.quote(term)}"
        code, body = http_get(url_rc, timeout=20)
        if code == 200 and body:
            j = json.loads(body)
            if j.get("success") and j.get("data"):
                rc_data = j["data"]
    except Exception:
        pass
    if not rc_data:
        return {"ok": False, "why": "notfound", "msg": "RC record nahi mila — number check karo ya provider key expire"}
    merged = {}
    if rc_data:
        sec = rc_data.get("sections") or {}
        own = sec.get("ownership_details") or {}
        veh = sec.get("vehicle_details") or {}
        imp = sec.get("important_dates") or {}
        ins = sec.get("insurance_information") or {}
        oth = sec.get("other_information") or {}
        vinfo = rc_data.get("vehicle_info") or {}
        merged["registration_number"] = vinfo.get("vehicle_number") or own.get("Registration Number") or term
        merged["owner_name"] = vinfo.get("owner_name") or own.get("Owner Name") or ""
        merged["model"] = f"{veh.get('Model Name','')} {veh.get('Maker Model','')}".strip() or vinfo.get("model_name") or ""
        merged["vehicle_class"] = veh.get("Vehicle Class") or ""
        merged["fuel_type"] = veh.get("Fuel Type") or ""
        merged["fuel_norms"] = veh.get("Fuel Norms") or ""
        merged["city"] = vinfo.get("city_name") or ""
        merged["rto"] = own.get("Registered RTO") or vinfo.get("code") or ""
        merged["address"] = vinfo.get("address") or ""
        merged["phone"] = vinfo.get("phone") or "NA"
        merged["reg_date"] = imp.get("Registration Date") or ""
        merged["fitness_upto"] = imp.get("Fitness Upto") or ""
        merged["insurance_upto"] = imp.get("Insurance Upto") or ins.get("Insurance Expiry") or ""
        merged["insurance_company"] = ins.get("Insurance Company") or ""
        merged["puc_upto"] = imp.get("PUC Upto") or ""
        merged["puc_status"] = imp.get("PUC Expiry In") or ""
        merged["engine_cc"] = oth.get("Cubic Capacity") or ""
        merged["financer"] = oth.get("Financer Name") or ""
        merged["blacklist"] = oth.get("Blacklist Status") or ""
        merged["tax_upto"] = imp.get("Tax Upto") or ""
        merged["vehicle_age"] = imp.get("Vehicle Age") or ""
        merged["rc_status"] = "ACTIVE"
    if not merged.get("registration_number"):
        merged["registration_number"] = term

    return {"ok": True, "data": merged, "raw": json.dumps(merged)[:800]}

def _native_num(term: str) -> dict:
    """v2.7 — num ab NATIVE (sarkariallupdates ?=num, muft) — stable, koi proxy nahi."""
    q = urllib.parse.quote(term)
    url = f"{SARKARI_API_BASE}?=num&key={urllib.parse.quote(SARKARI_API_KEY)}&q={q}&xr={q}&num={q}&term={q}"
    code, body = http_get(url, timeout=5)          # v2.18.2: mara hua source 5s me pakdo, 9s nahi

    if _aph_first():
        aj0 = _aph_get("num", f"num={q}", timeout=7)
        if isinstance(aj0, dict) and aj0.get("status") == "success" and isinstance(aj0.get("data"), list) and aj0.get("data"):
            j0 = aj0
            lst0 = j0["data"]

            def _s(v):
                t = str(v if v is not None else "").strip()
                return "" if t.lower() in ("n/a", "na", "none", "null", "-", "0") else t

            f0 = lst0[0]
            d0 = {
                "owner_name": _s(f0.get("name")),
                "father_name": _s(f0.get("fname")),
                "mobile_no": _s(f0.get("mobile")),
                "alt_mobile": _s(f0.get("alt")),
                "address": _s(f0.get("address")),
                "circle": _s(f0.get("circle")),
                "aadhar_card_no": _s(f0.get("id")),
            }
            for i, r0 in enumerate(lst0[1:6], 1):
                if isinstance(r0, dict) and _s(r0.get("name")):
                    d0[f"owner_name_{i}"] = _s(r0.get("name"))
            return {"ok": True, "data": d0, "source": "aph-mirror"}
    j = None
    if code == 200 and body:
        try:
            j = json.loads(body)
        except Exception:
            j = None
    if not (isinstance(j, dict) and isinstance(j.get("data"), list) and j.get("data")):
        # v2.4.0 APH mirror fallback (sarkari 509/transport-fail par)
        aj = _aph_get("num", f"num={q}", timeout=8)
        if isinstance(aj, dict) and aj.get("status") == "success" and isinstance(aj.get("data"), list) and aj.get("data"):
            j = aj
    if j is None:
        return {"ok": False, "why": "upstream", "msg": "number source abhi jawab nahi de raha — thodi der baad try karo"}
    lst = j.get("data") if isinstance(j, dict) else None
    if not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
        return {"ok": False, "why": "notfound", "msg": "is number ka record kisi database me nahi mila"}

    def s(v):
        t = str(v if v is not None else "").strip()
        return "" if t.lower() in ("n/a", "na", "none", "null", "-", "0") else t

    f = lst[0]
    d = {
        "owner_name": s(f.get("name")),
        "father_name": s(f.get("fname")),
        "mobile_no": s(f.get("mobile")),
        "alt_mobile": s(f.get("alt")),
        "address": s(f.get("address")),
        "circle": s(f.get("circle")),
        "aadhar_card_no": s(f.get("id")),
    }
    # extra records → owner_name_1.. (clients rec-count nikaalte hain)
    for i, r in enumerate(lst[1:6], 1):
        if not isinstance(r, dict):
            continue
        nm = s(r.get("name"))
        if nm:
            d[f"owner_name_{i}"] = nm
            mn = s(r.get("mobile"))
            if mn:
                d[f"mobile_no_{i}"] = mn
    d = {k: v for k, v in d.items() if v}
    if not d.get("owner_name") and not d.get("mobile_no"):
        return {"ok": False, "why": "notfound", "msg": "is number ka record kisi database me nahi mila"}
    return {"ok": True, "data": d, "raw": ""}


def _native_familyinfo(term: str) -> dict:
    """v2.19.1 — familyinfo ab sarkariallupdates ?=family (live, instant local cache).
    Pehle ?=ration tha jo 20-25s me timeout ho gaya (source ne endpoint badla).
    Output flat card + members list (naam, relation, member_id, aadhaar_linked)."""

    if _aph_first():
        _r = _aph_family(term)
        if _r and _r.get("ok"):
            return _r
    q = urllib.parse.quote(str(term).strip())
    url = f"{SARKARI_API_BASE}?=family&key={urllib.parse.quote(SARKARI_API_KEY)}&q={q}&xr={q}&term={q}"
    code, body = http_get(url, timeout=6)   # v2.4.1 fail-fast (509 turant; 12s waste tha)
    j = None
    if code == 200 and body:
        try:
            j = json.loads(body)
        except Exception:
            j = None
    # v2.4.0 APH mirror fallback (family) — tolerant shape mapping
    if not (isinstance(j, dict) and isinstance(j.get("data"), (dict, list))):
        aj = _aph_get("family", f"q={urllib.parse.quote(str(term).strip())}", timeout=8)
        if isinstance(aj, dict) and (aj.get("status") is True or aj.get("status") == "success"):
            d0 = aj.get("data")
            mem = d0.get("members") if isinstance(d0, dict) else (d0 if isinstance(d0, list) else None)
            if mem:
                norm = []
                for m in mem:
                    if not isinstance(m, dict):
                        continue
                    norm.append({
                        "name": m.get("name") or "",
                        "relation": m.get("relation") or m.get("relation_name") or "",
                        "member_id": m.get("member_id") or m.get("member_id_no") or "",
                        "aadhaar_linked": m.get("aadhaar_linked") or m.get("aadhaar") or "",
                    })
                if norm:
                    dd = d0 if isinstance(d0, dict) else {}
                    j = {"data": {
                        "head_of_family": dd.get("head_of_family") or (norm[0].get("name") or ""),
                        "ration_card_number": dd.get("ration_card_number") or "",
                        "scheme": dd.get("scheme") or "",
                        "state": dd.get("state") or "",
                        "district": dd.get("district") or "",
                        "fps_id": dd.get("fps_id") or "",
                        "total_members": dd.get("total_members") or len(norm),
                        "members": norm,
                        "source_note": "public ledger (mirror)",
                    }}
    if j is None:
        return {"ok": False, "why": "upstream", "msg": "family source abhi jawab nahi de raha — thodi der baad try karo"}
    d = j.get("data") if isinstance(j, dict) else None
    if not isinstance(d, dict) or not d.get("members"):
        return {"ok": False, "why": "notfound", "msg": "Is reference par koi family record nahi mila."}
    members = []
    for m in d.get("members") or []:
        if not isinstance(m, dict):
            continue
        members.append({
            "name": m.get("name") or "",
            "relation": m.get("relation") or "",
            "member_id": m.get("member_id") or "",
            "aadhaar_linked": m.get("aadhaar_linked") or "",
        })
    return {
        "ok": True,
        "data": {
            "head_of_family": d.get("head_of_family") or "",
            "ration_card_number": d.get("ration_card_number") or "",
            "scheme": d.get("scheme") or "",
            "state": d.get("state") or "",
            "district": d.get("district") or "",
            "fps_id": d.get("fps_id") or "",
            "total_members": d.get("total_members") or len(members),
            "members": members,
            "source_note": "public ration/ledger source",
        },
    }


#  datasets/bizpart_* = gzip chunks (90MB + 75MB, GitHub-safe) of the
#  sqlite index built offline from owner's 228MB sheet (1.72M rows).
#  Never expires — data repo ke andar, koi external API nahi.
#  Boot/first-hit: stream-concat parts -> gunzip -> data/business.sqlite
#  (data/ gitignored; ephemeral disk). Prewarm thread at import
#  (BIZ_WARM=0 tests ke liye, BIZ_DB_PATH = fixture override).
# ══════════════════════════════════════════════════════════════════
import contextlib as _contextlib
import gzip as _gzip
import sqlite3 as _sqlite3

# v2.12 — owner ke APNE free-forever tools (offline + self-hosted)
try:
    import native_free as _NF
except Exception:                                    # noqa: BLE001
    _NF = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIZ_PARTS_DIR = os.path.join(_HERE, "datasets")
_BIZ_DB = (os.environ.get("BIZ_DB_PATH") or "").strip() or os.path.join(_HERE, "data", "business.sqlite")
_BIZ_READY = False
_BIZ_ERR = ""
_BIZ_LOCK = threading.Lock()


class _CatReader:
    """gzip parts ko ek sequential file-like banao (byte-exact concat)."""
    def __init__(self, paths):
        self._fps = [open(p, "rb") for p in paths]
        self._i = 0

    def read(self, n=-1):
        if n is None or n < 0:
            out = b"".join(fp.read() for fp in self._fps[self._i:])
            self._i = len(self._fps)
            return out
        while self._i < len(self._fps):
            chunk = self._fps[self._i].read(n)
            if chunk:
                return chunk
            self._i += 1
        return b""

    def close(self):
        for fp in self._fps:
            with _contextlib.suppress(Exception):
                fp.close()


def _biz_ensure() -> bool:
    """DB ready? fixture = instant, prod = parts se ek baar decompress."""
    global _BIZ_READY, _BIZ_ERR
    if _BIZ_READY:
        return True
    with _BIZ_LOCK:
        if _BIZ_READY:
            return True
        try:
            fixture = bool(os.environ.get("BIZ_DB_PATH"))
            if os.path.exists(_BIZ_DB) and (fixture or os.path.getsize(_BIZ_DB) > 100_000_000):
                _BIZ_READY = True
                return True
            if fixture:
                _BIZ_ERR = "fixture missing"
                return False
            import glob as _glob
            parts = sorted(_glob.glob(os.path.join(_BIZ_PARTS_DIR, "bizpart_*")))
            if not parts:
                _BIZ_ERR = "bizpart_* missing in datasets/"
                return False
            os.makedirs(os.path.dirname(_BIZ_DB), exist_ok=True)
            tmp = _BIZ_DB + ".tmp"
            cat = _CatReader(parts)
            gzf = _gzip.GzipFile(fileobj=cat, mode="rb")
            with open(tmp, "wb") as out:
                while True:
                    b = gzf.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
            gzf.close()
            cat.close()
            os.replace(tmp, _BIZ_DB)
            _BIZ_READY = True
            return True
        except Exception as e:
            _BIZ_ERR = str(e)[:140]
            return False


_BIZ_TLS = threading.local()


def _biz_conn():
    """Persistent thread-local RO connection — mmap + warm cache (fast repeat lookups)."""
    c = getattr(_BIZ_TLS, "c", None)
    if c is None:
        c = _sqlite3.connect(f"file:{_BIZ_DB}?mode=ro", uri=True)
        with _contextlib.suppress(Exception):
            c.execute("PRAGMA cache_size=-8000")
            c.execute("PRAGMA mmap_size=1073741824")
        _BIZ_TLS.c = c
    return c


def _biz_ensure() -> bool:
    """DB ready? fixture = instant, prod = parts se ek baar decompress."""
    global _BIZ_READY, _BIZ_ERR
    if _BIZ_READY:
        return True
    with _BIZ_LOCK:
        if _BIZ_READY:
            return True
        try:
            fixture = bool(os.environ.get("BIZ_DB_PATH"))
            if os.path.exists(_BIZ_DB) and (fixture or os.path.getsize(_BIZ_DB) > 100_000_000):
                _BIZ_READY = True
                return True
            if fixture:
                _BIZ_ERR = "fixture missing"
                return False
            import glob as _glob
            parts = sorted(_glob.glob(os.path.join(_BIZ_PARTS_DIR, "bizpart_*")))
            if not parts:
                _BIZ_ERR = "bizpart_* missing in datasets/"
                return False
            os.makedirs(os.path.dirname(_BIZ_DB), exist_ok=True)
            tmp = _BIZ_DB + ".tmp"
            cat = _CatReader(parts)
            gzf = _gzip.GzipFile(fileobj=cat, mode="rb")
            with open(tmp, "wb") as out:
                while True:
                    b = gzf.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
            gzf.close()
            cat.close()
            os.replace(tmp, _BIZ_DB)
            _BIZ_READY = True
            return True
        except Exception as e:
            _BIZ_ERR = str(e)[:140]
            return False


_BIZ_TLS = threading.local()


def _biz_conn():
    """Persistent thread-local RO connection — mmap + warm cache (fast repeat lookups)."""
    c = getattr(_BIZ_TLS, "c", None)
    if c is None:
        c = _sqlite3.connect(f"file:{_BIZ_DB}?mode=ro", uri=True)
        with _contextlib.suppress(Exception):
            c.execute("PRAGMA cache_size=-8000")
            c.execute("PRAGMA mmap_size=1073741824")
        _BIZ_TLS.c = c
    return c


def _native_phonebiz(term: str) -> dict:
    """v2.10 — phone number -> business info (owner's local 1.72M index).
    Input: landline (011-29523401 / 911129523401) ya mobile (9876543210)."""
    if not _biz_ensure():
        return {"ok": False, "why": "building",
                "msg": f"⏳ PHONEBIZ dataset pending — boss apni 1.5GB files de de to ye tool turant LIVE (apna-DB me index hoga). ({_BIZ_ERR or 'bizpart files chahiye'})"}
    d = re.sub(r"\D", "", str(term or ""))
    if not (8 <= len(d) <= 15):
        return {"ok": False, "why": "validation", "msg": "8-15 digit phone number bhejo"}
    variants = []

    def _add(x):
        if x and 8 <= len(x) <= 15 and x not in variants:
            variants.append(x)

    _add(d)
    if d.startswith("91") and len(d) > 11:
        _add(d[2:])
    if d.startswith("0"):
        _add(d[1:])
    if len(d) == 10:
        _add("91" + d)

    conn = _biz_conn()
    try:
        for v in variants:
            row = conn.execute(
                """SELECT b.rid,b.sheet,b.pin,b.p_extra,b.p1,b.p2,b.p3,
                          b1.txt,b2.txt,b3.txt,b4.txt,b5.txt,b6.txt,b7.txt,b8.txt
                     FROM ph p JOIN biz b ON p.rid=b.rid
                     LEFT JOIN sst b1 ON b.s_biz=b1.idx
                     LEFT JOIN sst b2 ON b.s_addr=b2.idx
                     LEFT JOIN sst b3 ON b.s_area=b3.idx
                     LEFT JOIN sst b4 ON b.s_city=b4.idx
                     LEFT JOIN sst b5 ON b.s_email=b5.idx
                     LEFT JOIN sst b6 ON b.s_web=b6.idx
                     LEFT JOIN sst b7 ON b.s_state=b7.idx
                     LEFT JOIN sst b8 ON b.s_x1=b8.idx
                    WHERE p.phone=? LIMIT 1""", (v,)).fetchone()
            if not row:
                continue
            rid, sheet, pin, p_extra = row[0], row[1], row[2] or "", row[3] or ""
            b_name, b_addr, b_area, b_city, b_mail, b_web, b_dnd, b_state = row[7:15]
            # phones = biz row ke p1/p2/p3/p_extra (ph-by-rid scan tha 600ms ka source — ab nahi)
            phones = []
            for _p in (row[4], row[5], row[6], p_extra):
                _d = re.sub(r"\D", "", str(_p or ""))
                if len(_d) > 11 and _d.startswith("91"):
                    _d = _d[2:]
                elif len(_d) == 11 and _d.startswith("0"):
                    _d = _d[1:]
                if _d and 8 <= len(_d) <= 15 and _d not in phones:
                    phones.append(_d)
            data = {
                "business_name": b_name or "",
                "street_address": b_addr or "",
                "area": b_area or "",
                "city": b_city or "",
                "state": (b_state or "").strip(),
                "pincode": pin,
                "email": b_mail or "",
                "website": b_web or "",
                "dnd_status": b_dnd or "",
                "sheet": sheet,
                "phones": ", ".join(phones),
                "matched_phone": v,
                "source": "owner-biz-dataset (1,716,693 records)",
            }
            return {"ok": True, "data": data}
        return {"ok": False, "why": "notfound",
                "msg": f"Number {d} business directory me nahi mila"}
    finally:
        pass  # thread-local conn persistent — warm mmap cache


if os.environ.get("BIZ_WARM", "1") != "0":
    threading.Thread(target=_biz_ensure, daemon=True).start()


# ---- v2.12 thin wrappers: native_free module ko gateway contract me laao ----
def _nf_call(fn_name: str, term: str) -> dict:
    if _NF is None:
        return {"ok": False, "why": "unavailable", "msg": "native_free module load nahi hua"}
    try:
        return getattr(_NF, fn_name)(term)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "why": "error", "msg": f"{type(e).__name__}: {str(e)[:80]}"}


def _nf_plate(term):        return _nf_call("plate_decode", term)
def _nf_aadhaarcheck(term): return _nf_call("aadhaar_check", term)
def _nf_gstcheck(term):     return _nf_call("gst_check", term)
def _nf_webextract(term):   return _nf_call("webextract", term)


# =====================================================================
#  v2.15 — DUAL SOURCE (comparison ke baad ka faisla)
#  Har tool ke liye PRIMARY + FALLBACK. Order live measurement se decide:
#    num            : sarkari 1.8s  > xtrace 10.4s      -> native primary
#    vehicle        : xtrace 13s (chalti hai) > native 2.5s (notfound) -> xtrace primary
#    vehicletomobile: native 6.7s (chalti) > xtrace 10.6s (kuch gaadi 404) -> native primary
#    aadhaar2num    : native 0.97s > xtrace 12.1s       -> native primary
#    familyinfo     : native 500 ERROR (mara) > xtrace 12.1s -> xtrace primary
#    tc / aadhaar   : sirf xtrace
#  Dono zinda rehte hain: ek fail/expire ho to dusra apne aap sambhal leta hai.
# =====================================================================
_XT = None


def _xt():
    global _XT
    if _XT is None:
        try:
            import xtrace
            _XT = xtrace
        except Exception as e:
            _XT = False
            try:
                print("[xtrace] load fail:", e, flush=True)
            except Exception:
                pass
    return _XT or None


def xtrace_state() -> dict:
    x = _xt()
    if not x:
        return {"error": "xtrace module load nahi hua"}
    st = x.state()
    exp = st.get("expiry")
    days_left = None
    if exp:
        try:
            import datetime as _dt
            d = _dt.datetime.strptime(str(exp), "%Y-%m-%d").date()
            days_left = (d - _dt.date.today()).days
        except Exception:
            days_left = None
    st["days_left"] = days_left
    st["expiring_soon"] = bool(days_left is not None and days_left <= 3)
    st["note"] = ("provider expire hone wala hai — XTRACE_KEY_* env me nayi key daalo "
                  "(code change nahi)") if st["expiring_soon"] else "ok"
    st["kill_switch"] = "XTRACE_ENABLED=0 se ye pack band ho jaata hai (kuch nahi tootta)"
    st["no_code_change_fallback"] = ("kisi bhi naye provider ke liye env me "
                                     "TC_API_URL / AADHAAR_API_URL / VEHICLE_API_URL daal do")
    return st


def _xcall(tool_key: str, term: str) -> dict:
    x = _xt()
    if not x:
        return {"ok": False, "why": "unavailable", "msg": "xtrace module available nahi", "source": "xtrace", "ms": 0}
    try:
        return x.HANDLERS[tool_key](term)
    except Exception as e:
        return {"ok": False, "why": "error", "msg": "xtrace error: " + str(e)[:60], "source": "xtrace", "ms": 0}


# =====================================================================
#  v2.15.1 — PERMANENT PROVIDER SLOTS (env-driven, code change NAHI)
#  Kisi bhi tool ke liye kal naya provider aaye to bas Render env me daalo:
#      TC_API_URL=https://kisi-bhi-provider.com/tc?key=XXX&query=
#  (ya <TOOL>_API_URL + <TOOL>_API_KEY). Server restart -> tool us provider
#  se chalne lagega. Purana provider expire ho jaye to bhi API nahi marti.
# =====================================================================
def _env_provider(tool: str):
    url_tpl = (os.environ.get(f"{tool.upper()}_API_URL") or "").strip()
    if not url_tpl:
        return None
    key = (os.environ.get(f"{tool.upper()}_API_KEY") or "").strip()

    def _run(term: str) -> dict:
        u = url_tpl.replace("{term}", urllib.parse.quote(str(term)))
        if "{term}" not in url_tpl:
            sep = "&" if "?" in u else "?"
            u = u + sep + "query=" + urllib.parse.quote(str(term))
            if key and "key=" not in u:
                u = u + "&key=" + urllib.parse.quote(key)
        elif key and "key=" not in u:
            u = u + ("&" if "?" in u else "?") + "key=" + urllib.parse.quote(key)
        code, body = http_get(u, timeout=15)
        if code != 200 or not body:
            return {"ok": False, "why": "upstream", "msg": tool + " provider jawab nahi de raha", "source": "custom"}
        try:
            j = json.loads(body)
        except Exception:
            return {"ok": False, "why": "bad_json", "msg": tool + " provider ka jawab theek nahi", "source": "custom"}
        data = j.get("data") if isinstance(j, dict) else None
        if isinstance(j, dict) and (data or j.get("result")):
            return {"ok": True, "data": data if isinstance(data, dict) else {"result": data or j.get("result")},
                    "source": "custom", "raw": j}
        if isinstance(j, dict) and j.get("ok") is False:
            return {"ok": False, "why": j.get("why") or "notfound", "msg": str(j.get("msg") or "")[:120], "source": "custom"}
        return {"ok": False, "why": "notfound", "msg": tool + " provider: record nahi mila", "source": "custom"}
    return _run


# branding jo kabhi user ko nahi dikhni chahiye (provider ke naam/telegram)
_BRAND_FIELDS = {"developer", "credit", "credits_left", "expiry_date", "credit_type",
                 "powered_by", "owner_contact", "telegram", "channel", "key_details"}


def sanitize(obj, owner: str = "HIMANSHU"):
    """Provider ke naam/telegram/credits ko jawab se hata do; apna naam lagao."""
    def _walk(o):
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()
                    if str(k).lower() not in _BRAND_FIELDS}
        if isinstance(o, list):
            return [_walk(x) for x in o]
        return o
    out = _walk(obj)
    if isinstance(out, dict):
        out.setdefault("api", owner)
    return out


HEDGE_MS = int(os.environ.get("CHAIN_HEDGE_MS", "1500"))


# ── v2.18.2 SOURCE CIRCUIT BREAKER ─────────────────────────────────────
# Ek source lagataar timeout/upstream fail ho raha ho (jaise sarkari num aaj),
# to har user uske 9-12s ka intezaar karta tha aur phir FAIL milta tha.
# Ab: 2 lagataar transport-fail => source 10 min ke liye SKIP (auto-recover).
_SRC_FAILS = {}                                  # name -> [consec_fail, skip_until_ts]
_SRC_LOCK = threading.Lock()
_SRC_BREAK_AFTER = int(os.environ.get("SRC_BREAK_AFTER", "2"))
_SRC_COOLDOWN = int(os.environ.get("SRC_COOLDOWN_SEC", "600"))
_NO_BREAK_WHY = ("notfound", "validation", "bad_json", "rate_limit")   # ye jawab hai, galti nahi


def _src_ok(name: str) -> bool:
    with _SRC_LOCK:
        f = _SRC_FAILS.get(name)
        return not f or time.time() >= f[1]


def _src_mark(name: str, ok: bool, why: str = "") -> None:
    if ok or (why or "").lower() in _NO_BREAK_WHY:
        with _SRC_LOCK:
            _SRC_FAILS.pop(name, None)
        return
    with _SRC_LOCK:
        f = _SRC_FAILS.setdefault(name, [0, 0])
        f[0] += 1
        if f[0] >= _SRC_BREAK_AFTER:
            f[1] = time.time() + _SRC_COOLDOWN


def _chain(*providers):
    """Ordered provider chain — PEHLA JO OK DE WAHI JAWAB.

    v2.17 SPEED — HEDGING:
    Provider 1 agar HEDGE_MS (1.5s) me jawab na de, to provider 2 bhi SAATH ME
    start kar diya jaata hai. Jo pehle success de, wahi return hota hai.
    Isse slow-chain wale tools (vehicletomobile 16.7s, familyinfo 13.7s)
    ~7-11s me aa jaate hain — kyunki dono source parallel chalte hain.
    (Normal sequential me 6.7s + 10.6s = 17s lagta tha.)
    """
    chain = [(n, f) for n, f in providers if f is not None]
    # breaker: jo source abhi "bimaar" hai use is request se nikaal do
    # (agar SAB bimaar hai to sab chala do — kuch na karne se accha)
    _alive = [p for p in chain if _src_ok(p[0])]
    if _alive:
        chain = _alive

    def _run(term: str) -> dict:
        if not chain:
            return {"ok": False, "why": "unavailable", "msg": "koi provider nahi"}
        results, lock, done = {}, threading.Lock(), threading.Event()
        order = {name: i for i, (name, _fn) in enumerate(chain)}

        def _worker(name, fn):
            try:
                r = fn(term)
            except Exception as e:
                r = {"ok": False, "why": "error", "msg": str(e)[:60]}
            try:
                _src_mark(name, bool(r.get("ok")), str(r.get("why") or ""))
            except Exception:
                pass
            with lock:
                results[name] = r
                if r.get("ok"):
                    done.set()

        started = []
        # 1) pehla provider turant
        t = threading.Thread(target=_worker, args=chain[0], daemon=True)
        t.start()
        started.append(chain[0][0])
        deadline = time.time() + HEDGE_MS / 1000.0
        # 2) hedge window ke andar success? warna agla bhi start karo
        idx = 1
        while not done.is_set() and idx < len(chain):
            # v2.24.4 SPEED: 0.02s poll-loop HATAO — event-wait (done.set() par turant uthta hai,
            # warna deadline tak exactly ek baar block; CPU zero, latency zero)
            _left = deadline - time.time()
            if _left > 0:
                done.wait(_left)
            if done.is_set():
                break
            n, fn = chain[idx]
            threading.Thread(target=_worker, args=(n, fn), daemon=True).start()
            started.append(n)
            idx += 1
            deadline = time.time() + HEDGE_MS / 1000.0
        # 3) sabka jawab aane ka intezaar (bounded)
        tmo = float(os.environ.get("CHAIN_WAIT_SEC", "26"))
        end = time.time() + tmo
        while time.time() < end and not done.is_set():
            with lock:
                if len(results) >= len(started) and idx >= len(chain):
                    break
            # v2.24.4 SPEED: yahan bhi event-wait (pehle 0.02s sleep-spin tha)
            done.wait(min(0.2, max(0.0, end - time.time())))

        with lock:
            ok = [(n, r) for n, r in results.items() if r.get("ok")]
            ok.sort(key=lambda kv: order.get(kv[0], 99))
            if ok:
                name, r = ok[0]
                r = dict(r)
                r.setdefault("source", name)
                failed = [n for n in started if n != name and n in results]
                if failed:
                    r["fallback_used"] = True
                    r["tried"] = started
                    r["primary_failed"] = (results.get(chain[0][0]) or {}).get("why")
                return r
            last = None
            for n, _fn in chain:
                rr = results.get(n)
                if rr is not None:
                    last = rr
            out = dict(last or {"ok": False, "why": "timeout", "msg": "sab provider slow/fail"})
            out["tried"] = started
            return out
    return _run


def _dual(primary, secondary, primary_name="native", secondary_name="fallback"):
    """purana API — andar se _chain hi hai."""
    return _chain((primary_name, primary), (secondary_name, secondary))


# =====================================================================
#  v2.20 — SHURUU-API SILENT FALLBACKS (env-driven)
#  Demo key "HUNTER": daily_limit 500 & expires 2026-10-29 — isliye ye
#  sab chains me AAKHRI position par hain (primary kabhi nahi).
#  Personal/unlimited key le aaye to sirf Render env me daalo:
#      SHURU_API_KEY=<naya-key>   (base bhi badalna ho to SHURU_API_BASE)
#  Service restart -> ye sources khud naye key se chalenge. Code nahi chhoona.
#  NOTE: lpg_info (Gateway 403 — dead) aur truecaller_info (numerology junk,
#  real TC data nahi) jaan-boojh kar SKIP kiye — baaki 4 healthy.
# =====================================================================
SHURU_API_BASE = (os.environ.get("SHURU_API_BASE") or "https://shuruuu-api.vercel.app").strip().rstrip("/")
# v2.21.4 — wrapper sources bhi env se badal sakte hain (code nahi chhoona kabhi)
FASTAG_API_BASE = (os.environ.get("FASTAG_API_BASE") or "https://abhigyan-fastag-info.vercel.app").strip().rstrip("/")
SHURU_API_KEY = (os.environ.get("SHURU_API_KEY") or "HUNTER").strip()


def _shuruu_get(path: str, term: str, timeout: int = 10):
    """Shuruu endpoint -> parsed dict ya None (koi bhi error = None)."""
    try:
        url = (f"{SHURU_API_BASE}/{path}?key={urllib.parse.quote(SHURU_API_KEY)}"
               f"&query={urllib.parse.quote(term)}")
        code, body = http_get(url, timeout=timeout)
        if code != 200 or not body:
            return None
        j = json.loads(body)
        if not isinstance(j, dict) or j.get("error"):
            return None
        return j
    except Exception:
        return None


def _shuruu_clean(v) -> str:
    t = str(v if v is not None else "").strip()
    return "" if t.lower() in ("n/a", "na", "none", "null", "-", "0") else t


def _shuruu_num(term: str) -> dict:
    """num_info: mobile -> identity records. Shape native num jaisi."""
    j = _shuruu_get("num_info", term)
    lst = j.get("result") if isinstance(j, dict) else None
    if not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
        return {"ok": False, "why": "notfound", "msg": "is number ka record kisi database me nahi mila"}
    # dedup (source same record duplicate bhejta hai)
    seen, recs = set(), []
    for r in lst:
        if not isinstance(r, dict):
            continue
        key = (_shuruu_clean(r.get("mobile")), (r.get("name") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        recs.append(r)
    if not recs:
        return {"ok": False, "why": "notfound", "msg": "is number ka record kisi database me nahi mila"}
    f = recs[0]
    d = {
        "owner_name": _shuruu_clean(f.get("name")),
        "father_name": _shuruu_clean(f.get("fname")),
        "mobile_no": _shuruu_clean(f.get("mobile")),
        "alt_mobile": _shuruu_clean(f.get("alt")),
        "address": _shuruu_clean(f.get("address")),
        "circle": _shuruu_clean(f.get("circle")),
        "aadhar_card_no": _shuruu_clean(f.get("aadhar")),
    }
    for i, r in enumerate(recs[1:6], 1):
        nm = _shuruu_clean(r.get("name"))
        if nm:
            d[f"owner_name_{i}"] = nm
            mn = _shuruu_clean(r.get("mobile"))
            if mn:
                d[f"mobile_no_{i}"] = mn
    d = {k: v for k, v in d.items() if v}
    if not d:
        return {"ok": False, "why": "notfound", "msg": "is number ka record kisi database me nahi mila"}
    return {"ok": True, "data": d, "raw": ""}


def _shuruu_aadhaar2num(term: str) -> dict:
    """aadhar_info: aadhaar ref -> mobile records. Native aadhaar2num shape."""
    j = _shuruu_get("aadhar_info", term, timeout=12)
    lst = j.get("result") if isinstance(j, dict) else None
    if not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
        return {"ok": False, "why": "notfound", "msg": "Aadhaar par koi mobile nahi mila"}
    seen, recs = set(), []
    for r in lst:
        if not isinstance(r, dict):
            continue
        key = (_shuruu_clean(r.get("mobile")), (r.get("name") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        recs.append({
            "mobile": _shuruu_clean(r.get("mobile")),
            "name": _shuruu_clean(r.get("name")),
            "fname": _shuruu_clean(r.get("fname")),
            "address": _shuruu_clean(r.get("address")),
            "circle": _shuruu_clean(r.get("circle")),
            "alt": _shuruu_clean(r.get("alt")),
        })
    recs = [r for r in recs if r["mobile"]]
    if not recs:
        return {"ok": False, "why": "notfound", "msg": "Aadhaar par koi mobile nahi mila"}
    merged = {
        "aadhaar": term,
        "count": len(recs),
        "records": recs[:8],
        "primary": recs[0],
        "mobile": recs[0]["mobile"],
        "name": recs[0]["name"],
        "address": recs[0]["address"],
    }
    return {"ok": True, "data": merged}


def _shuruu_familyinfo(term: str) -> dict:
    """family_info: aadhaar ref -> ration family (memberDetailsList). Humari
    familyinfo shape me map: members[{name,relation,member_id,aadhaar_linked}]."""
    j = _shuruu_get("family_info", term, timeout=12)
    pd_ = None
    if isinstance(j, dict) and isinstance(j.get("data"), dict):
        pd_ = j["data"].get("pd")
    if not isinstance(pd_, dict):
        return {"ok": False, "why": "notfound", "msg": "family source abhi jawab nahi de raha — thodi der baad try karo"}
    raw = pd_.get("memberDetailsList") or []
    members, hof = [], ""
    for m in raw:
        if not isinstance(m, dict):
            continue
        rel = str(m.get("releationship_name") or m.get("relationship_name") or "NOT AVAILABLE")
        if rel.strip().upper() == "SELF" and not hof:
            hof = str(m.get("memberName") or "")
        members.append({
            "name": str(m.get("memberName") or ""),
            "relation": rel,
            "member_id": str(m.get("memberId") or ""),
            "aadhaar_linked": "Yes" if str(m.get("uid") or "").strip().lower() == "yes" else "No",
        })
    if not members:
        return {"ok": False, "why": "notfound", "msg": "family source abhi jawab nahi de raha — thodi der baad try karo"}
    return {
        "ok": True,
        "data": {
            "head_of_family": hof,
            "ration_card_number": str(j.get("ration_card") or pd_.get("rcId") or ""),
            "scheme": str(pd_.get("schemeName") or ""),
            "state": str(pd_.get("homeStateName") or ""),
            "district": str(pd_.get("homeDistName") or ""),
            "fps_id": str(pd_.get("fpsId") or ""),
            "total_members": len(members),
            "members": members,
            "source_note": "public ration/ledger source-2",
        },
    }


def _shuruu_v2m(term: str) -> dict:
    """rc_num: vehicle RC -> mobile. Native vehicletomobile shape."""
    j = _shuruu_get("rc_num", term, timeout=12)
    mn = ""
    if isinstance(j, dict) and isinstance(j.get("data"), dict):
        mn = _shuruu_clean(j["data"].get("mobileNo"))
    if not mn:
        return {"ok": False, "why": "notfound", "msg": "Vehicle se mobile nahi mila"}
    return {"ok": True, "data": {"registration_number": term, "mobile_number": mn, "mobile": mn}}


_TAC_DB = None
_TAC_LOCK = threading.Lock()


def _tac_db():
    """v2.22 — 248k devices ka offline TAC database (data/tacdb.pkl.gz, lazy-load once)."""
    global _TAC_DB
    if _TAC_DB is not None:
        return _TAC_DB
    if os.environ.get("IMEI_TACDB", "on").lower() == "off":
        return None
    with _TAC_LOCK:
        if _TAC_DB is not None:
            return _TAC_DB
        try:
            import gzip, pickle as _pk
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tacdb.pkl.gz")
            with gzip.open(p, "rb") as f:
                _TAC_DB = _pk.load(f)
        except Exception:
            _TAC_DB = None
    return _TAC_DB


def _native_imei(term: str) -> dict:
    """v2.22 — IMEI REPORT: Luhn + reporting body + TAC database se BRAND/MODEL/SPECS
    (pure offline — koi network nahi, kabhi nahi marega)."""
    d = re.sub(r"\D", "", str(term))
    if len(d) != 15:
        return {"ok": False, "why": "validation", "msg": "15-digit IMEI bhejo (digits only)"}
    tot = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9: n -= 9
        tot += n
    bodies = {"35": "BABT (UK)", "01": "PTCRB (USA)", "86": "TAF (China)", "91": "BIS (India)",
              "99": "Global", "33": "France", "44": "BABT (UK)", "49": "Germany"}
    data = {
        "imei": d,
        "valid_check_digit": "YES — genuine IMEI structure" if tot % 10 == 0 else "NO — check digit failed (fake/typo)",
        "reporting_body": bodies.get(d[:2], "Unknown reporting body"),
        "tac": d[:8], "fac_sn": d[8:14], "check_digit": d[14],
    }
    db = _tac_db()
    hit = db.get(d[:8]) if db else None
    if hit:
        brand, _, specs = hit.partition("|")
        model = specs
        year = ""
        m = re.search(r",\s*(20[0-2]\d)\s*$", specs)
        if m:
            year = m.group(1)
            model = specs[:m.start()].strip().rstrip(",")
        model = model.split(",", 1)[-1].strip() or model   # "SAMSUNG, SAMSUNG GALAXY..." -> model part
        if model.lower().startswith(brand.lower()):
            model = model[len(brand):].strip(" ,")
        data["device_brand"] = brand.title()
        data["device_model"] = model or specs
        data["device_full"] = specs
        if year:
            data["release_year"] = year
        data["report"] = (f"{brand.title()} {model or ''} — TAC verified in GSMA-style device database"
                          + (f", released {year}" if year else ""))
    else:
        data["device_brand"] = "Not in offline TAC database"
        data["report"] = "Structure valid; model database me is TAC ki entry nahi mili"
    return {"ok": True, "source": "builtin-imei", "data": data}


def _native_aadhaar2pan(term: str) -> dict:
    """v2.23 — Aadhaar -> PAN (masked). Ronak ka free source (subho bhai ka fav tool 😄).
    Env-slot: AADHAAR2PAN_API_URL — expiry pe naya URL daalo, code nahi chhoona."""
    base = (os.environ.get("AADHAAR2PAN_API_URL")
            or "https://api.sarkariallupdates.com/api?api=aadhaar2pan&key=30day&aadhaar2pan={term}").strip()
    url = base.replace("{term}", urllib.parse.quote(str(term)))
    try:
        code, body = http_get(url, timeout=15)
        j = json.loads(body) if (code == 200 and body) else None
    except Exception:
        j = None
    if not isinstance(j, dict):
        return {"ok": False, "why": "upstream", "msg": "aadhaar2pan source jawab nahi de raha"}
    st = str(j.get("status") or "").lower()
    pan = str(j.get("pan_number") or "").strip()
    if st in ("success", "true") and pan:
        return {"ok": True, "source": "sarkari-aadhaar2pan",
                "data": {"aadhaar": str(j.get("aadhaar_number") or term),
                         "pan": pan,
                         "note": "PAN masked by source (aakhri 2 + pehla char dikhte hain)"}}
    if st in ("not_found", "notfound") or "not_found" in str(j.get("message") or "").lower():
        return {"ok": False, "why": "notfound", "msg": "Is Aadhaar ka PAN record nahi mila"}
    return {"ok": False, "why": "upstream", "msg": str(j.get("message") or "source ne theek jawab nahi diya")[:80]}



def _native_fastag(term: str) -> dict:
    """v2.21 — FASTag NETC (RC -> tags/bank/status) — siteapi wala hi source, gateway tool ab."""
    v = re.sub(r"\s+", "", str(term).upper())
    if not re.match(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{3,4}$", v):
        return {"ok": False, "why": "validation", "msg": "Need an RC like RJ14CV0002."}
    url = FASTAG_API_BASE + "/vehtofastaginfo?veh=" + urllib.parse.quote(v)
    j = None
    for attempt in (1, 2):   # v2.21.3: transient fail par 1 auto-retry
        try:
            code, body = http_get(url, timeout=12)   # v2.21.6: hang-risk fix
            j = json.loads(body) if (code == 200 and body) else None
        except Exception:
            j = None
        if isinstance(j, dict) and (j.get("found") or j.get("tags")):
            break
        if attempt == 1:
            time.sleep(1)
    if not isinstance(j, dict) or not (j.get("found") or j.get("tags")):
        return {"ok": False, "why": "notfound", "msg": "No FASTag records for this vehicle."}
    tags = [t for t in (j.get("tags") or []) if isinstance(t, dict)]
    banks = sorted({t.get("bank_name") for t in tags if t.get("bank_name")})
    tags_detail = [(f"{t.get('bank_name','?')} · {t.get('status','?')}"
                    + (f" · issued {t.get('issue_date')}" if t.get("issue_date") else "")
                    + (f" · {t.get('vehicle_class')}" if t.get("vehicle_class") else ""))
                   for t in tags]
    return {"ok": True, "source": "fastag-netc",
            "data": {"vehicle": v, "total_tags": j.get("count", len(tags)),
                     "active_tags": j.get("active_count"), "banks": banks,
                     "tags_detail": tags_detail[:12]}}



def _ldb_num(term: str) -> dict:
    if LDB is None:
        return {"ok": False, "why": "skip"}
    r = LDB.lookup_num(term)
    if not r:
        return {"ok": False, "why": "notfound"}
    if not (r.get("name") or r.get("address")):
        return {"ok": False, "why": "notfound"}
    return {"ok": True, "source": "apna-db", "data": {"mobile": term, "name": r.get("name"),
            "father": r.get("father"), "address": r.get("address"), "circle": r.get("circle"),
            "email": r.get("email")}}


def _ldb_aadhaar(term: str) -> dict:
    if LDB is None:
        return {"ok": False, "why": "skip"}
    r = LDB.lookup_aadhaar(term)
    if not r:
        return {"ok": False, "why": "notfound"}
    recs = r["records"]
    if not recs or not recs[0].get("name"):
        return {"ok": False, "why": "notfound"}
    out = []
    for x in recs:
        out.append({"name": x.get("name"), "father": x.get("father"),
                    "mobile": x.get("mobile"), "address": str(x.get("address") or "").replace("!", ", ")})
    return {"ok": True, "source": "apna-db", "data": {"records": out, "total": len(out)}}


def _ldb_rc(term: str) -> dict:
    if LDB is None:
        return {"ok": False, "why": "skip"}
    r = LDB.lookup_rc(term)
    if not r:
        return {"ok": False, "why": "notfound"}
    return {"ok": True, "source": "apna-db", "data": {"regNo": term, "owner": r.get("owner"),
            "vehicle": r.get("model"), "rtoCode": r.get("rto"), "regDate": r.get("reg_date")}}


def _ldb_tg(term: str) -> dict:
    if LDB is None:
        return {"ok": False, "why": "skip"}
    r = LDB.lookup_tg(term)
    if not r:
        return {"ok": False, "why": "notfound"}
    return {"ok": True, "source": "apna-db", "data": {"username": r.get("username"),
            "tg_id": r.get("tg_id"), "mobile": r.get("mobile"), "owner_number": r.get("mobile")}}


# =====================================================================
#  v2.24.6 — LAKSHITX PROVIDER (boss personal-key, env-swappable)
#  Base: https://lakshitxosintapihuborg.vercel.app/api/{tool}/{KEY}?{param}={val}
#  Key env LAKSHITX_KEY me (expire ho to sirf env badalna hai — code nahi)
#  31 Oct live-verified: number/aadhar/vehicle/ifsc/pincode/imei/gst/upi ✓
# =====================================================================
LX_BASE = (os.environ.get("LAKSHITX_BASE") or "https://lakshitxosintapihuborg.vercel.app/api").strip().rstrip("/")
LX_KEYS = [k.strip() for k in (
    (os.environ.get("LAKSHITX_KEY") or ""),
    (os.environ.get("LAKSHITX_KEY_1D") or ""),
    (os.environ.get("LAKSHITX_KEY_D3") or ""),
) if k.strip()] or [""]
LX_KEY = LX_KEYS[0]  # compat: purana ref


# =====================================================================
#  v2.24.7 — INSTAGRAM PROFILE INFO (NATIVE — koi middleman nahi)
#  Source: storyviewer GCF (public endpoint, storyviewer-7a64d.cloudfunctions.net)
#  (ye wahi src hai jo 'leaked zip' me FroxtDevil ne becha tha — hum direct maarenge)
# =====================================================================
_INSTA_GCF = (os.environ.get("INSTA_GCF_URL")
              or "https://europe-west3-storyviewer-7a64d.cloudfunctions.net/getInstagramData").strip()


def _native_insta(term: str) -> dict:
    """v2.24.7 — Instagram profile info: username/ID se full profile (followers, bio, pic...)."""
    u = str(term or "").strip().lstrip("@")
    if not re.match(r"^[A-Za-z0-9._]{1,30}$", u):
        return {"ok": False, "why": "validation", "msg": "Instagram username bhejo (e.g. therock)"}
    payload = json.dumps({"data": {"endpoint": "/v1/info",
                                   "params": {"include_about": True,
                                              "username_or_id_or_url": u}}}).encode()
    req = urllib.request.Request(_INSTA_GCF, data=payload, method="POST",
                                 headers={"User-Agent": "okhttp/4.10.0",
                                          "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=18) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": False, "why": "upstream", "msg": f"Insta-source busy/down ({str(e)[:40]})"}
    d = ((data.get("result") or {}).get("data") or {}) if isinstance(data, dict) else {}
    if not d:
        return {"ok": False, "why": "notfound", "msg": "Account private/deleted ya data nahi mila"}
    about = d.get("about") or {}
    merged = {**about, **{k: v for k, v in d.items() if k != "about"}}
    username = merged.get("username") or u
    pic = merged.get("profile_pic_url") or merged.get("hd_profile_pic_url") or ""
    return {"ok": True, "source": "insta-native", "data": {
        "username": username,
        "full_name": merged.get("full_name") or "",
        "bio": merged.get("biography") or "",
        "category": merged.get("category_name") or merged.get("account_category") or "",
        "country": about.get("country") or "",
        "joined": about.get("date_joined") or "",
        "followers": merged.get("follower_count") or 0,
        "following": merged.get("following_count") or 0,
        "posts": merged.get("media_count") or 0,
        "private": bool(merged.get("is_private")),
        "verified": bool(merged.get("is_verified")),
        "business": bool(merged.get("is_business")),
        "profile_pic": pic,
        "external_url": merged.get("external_url") or ""}}


def _lx(tool_path: str, param: str, value: str, timeout: float = 30) -> dict:
    """LakshitX call → normalized {ok,source,data} ya {ok:False,why}. None-return nahi.
    Key-chain: LAKSHITX_KEY → _KEY_1D → _KEY_D3 (sirf invalid-key/expired pe aage badhta hai — value-error pe nahi)."""
    if not LX_KEYS or LX_KEYS == [""]:
        return {"ok": False, "why": "unconfigured", "msg": "LAKSHITX_KEY env missing"}
    j = None
    for ki, LX_KEY in enumerate(LX_KEYS):
        url = (f"{LX_BASE}/{tool_path}/{urllib.parse.quote(LX_KEY)}"
               f"?{urllib.parse.quote(param)}={urllib.parse.quote(str(value))}")
        code, body = http_get(url, timeout=timeout)
        if code != 200 or not body:
            # 401/403 = key-mara → agla key try karo (agar bacha hai)
            if code in (401, 403) and ki + 1 < len(LX_KEYS):
                continue
            return {"ok": False, "why": "upstream", "msg": f"LakshitX HTTP {code}"}
        try:
            j = json.loads(body)
        except Exception:
            return {"ok": False, "why": "upstream", "msg": "LakshitX bad-json"}
        if not isinstance(j, dict):
            return {"ok": False, "why": "upstream", "msg": "LakshitX odd-response"}
        msg_l = str(j.get("message") or "").lower()
        if "invalid key" in msg_l or "expired" in msg_l or "key blocked" in msg_l:
            if ki + 1 < len(LX_KEYS):
                continue
            return {"ok": False, "why": "key-dead", "msg": "LakshitX: sab keys dead/expired"}
        break  # valid-key + real response → isko normalize karo
    st, suc = j.get("status"), j.get("success")
    if st == "error":
        return {"ok": False, "why": "validation", "msg": j.get("message") or "LakshitX param issue"}
    if st == "failed" or suc is False or st is False:
        return {"ok": False, "why": "notfound", "msg": j.get("message") or "LakshitX: data nahi mila"}
    # ---- per-tool normalize (unke shapes tool-wise alag hain) ----
    if tool_path == "number" and isinstance(j.get("data"), dict):
        d = dict(j["data"]); d.pop("_id", None)
        return {"ok": True, "source": "lakshitx", "data": {
            "mobile": d.get("mobile"), "name": d.get("name"), "father": d.get("fname"),
            "address": str(d.get("address") or "").replace("!", ", "),
            "circle": d.get("circle"), "email": d.get("email")}}
    if tool_path == "aadhar" and isinstance(j.get("result"), list):
        recs = [{"name": r.get("NAME"), "father": r.get("fname"), "mobile": r.get("num"),
                 "alt": r.get("alt"), "circle": r.get("circle"), "email": r.get("email"),
                 "address": str(r.get("ADDRESS") or "").replace("!", ", ")}
                for r in j["result"][:10] if isinstance(r, dict)]
        return {"ok": True, "source": "lakshitx", "data": {"records": recs, "total": len(j["result"])}}
    if tool_path == "vehicle" and isinstance(j.get("response"), dict):
        d = dict(j["response"]); d["regNo"] = j.get("regNo")
        return {"ok": True, "source": "lakshitx", "data": d}
    if tool_path == "ifsc" and isinstance(j.get("data"), dict):
        return {"ok": True, "source": "lakshitx", "data": j["data"]}
    if tool_path == "pincode" and j.get("status") == "success":
        return {"ok": True, "source": "lakshitx",
                "data": {"pincode": j.get("pincode"), "total": j.get("total_records_found"),
                         "records": (j.get("records") or [])[:15]}}
    if tool_path == "imei" and isinstance(j.get("result"), dict):
        return {"ok": True, "source": "lakshitx", "data": j["result"]}
    if tool_path == "gst":
        return {"ok": True, "source": "lakshitx", "data": j.get("data") if j.get("data") else j}
    if tool_path == "upi":
        if suc is True or st is True:
            clean = {k: v for k, v in j.items() if k not in ("developer", "join_channel", "buy_apis")}
            return {"ok": True, "source": "lakshitx", "data": clean.get("data") or clean}
        return {"ok": False, "why": "notfound", "msg": j.get("message") or "UPI record nahi mila"}
    return {"ok": True, "source": "lakshitx", "data": j}


NATIVE_HANDLERS = {
    "weather": _native_weather,
    "dns": _native_dns,
    "ip": _native_ip,
    "gst": _chain(("native", _native_gst), ("lakshitx", lambda t: _lx("gst", "gst", t))),
    "pantogst": _native_pantogst,
    "ifsc": _native_ifsc,
    "pincode": _native_pincode,
    # ---- v2.15: comparison ke baad ka faisla (measurements comment me) ----
    # vehicle: humara source UP16FB2070 par notfound deta tha (2.5s), X_TRACE deta hai (13s) -> xtrace primary
    # v2.24.6: LAKSHITX first — full rtoData (chassis/engine/pucc) 2-4s me, xtrace ab last-resort
    "vehicle": _chain(("apna-db", _ldb_rc),
                      ("lakshitx", lambda t: _lx("vehicle", "vehicle", t)),
                      ("provider", _env_provider("vehicle")),
                      ("native", _native_vehicle),
                      ("vahanx", _vahanx_rc),
                      ("xtrace", lambda t: _xcall("vehicle_full", t))),
    "tginfo": _native_tginfo,
    "tgnum": _chain(("apna-db", _ldb_tg),
                    ("native", _native_tgnum)),
    # vehicletomobile: humara 6.7s par chalta hai, X_TRACE 10.6s + kuch gaadi 404 -> native primary
    "vehicletomobile": _chain(("native", _native_vehicletomobile),
                              ("provider", _env_provider("vehicletomobile")),
                              ("shuruu", _shuruu_v2m),
                              ("xtrace", lambda t: _xcall("vehicle_num", t))),
    # aadhaar2num: humara 0.97s (fast) vs X_TRACE 12.1s -> native primary
    # v2.19.2: xtrace is chain se hata — wo 12-15s hang karta tha, isliye
    # "record nahi mila" jaisa honest jawab bhi 15s me timeout ban jaata tha.
    # native (sarkari ?=addhaar2num, 0.4s) primary + provider slot future ke liye.
    "aadhaar2num": _chain(("native", _native_aadhaar2num),
                          ("provider", _env_provider("aadhaar2num")),
                          ("shuruu", _shuruu_aadhaar2num)),
    # num: humara 1.8s vs X_TRACE 10.4s -> native primary (xtrace sirf fallback)
    # v2.24.6: LAKSHITX first — name/father/address/circle/email 1-3s me (sarkari-509 ke baad
    # ye hi sabse tez rich source hai; 14s-wali aph-chain ab fallback)
    "num": _chain(("apna-db", _ldb_num),
                  ("lakshitx", lambda t: _lx("number", "number", t)),
                  ("native", _native_num),
                  ("provider", _env_provider("num")),
                  ("xtrace", lambda t: _xcall("num", t)),
                  ("shuruu", _shuruu_num)),
    # familyinfo — v2.19.2: ?=family (0.3s, members[]) PRIMARY. Pehle aadhaar2num aage tha
    # -> upstream slow hone par hang (user ka asli bug) aur members ke bajaye mobile
    # records bhi ja sakta tha. Ab family pehle, baaki sirf fallback.
    "familyinfo": _chain(("family", _native_familyinfo),
                         ("provider", _env_provider("familyinfo")),
                         ("aadhaar2num", _native_aadhaar2num),
                         ("shuruu", _shuruu_familyinfo),
                         ("xtrace", lambda t: _xcall("aadhar", t))),
    "phonebiz": _native_phonebiz,
    # v2.12 — owner ke apne free-forever tools
    # v2.24.6: imei — LakshitX rich (brand/model/specs) first, builtin-luhn fallback
    "imei": _chain(("lakshitx", lambda t: _lx("imei", "imei", t)), ("builtin", _native_imei)),
    # v2.24.6: aadhaar-rich — LakshitX multi-records (name/father/mobile/address) first
    "aadhaar": _chain(("apna-db", _ldb_aadhaar),
                      ("lakshitx", lambda t: _lx("aadhar", "aadhar", t)),
                      ("aadhaar2num", _native_aadhaar2num),
                      ("provider", _env_provider("aadhaar")),
                      ("xtrace", lambda t: _xcall("aadhar", t))),
    # v2.24.6: ifsc/pincode — native instant primary, LakshitX rich-fallback
    "ifsc": _chain(("native", _native_ifsc), ("lakshitx", lambda t: _lx("ifsc", "ifsc", t))),
    "pincode": _chain(("native", _native_pincode), ("lakshitx", lambda t: _lx("pincode", "pincode", t))),
    # v2.24.6: upi — NAYA TOOL (LakshitX upi-verify)
    "upi": _chain(("lakshitx", lambda t: _lx("upi", "upi", t))),
    # v2.24.7: insta — NAYA TOOL (native GCF, koi middleman nahi)
    "insta": _native_insta,
    "aadhaar2pan": _native_aadhaar2pan,
    "fastag": _native_fastag,
    "plate": _nf_plate,
    "aadhaarcheck": _nf_aadhaarcheck,
    "gstcheck": _nf_gstcheck,
    "webextract": _nf_webextract,
    # v2.15 — X_TRACE only tools
    # v2.15.1 — har xtrace-only tool ke peeche ENV PROVIDER slot
    # (kal X_TRACE expire ho -> TC_API_URL env daalo, code nahi chhuna)
    "tc": _chain(("provider", _env_provider("tc")), ("xtrace", lambda t: _xcall("tc", t))),
    # aadhaar: v2.24.6 — LakshitX-chain upar shift (yahan duplicate hata)
}

# =====================================================================
#  v2.14 SPEED — cache: single-flight + stale-while-revalidate + stats
#   • same term 2 users ne maanga -> upstream sirf 1 baar jaata hai
#   • TTL beet gaya -> purana jawab TURANT milta hai, refresh background me
#   • static data (ifsc/pincode/plate/checks) 6 ghante tak cache
# =====================================================================
_CACHE: dict = {}
_LOCK = threading.Lock()
_CACHE_MAX = int(os.environ.get("CACHE_MAX", "8000"))
_TTL_OK = int(os.environ.get("CACHE_TTL_OK", "3600"))
_TTL_STATIC = int(os.environ.get("CACHE_TTL_STATIC", "21600"))
_TTL_NO = int(os.environ.get("CACHE_TTL_FAIL", "300"))
# v2.25.0 — flappy wrapper tools (pantogst): fail-cache sirf 45s
_TTL_NO_FAST = 45
_FAST_RETRY_TOOLS = ("pantogst",)
_SWR_MAX = int(os.environ.get("CACHE_STALE_MAX", "86400"))
_STATIC_TOOLS = {"ifsc", "pincode", "plate", "gstcheck", "aadhaarcheck", "gst", "pantogst"}
_INFLIGHT: dict = {}
_INFLIGHT_LOCK = threading.Lock()
STATS: dict = {}
STATS_LOCK = threading.Lock()
_SEM = threading.Semaphore(int(os.environ.get("MAX_CONCURRENT", "32")))

# jin tools ka source owner ke control me nahi / mar chuka hai
DEGRADED = {
    # pantogst v2.23.1: provider(env GST_API_KEY) + sarkari pan2gst fallback — do sources

}

# v2.14 — har tool ka max time. Source slow ho to user ko 46s nahi, 10-12s me
# honest jawab milega ("source slow"). Per-tool override: BUDGET_NUM=8000 etc.
TOOL_BUDGET_MS = int(os.environ.get("TOOL_BUDGET_MS", "12000"))
_BUDGET = {
    # X_TRACE tools: unka backend hi 10-15s leta hai. Budget kam rakhne se hum
    # unka credit jala kar jawab chhod dete the -> inhe poora 22s do.
    "tc": int(os.environ.get("BUDGET_TC", "22000")),
    "aadhaar": int(os.environ.get("BUDGET_AADHAAR", "22000")),
    "vehicle": int(os.environ.get("BUDGET_VEHICLE", "22000")),
    "num": int(os.environ.get("BUDGET_NUM", "15000")),
    "vehicletomobile": int(os.environ.get("BUDGET_VEHICLE_TO_MOBILE", "15000")),
    "tgnum": int(os.environ.get("BUDGET_TGNUM", "8000")),
    "familyinfo": int(os.environ.get("BUDGET_FAMILYINFO", "22000")),
    "aadhaar2num": int(os.environ.get("BUDGET_AADHAAR2NUM", "15000")),
    "fastag": int(os.environ.get("BUDGET_FASTAG", "30000")),
}
_AUTO_DEGRADE_AFTER = int(os.environ.get("AUTO_DEGRADE_AFTER", "4"))


def _stat(tool: str, ms: int, ok: bool, why: str = ""):
    with STATS_LOCK:
        e = STATS.setdefault(tool, {"n": 0, "ok": 0, "fail": 0, "ms_sum": 0, "last_ms": 0,
                                    "last_at": 0, "consec_fail": 0, "last_why": ""})
        e["n"] += 1
        e["ok" if ok else "fail"] += 1
        e["ms_sum"] += ms
        e["last_ms"] = ms
        e["last_at"] = int(time.time())
        e["last_why"] = "" if ok else (why or "fail")
        if ok:
            e["consec_fail"] = 0
        else:
            e["consec_fail"] = e.get("consec_fail", 0) + 1
            if e["consec_fail"] >= _AUTO_DEGRADE_AFTER and tool not in DEGRADED:
                DEGRADED[tool] = f"auto: lagataar {e['consec_fail']} baar source fail ({e['last_why']})"


def stats_snapshot() -> dict:
    with STATS_LOCK:
        out = {}
        for t, e in STATS.items():
            out[t] = {
                "status": "degraded" if t in DEGRADED else "live",
                "consec_fail": e.get("consec_fail", 0),
                "last_why": e.get("last_why", ""),
                "hits": e["n"],
                "ok": e["ok"],
                "fail": e["fail"],
                "avg_ms": int(e["ms_sum"] / e["n"]) if e["n"] else 0,
                "last_ms": e["last_ms"],
                "success_rate": round(100.0 * e["ok"] / e["n"], 1) if e["n"] else None,
            }
        return out


def env_providers() -> dict:
    """kaun kaun se custom provider slots bhare hue hain (env se)."""
    out = {}
    for t in ("num", "tc", "aadhaar", "aadhaar2num", "vehicle", "vehicletomobile", "familyinfo", "pantogst"):
        u = (os.environ.get(f"{t.upper()}_API_URL") or "").strip()
        if u:
            out[t] = {"configured": True, "url_host": urllib.parse.urlsplit(u).hostname or "?"}
    return out


def permanent_tools() -> dict:
    """kaun se tools 100% permanent hain (kisi third-party key ke bina)."""
    perm = ["ip", "ifsc", "pincode", "gst", "plate", "aadhaarcheck", "gstcheck",
            "webextract", "phonebiz", "tgnum", "num", "aadhaar2num"]
    return {"permanent": perm, "count": len(perm),
            "provider_dependent": [t for t in TOOLS if t not in perm]}


# =====================================================================
#  v2.16 — APNA DATABASE (owner's own growing store)
#  Jo bhi query ek baar chali, uska result disk par jama hota jaata hai.
#  Iska matlab: jaise-jaise aapke users use karenge, aapka DB bada hota
#  jayega aur repeat lookups upstream par jaayenge hi nahi -> provider par
#  dependency kam hoti jaayegi. (Kisi ki poori DB copy karna nahi — sirf
#  apne users ki apni queries ka apna result store.)
# =====================================================================
_DB_FILE = os.path.join(os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
                        "mydb.json")
_DB_SAVE_EVERY = int(os.environ.get("DB_SAVE_SECONDS", "600"))


def db_stats() -> dict:
    with _LOCK:
        n = len(_CACHE)
        per_tool: dict = {}
        for (tool, _term) in _CACHE.keys():
            per_tool[tool] = per_tool.get(tool, 0) + 1
    try:
        size = os.path.getsize(_DB_FILE)
    except OSError:
        size = 0
    return {"records": n, "per_tool": per_tool, "disk_kb": round(size / 1024, 1),
            "file": "data/mydb.json", "persistent": size > 0}


def _save_db():
    try:
        os.makedirs(os.path.dirname(_DB_FILE), exist_ok=True)
        with _LOCK:
            items = {}
            for (tool, term), (ts, res, ttl) in list(_CACHE.items()):
                if res.get("ok"):
                    items[f"{tool}|{term}"] = [ts, res, ttl]
        tmp = _DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, _DB_FILE)
        return len(items)
    except Exception:
        return 0


def load_db():
    """Boot par apna jama kiya hua data wapas laao."""
    try:
        with open(_DB_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        n = 0
        with _LOCK:
            for k, v in raw.items():
                if "|" not in k or not isinstance(v, list) or len(v) != 3:
                    continue
                tool, term = k.split("|", 1)
                _CACHE[(tool, term)] = (float(v[0]), v[1], int(v[2]))
                n += 1
        if n:
            print(f"[db] apna database restored: {n} records", flush=True)
        return n
    except Exception:
        return 0


def start_db_maintenance():
    load_db()

    def _loop():
        while True:
            time.sleep(_DB_SAVE_EVERY)
            _save_db()
    threading.Thread(target=_loop, daemon=True).start()


def cache_info() -> dict:
    with _LOCK:
        n = len(_CACHE)
    return {"entries": n, "max": _CACHE_MAX, "ttl_ok": _TTL_OK, "ttl_static": _TTL_STATIC,
            "ttl_fail": _TTL_NO, "stale_max": _SWR_MAX}
_RATE: dict = {}
_RATE_LOCK = threading.Lock()

def _rate_ok(bucket: str, limit: int) -> bool:
    win = int(time.time() // 60)
    with _RATE_LOCK:
        if len(_RATE) > 128:
            _RATE.clear()
        c = _RATE.get((bucket, win), 0)
        if c >= limit:
            return False
        _RATE[(bucket, win)] = c + 1
        return True

def _cached(tool: str, term: str):
    with _LOCK:
        hit = _CACHE.get((tool, term))
        if hit and time.time() - hit[0] < hit[2]:
            out = dict(hit[1])
            out["cached"] = True
            out["cache_age_s"] = int(time.time() - hit[0])
            return out
    return None


def _cache_row(tool: str, term: str):
    with _LOCK:
        return _CACHE.get((tool, term))


def _store(tool: str, term: str, res: dict):
    # v2.18.2: timeout/upstream jaise TRANSPORT fail cache NAHI — warna source
    # wapas aane ke baad bhi 5 min tak instant-FAIL milta raheta tha.
    if not res.get("ok") and (res.get("why") or "").lower() in ("timeout", "upstream", "error"):
        return
    ttl = _TTL_STATIC if (res.get("ok") and tool in _STATIC_TOOLS) else (_TTL_OK if res.get("ok") else (_TTL_NO_FAST if tool in _FAST_RETRY_TOOLS else _TTL_NO))
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:            # sabse purani entry hatao
            try:
                oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
                _CACHE.pop(oldest, None)
            except Exception:
                _CACHE.clear()
        _CACHE[(tool, term)] = (time.time(), res, ttl)


def _refresh_async(tool: str, term: str):
    """Stale jawab dene ke baad background me taaza karo."""
    def _work():
        try:
            _compute(tool, term)
        except Exception:
            pass
    t = threading.Thread(target=_work, daemon=True)
    t.start()


def _compute(tool: str, term: str) -> dict:
    """Asli upstream call (cache chhod kar) — HARD TIME BUDGET ke saath."""
    t0 = time.time()
    budget = _BUDGET.get(tool, TOOL_BUDGET_MS) / 1000.0
    box = {}

    def _run():
        try:
            with _SEM:
                box["res"] = NATIVE_HANDLERS[tool](term)
        except Exception as e:
            box["err"] = e
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(budget)
    if th.is_alive():
        res = {"ok": False, "why": "timeout",
               "msg": f"source ne {int(budget)}s me jawab nahi diya — thodi der baad try karo"}
    elif "err" in box:
        res = {"ok": False, "why": "error", "msg": "andar hi andar error aaya"}
    else:
        res = box.get("res") or {"ok": False, "why": "error", "msg": "khali jawab"}
    res = dict(res)
    res = sanitize(res)                     # provider ke naam/credits hatao, "api": HIMANSHU
    res.setdefault("source", "native")     # handler ka source (native/provider) chhino mat
    res = dict(res)
    res.setdefault("ok", False)
    res["tool"] = tool
    res["term"] = term
    res["query"] = term
    res["success"] = bool(res.get("ok"))
    res["ms"] = int((time.time() - t0) * 1000)
    if res.get("ok") and isinstance(res.get("data"), dict):
        res["fields"] = [[k, v] for k, v in list(res["data"].items())[:20] if v not in (None, "", [], {})]
    _store(tool, term, res)
    _stat(tool, res["ms"], bool(res.get("ok")), res.get("why") or "")
    return res

def lookup(tool: str, term: str) -> dict:
    t0 = time.time()
    tool = ALIASES.get(tool, tool)
    if tool not in TOOLS:
        return {"ok": False, "why": "unknown_tool", "ms": 0, "msg": f"'{tool}' naam ka koi tool nahi"}
    clean = validate(tool, term)
    if not clean:
        return {"ok": False, "why": "validation", "ms": 0, "msg": f"{tool} ke liye ye value theek nahi hai"}

    # 1) fresh cache
    hit = _cached(tool, clean)
    if hit:
        hit["ms"] = int((time.time() - t0) * 1000)
        return hit

    # 2) stale cache -> turant jawab + background refresh (v2.14 SWR)
    row = _cache_row(tool, clean)
    if row and (time.time() - row[0]) < _SWR_MAX:
        out = dict(row[1])
        out["cached"] = True
        out["stale"] = True
        out["cache_age_s"] = int(time.time() - row[0])
        out["ms"] = int((time.time() - t0) * 1000)
        _refresh_async(tool, clean)
        return out

    lim = int(os.environ.get("NATIVE_RPM", "0"))     # 0 = koi global cap nahi (per-key limit server me hai)
    if lim > 0 and not _rate_ok("native", lim):
        return {"ok": False, "why": "rate_limit", "ms": 0, "msg": f"bahut saare requests — ek minute ruk kar try karo (limit {lim}/min)"}

    # 3) single-flight: ek hi term ke liye ek hi upstream call
    key = (tool, clean)
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(key)
        leader = ev is None
        if leader:
            ev = threading.Event()
            _INFLIGHT[key] = ev
    if not leader:
        ev.wait(timeout=45)
        got = _cached(tool, clean)
        if got:
            got["ms"] = int((time.time() - t0) * 1000)
            got["shared"] = True
            return got
    try:
        res = _compute(tool, clean)
        res["ms"] = int((time.time() - t0) * 1000)
        return res
    finally:
        if leader:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(key, None)
            ev.set()
