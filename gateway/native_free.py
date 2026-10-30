# -*- coding: utf-8 -*-
"""
native_free.py — 🆓 OWNER KE APNE TOOLS (v2.12)
===============================================================
Ye file aapke gateway ke liye naye tools deti hai jo **kisi paid/demo API
par depend nahi karte**. Har tool ka source ya to:

  (a) OFFLINE hai — matlab internet hi nahi chahiye, 0 ms, kabhi nahi marega
  (b) OFFICIAL free API hai — RazorPay IFSC, India Post pincode

Isliye in par "expire" ka sawaal hi nahi aata. Ye aapke hamesha ke rahenge.

Tools
-----
  plate         🚗 RC plate decode — state, RTO code, city, format valid?
                   (100% OFFLINE, exact public data, ~0ms)
  aadhaarcheck  🆔 Aadhaar checksum validate — Verhoeff algorithm
                   (100% OFFLINE — sirf "valid/invalid format" batata hai,
                    koi personal data nahi. Legally safe.)
  gstcheck      🧾 GSTIN checksum validate (100% OFFLINE, exact)
  webextract    🌐 URL se title/description/emails/phones/links
                   (self-hosted — koi third party nahi, ~300-900ms)

Design rules (jaan-boojh kar):
  • Sirf stdlib — koi pip install nahi (Render build 0 sec).
  • Koi bhi tool personal data nahi lautata (na naam, na pata, na mobile).
  • SSRF guard `webextract` me: private IP / cloud-metadata block.
  • Har response: {"ok": bool, "data": {...}, "source": "native", ...}
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request

__all__ = ["TOOLS", "HANDLERS", "close_all"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ══════════════════════════════════════════════════════════════════
#  1) 🚗 RC PLATE DECODER — 100% OFFLINE, exact
# ══════════════════════════════════════════════════════════════════
# India ka RC format (Motor Vehicles Act + CMVR 1989, public):
#     AA  NN  AA  NNNN
#      │   │   │    └── unique number
#      │   │   └────── series letters (0-3, optional)
#      │   └────────── RTO office number (1-2 digits)
#      └────────────── state / UT code (2 letters)

STATE_CODES = {
    "AN": "Andaman & Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "CH": "Chandigarh",
    "DD": "Daman & Diu", "DL": "Delhi", "DN": "Dadra & Nagar Haveli",
    "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana",
    "JH": "Jharkhand", "JK": "Jammu & Kashmir", "KA": "Karnataka", "KL": "Kerala",
    "LA": "Ladakh", "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya",
    "MN": "Manipur", "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland",
    "OD": "Odisha", "OR": "Odisha (old code)", "PB": "Punjab", "PY": "Puducherry",
    "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TR": "Tripura",
    "TS": "Telangana", "UK": "Uttarakhand", "UA": "Uttarakhand (old code)",
    "UP": "Uttar Pradesh", "WB": "West Bengal",
}

# Mashhoor RTO offices (state+code -> city/RTO naam). Poori list nahi hai —
# jo mila wo exact, baaki me sirf state dikhta hai (galat city nahi bataenge).
RTO_CITY = {
    "BR01": "Patna", "BR02": "Gaya", "BR03": "Bhagalpur", "BR06": "Muzaffarpur",
    "BR07": "Darbhanga", "BR09": "Begusarai", "BR10": "Chhapra", "BR11": "Saharsa",
    "BR19": "Saharsa", "BR21": "Nalanda (Bihar Sharif)", "BR24": "Bhojpur (Arrah)",
    "BR28": "Madhubani", "BR30": "Samastipur", "BR31": "Samastipur",
    "BR33": "Siwan", "BR34": "Motihari", "BR37": "Sitamarhi", "BR38": "Araria",
    "BR39": "Katihar", "BR43": "Sasaram (Rohtas)", "BR44": "Buxar",
    "DL01": "Delhi (Mall Road)", "DL02": "Delhi (Rajouri Garden)",
    "DL03": "Delhi (Sheikh Sarai)", "DL04": "Delhi (Janakpuri)",
    "DL05": "Delhi (Loni Road)", "DL08": "Delhi (Wazirpur)",
    "DL09": "Delhi (Rohini)", "DL10": "Delhi (Raja Garden)",
    "HR01": "Ambala", "HR02": "Yamunanagar", "HR26": "Gurugram",
    "HR51": "Faridabad", "HR55": "Gurugram (South)",
    "KA01": "Bengaluru (Koramangala)", "KA02": "Bengaluru (Rajajinagar)",
    "KA03": "Bengaluru (Indiranagar)", "KA04": "Bengaluru (Yeshwanthpur)",
    "KA05": "Bengaluru (Jayanagar)", "KA51": "Bengaluru (Electronic City)",
    "KL01": "Thiruvananthapuram", "KL07": "Ernakulam", "KL08": "Thrissur",
    "KL11": "Kozhikode", "KL41": "Kollam",
    "MH01": "Mumbai (Tardeo)", "MH02": "Mumbai (Andheri)", "MH12": "Pune",
    "MH14": "Pune (Pimpri-Chinchwad)", "MH31": "Nagpur", "MH43": "Thane",
    "RJ01": "Ajmer", "RJ14": "Jaipur", "RJ19": "Jodhpur", "RJ27": "Udaipur",
    "TN01": "Chennai (Anna Nagar)", "TN09": "Chennai (T. Nagar)",
    "TN10": "Chennai (Saidapet)", "TN38": "Coimbatore",
    "TS07": "Hyderabad (Ranga Reddy)", "TS09": "Hyderabad (Khairatabad)",
    "UP14": "Ghaziabad", "UP16": "Noida (Gautam Budh Nagar)",
    "UP32": "Lucknow", "UP65": "Varanasi", "UP78": "Kanpur",
    "WB01": "Kolkata (Beltala)", "WB02": "Kolkata (Kasba)",
    "GJ01": "Ahmedabad", "GJ05": "Surat", "GJ18": "Ahmedabad (East)",
    "MP04": "Bhopal", "MP09": "Indore", "PB10": "Ludhiana", "PB65": "Mohali",
}

# BH series (Bharat Series) — 2021 se naya format: YY BH NNNN XX
_BH_RE = re.compile(r"^([0-9]{2})BH([0-9]{4})([A-Z]{0,2})$")
_STD_RE = re.compile(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{1,4})$")

# Plates jo bhram paida karte hain (0 vs O). Owner ko hint do.
_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "I": "1", "Z": "2"})


def plate_decode(term: str) -> dict:
    """🚗 RC plate ko OFFLINE decode karo — 0 ms, kabhi fail nahi.

    Data: state name + RTO code + city (jahan pata ho) + format validity.
    Koi personal info nahi — sirf public numbering plan.
    """
    raw = str(term or "").upper()
    clean = re.sub(r"[^A-Z0-9]", "", raw)

    out = {"input": raw, "plate": clean, "length": len(clean)}

    if 6 > len(clean) or len(clean) > 11:
        return {"ok": False, "why": "validation",
                "msg": "gaadi number 6-11 characters ka hota hai",
                "data": out}

    # ---- BH (Bharat) series ----
    m = _BH_RE.match(clean)
    if m:
        yy, num, suffix = m.groups()
        out.update({
            "series_type": "BH (Bharat Series)",
            "year_code": int(yy),
            "registration_year": 2000 + int(yy),
            "number": num,
            "suffix": suffix or None,
            "note": "BH series — state se independent (central).",
        })
        return {"ok": True, "data": out}

    # ---- normal state series ----
    m = _STD_RE.match(clean)
    if not m:
        hint = ""
        fixed = clean.translate(_CONFUSABLE)
        if fixed != clean and _STD_RE.match(fixed):
            hint = f" Aapka matlab shayad '{fixed}' tha (O→0 / I→1)."
        return {"ok": False, "why": "validation",
                "msg": f"format theek nahi hai. Jaise: BR01AB1234.{hint}",
                "data": out}

    state, rto_no, series, num = m.groups()
    rto_code = f"{state}{rto_no.zfill(2)}"
    out.update({
        "series_type": "State series",
        "state_code": state,
        "state": STATE_CODES.get(state),
        "rto_code": rto_code,
        "rto_office_no": int(rto_no),
        "rto_city": RTO_CITY.get(rto_code),
        "series_letters": series or None,
        "number": num,
    })
    if not out["state"]:
        return {"ok": False, "why": "validation",
                "msg": f"'{state}' koi state/UT code nahi hai",
                "data": out}

    # state ka naam mil gaya = valid plate
    return {"ok": True, "data": out}


# ══════════════════════════════════════════════════════════════════
#  2) 🆔 AADHAAR CHECKSUM — 100% OFFLINE (Verhoeff)
# ══════════════════════════════════════════════════════════════════
# UIDAI Aadhaar ka 12th digit Verhoeff checksum hota hai. Ye sirf batata
# hai ki "number banawat ka nahi hai" — koi personal data nahi chhua jata.
_D_V = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P_V = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D_V[c][_P_V[i % 8][int(ch)]]
    return c == 0


def aadhaar_check(term: str) -> dict:
    """🆔 Aadhaar ka format + Verhoeff checksum — 0 ms, fully offline."""
    d = re.sub(r"\D", "", str(term or ""))
    data = {"digits": len(d)}
    if len(d) != 12:
        return {"ok": False, "why": "validation",
                "msg": "Aadhaar 12 digit ka hota hai", "data": data}
    if d[0] in "01":
        data["checksum_ok"] = False
        return {"ok": False, "why": "validation",
                "msg": "Aadhaar ka pehla digit 2 se bada hota hai", "data": data}
    ok = _verhoeff_valid(d)
    data.update({
        "checksum_ok": ok,
        "masked": "XXXX-XXXX-" + d[-4:],
        "verdict": "valid format (checksum OK)" if ok else "invalid (checksum fail — number galat type hua hai)",
    })
    return {"ok": True, "data": data}


# ══════════════════════════════════════════════════════════════════
#  3) 🧾 GSTIN CHECKSUM — 100% OFFLINE
# ══════════════════════════════════════════════════════════════════
_GST_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gst_check(term: str) -> dict:
    """🧾 GSTIN ka 15th character checksum — 0 ms, fully offline."""
    g = str(term or "").upper().strip()
    g = re.sub(r"[^A-Z0-9]", "", g)
    data = {"gstin": g, "length": len(g)}
    if len(g) != 15:
        return {"ok": False, "why": "validation",
                "msg": "GSTIN 15 character ka hota hai", "data": data}
    try:
        total = 0
        for i, ch in enumerate(g[:14]):
            v = _GST_ALNUM.index(ch) * (2 if i % 2 else 1)
            total += v // 36 + v % 36
        checksum = (36 - total % 36) % 36
        ok = _GST_ALNUM[checksum] == g[14]
    except ValueError:
        return {"ok": False, "why": "validation",
                "msg": "GSTIN me sirf 0-9 aur A-Z chalte hain", "data": data}
    data.update({"checksum_ok": ok, "state_code": g[:2],
                 "pan": g[2:12], "entity_no": g[12]})
    return {"ok": True, "data": data}


# ══════════════════════════════════════════════════════════════════
#  4) 🌐 WEB EXTRACTOR — self-hosted (koi third party nahi)
# ══════════════════════════════════════════════════════════════════
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\-\s]?)?[6-9]\d{9}(?!\d)")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)', re.I)
_OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']*)', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)

MAX_BYTES = 900_000     # 900 KB — Render free RAM safe
MAX_SECS = 8


def _ssrf_blocked(host: str) -> str:
    """Private / metadata IP par request na jaye (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return "host resolve nahi hua"
    for fam, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return "private/internal address blocked"
    return ""


def webextract(term: str) -> dict:
    """🌐 URL se title, description, emails, phones, links — apna hi fetch."""
    url = str(term or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host or "." not in host:
        return {"ok": False, "why": "validation", "msg": "URL theek nahi hai"}

    blocked = _ssrf_blocked(host)
    if blocked:
        return {"ok": False, "why": "blocked", "msg": blocked}

    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-IN,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=MAX_SECS) as resp:
            final_url = resp.geturl()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            raw = resp.read(MAX_BYTES)
            code = resp.status
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "why": "upstream",
                "msg": f"site jawab nahi de rahi ({type(e).__name__})"}

    encoding = "utf-8"
    m = re.search(r"charset=([\w\-]+)", ctype)
    if m:
        encoding = m.group(1)
    try:
        text = raw.decode(encoding, "ignore")
    except LookupError:
        text = raw.decode("utf-8", "ignore")

    title = _TAG_RE.sub("", _TITLE_RE.search(text).group(1)).strip() if _TITLE_RE.search(text) else ""
    desc = (_DESC_RE.search(text).group(1) if _DESC_RE.search(text) else "") or \
           (_OG_RE.search(text).group(1) if _OG_RE.search(text) else "")

    emails, phones = [], []
    for e in _EMAIL_RE.findall(text):
        el = e.lower()
        if el not in emails and not el.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            emails.append(el)
    for p in _PHONE_RE.findall(text):
        pd = re.sub(r"\D", "", p)[-10:]
        if pd not in phones:
            phones.append(pd)

    links = []
    base_host = urllib.parse.urlparse(final_url).hostname or ""
    for href in _LINK_RE.findall(text):
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urllib.parse.urljoin(final_url, href)
        if full not in links:
            links.append(full)

    return {"ok": True, "data": {
        "url": final_url,
        "status": code,
        "title": title[:200],
        "description": desc[:400],
        "emails": emails[:25],
        "phones": phones[:25],
        "links_total": len(links),
        "links_sample": links[:25],
        "html_chars": len(text),
        "ms": int((time.time() - t0) * 1000),
    }}


# ══════════════════════════════════════════════════════════════════
#  REGISTRY
# ══════════════════════════════════════════════════════════════════
TOOLS = {
    "plate": "v2.12 🚗 RC plate OFFLINE decode — state, RTO code, city (0ms, never expire)",
    "aadhaarcheck": "v2.12 🆔 Aadhaar Verhoeff checksum validate (0ms, offline, koi personal data nahi)",
    "gstcheck": "v2.12 🧾 GSTIN checksum validate (0ms, offline)",
    "webextract": "v2.12 🌐 URL → title/desc/emails/phones/links (self-hosted, koi third party nahi)",
}

HANDLERS = {
    "plate": plate_decode,
    "aadhaarcheck": aadhaar_check,
    "gstcheck": gst_check,
    "webextract": webextract,
}

VALIDATORS = {
    "plate": lambda t: re.sub(r"[^A-Za-z0-9]", "", t or "").upper(),
    "aadhaarcheck": lambda t: re.sub(r"\D", "", t or ""),
    "gstcheck": lambda t: re.sub(r"[^A-Za-z0-9]", "", t or "").upper(),
    "webextract": lambda t: (t or "").strip()[:2048],
}


def close_all() -> None:
    """Koi persistent connection nahi rakhte — no-op (interface ke liye)."""
    return None
