"""
website_extractor.py — URL se emails / phones / links / meta nikalna
====================================================================
v2.18 — deep audit ke baad poora rewrite. Pehle kya galat tha:

  1. SSRF HOLE (serious): `requests.get(url)` seedha user ke URL par jaata tha.
     Koi bhi `http://169.254.169.254/latest/meta-data/` (cloud metadata) ya
     `http://localhost:10000/` bhej sakta tha → Render ka internal data leak.
  2. Size cap download ke BAAD lagta tha (3MB) — 500MB file RAM me aa jaati.
  3. `r.text` binary/PDF par bhi chalta tha → kachra parse, CPU waste.
  4. BeautifulSoup DO baar parse hota tha (2x CPU).
  5. Redirect follow hone par naya host safe hai ya nahi — check nahi tha.
  6. Timeout 15s hard-coded, connection pooling nahi.

Ab:
  • is_safe_url() — scheme / private IP / metadata host / DNS-rebind guard.
  • Streaming download + 2MB hard cap (server mana kare to bhi).
  • content-type gate — sirf text/html, text/plain, application/xhtml.
  • Ek hi parse (soup ek baar banao, sab usse).
  • Har redirect hop dobara verify.
  • Timeout env se (WEB_TIMEOUT, default 12s).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import fasthttp as requests
from bs4 import BeautifulSoup

from modules.core.net import is_safe_url

from .fast_cache import with_cache

MAX_BYTES = int(os.environ.get("WEB_MAX_KB", "2048")) * 1024      # 2 MB
TIMEOUT = float(os.environ.get("WEB_TIMEOUT", "12"))
MAX_REDIRECTS = 4
TEXT_TYPES = ("text/html", "application/xhtml", "text/plain", "text/xml", "application/xml")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# v2.24.2 FIX: FastSession (pool+retry+timeout built-in) — pura requests.Session()
# fasthttp-shim me nahi hota → boot-time AttributeError → PTB loop kabhi start nahi
# hota tha (sab updates DROP ho rahe the!). Ab poora bot zinda.
_session = requests.FastSession(default_timeout=25.0)

# junk jo email/phone regex me aa jaata hai
_BAD_MAIL_DOMAIN = ("example.com", "example.org", "sentry.io", "wixpress.com",
                    "domain.com", "email.com", "yourdomain.com")
_BAD_MAIL_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")
_BAD_MAIL_LOCAL = ("noreply", "no-reply", "donotreply", "test@", "user@", "name@", "your@")


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    return url


def extract_emails(text: str) -> List[str]:
    found = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}", text or "")
    out, seen = [], set()
    for e in found:
        e = e.strip(".,;:()[]<>\"'").lower()
        if not e or e in seen or len(e) > 120:
            continue
        local, _, domain = e.partition("@")
        if not local or not domain:
            continue
        if domain.endswith(_BAD_MAIL_EXT) or domain in _BAD_MAIL_DOMAIN:
            continue
        if any(local.startswith(p) for p in _BAD_MAIL_LOCAL):
            continue
        seen.add(e)
        out.append(e)
        if len(out) >= 50:
            break
    return out


def extract_phones(text: str) -> List[str]:
    raw = re.findall(r"(?:\+91[\s\-]?\d{10}\b|\b[6-9]\d{9}\b|\+\d{1,3}[\s\-.]?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4})", text or "")
    out, seen = [], set()
    for p in raw:
        digits = re.sub(r"\D", "", p)
        if len(digits) < 10 or len(digits) > 15:
            continue
        if digits in seen:
            continue
        seen.add(digits)
        out.append(re.sub(r"\s+", " ", p).strip())
        if len(out) >= 50:
            break
    return out


def _fetch(url: str):
    """Safe streaming fetch. -> (html, final_url, error)"""
    cur = url
    for _hop in range(MAX_REDIRECTS + 1):
        ok, why = is_safe_url(cur)                 # har hop par SSRF check
        if not ok:
            return "", cur, why
        try:
            r = _session.get(cur, headers=HEADERS, timeout=(4, TIMEOUT),
                             stream=True, allow_redirects=False)
        except requests.RequestException as e:
            return "", cur, f"site se connect nahi hua ({type(e).__name__})"

        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location") or ""
            r.close()
            if not loc:
                return "", cur, "site ne galat redirect bheja"
            cur = urljoin(cur, loc)
            continue
        if r.status_code != 200:
            r.close()
            return "", cur, f"HTTP {r.status_code} — site khuli nahi"

        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and not any(t in ctype for t in TEXT_TYPES):
            r.close()
            return "", cur, f"ye page website nahi hai ({ctype.split(';')[0]})"

        chunks, size, truncated = [], 0, False
        try:
            for chunk in r.iter_content(16384):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    truncated = True
                    break
        except requests.RequestException:
            pass
        finally:
            r.close()
        enc = r.encoding or "utf-8"
        body = b"".join(chunks)
        try:
            html = body.decode(enc, "replace")
        except LookupError:
            html = body.decode("utf-8", "replace")
        if truncated:
            html += "\n<!-- truncated by extractor -->"
        return html, cur, ""
    return "", cur, "bahut zyada redirect"


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return BeautifulSoup("", "html.parser")


def extract_links(soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
    internal, external, seen = [], [], set()
    base_host = urlparse(base_url).netloc.lower()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = urljoin(base_url, href).split("#")[0]
        if not full.startswith(("http://", "https://", "/")):
            continue
        if full in seen:
            continue
        seen.add(full)
        host = urlparse(full).netloc.lower()
        (internal if (not host or host == base_host) else external).append(full)
        if len(internal) >= 100 and len(external) >= 100:
            break
    return {"internal": internal[:100], "external": external[:100],
            "total": len(internal) + len(external)}


def extract_meta(soup: BeautifulSoup, url: str) -> Dict[str, Any]:
    def _txt(tag):
        return (tag.get_text() or "").strip() if tag else ""

    def _meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
            if t and t.get("content"):
                return t["content"].strip()
        return ""

    icon = soup.find("link", rel=lambda x: x and "icon" in str(x).lower())
    canonical = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
    return {
        "title": _txt(soup.title)[:200],
        "description": _meta("description", "og:description", "twitter:description")[:500],
        "keywords": _meta("keywords")[:500],
        "og_image": _meta("og:image", "twitter:image"),
        "favicon": urljoin(url, icon.get("href")) if (icon and icon.get("href")) else "",
        "canonical": urljoin(url, canonical.get("href")) if (canonical and canonical.get("href")) else "",
        "h1": [h.get_text().strip()[:160] for h in soup.find_all("h1")][:5],
        "h2": [h.get_text().strip()[:160] for h in soup.find_all("h2")][:10],
        "og_title": _meta("og:title", "twitter:title")[:200],
    }


@with_cache("web", 1800, 21600)
def website_extractor(url: str) -> Dict[str, Any]:
    """URL -> emails / phones / links / meta. SSRF-safe, size-capped, cached."""
    target = _normalize_url(url)
    if not target:
        return {"ok": False, "error": "URL khaali hai."}

    html, final_url, err = _fetch(target)
    if err:
        return {"ok": False, "error": err, "url": target}

    soup = _soup(html)                       # ek hi parse — sab isse
    text = soup.get_text(" ", strip=True)
    blob = html + " " + text

    emails = extract_emails(blob)
    phones = extract_phones(blob)
    links = extract_links(soup, final_url)
    meta = extract_meta(soup, final_url)

    return {
        "ok": True,
        "url": final_url,
        "requested": target,
        "redirected": final_url != target,
        "meta": meta,
        "title": meta["title"],
        "description": meta["description"],
        "emails": emails,
        "phones": phones,
        "links": links,
        "counts": {
            "emails": len(emails),
            "phones": len(phones),
            "internal_links": len(links["internal"]),
            "external_links": len(links["external"]),
        },
        "size_kb": round(len(html) / 1024, 1),
        "source": "self-hosted",
    }
