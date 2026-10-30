# -*- coding: utf-8 -*-
"""
net.py — Ek hi professional HTTP layer (saare tools ke liye)
===========================================================
Pehle kya galat tha:
  • 47 alag-alag `requests.get/post` calls, 9 me `timeout` hi nahi tha
    → ek slow server poora bot hang kar deta tha (forever).
  • Har call naya TCP+TLS connection banata tha → slow + CPU waste.
  • Retry logic har module me alag (aur mostly absent).
  • Koi SSRF guard nahi tha — URL tools (screenshot / expand / link check)
    se koi bhi `http://169.254.169.254/` (cloud metadata) jaisa internal
    address hit karwa sakta tha. Ye Render par REAL risk hai.

Ab:
  • Ek shared connection pool (HTTPAdapter + retry) — fast + kam CPU.
  • Har request par DEFAULT TIMEOUT. Timeout dena bhoolna ab possible hi nahi.
  • Exponential backoff retry (429 / 5xx / connection error par).
  • `is_safe_url()` — private / metadata / loopback IP block.
  • Response size cap — koi 5 GB file download karke RAM nahi kha jayega.
  • Fail-safe: koi bhi exception `NetError` me wrap hota hai, kabhi traceback nahi.

Note: ye module intentionally `requests` par bana hai (pehle se installed hai),
      taaki Render par kuch naya install na karna pade.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger("ud.net")

__all__ = [
    "NetError", "session", "pooled_session", "http_get", "http_get_json",
    "http_post", "http_delete",
    "http_post_json", "http_head", "http_bytes", "is_safe_url",
    "DEFAULT_TIMEOUT", "DEFAULT_UA",
]

DEFAULT_TIMEOUT = float(os.environ.get("NET_TIMEOUT", "20"))
DEFAULT_UA = os.environ.get(
    "NET_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 UtilityDuniyaBot/5.0",
)
MAX_BYTES = int(os.environ.get("NET_MAX_MB", "150")) * 1024 * 1024
_RETRIES = int(os.environ.get("NET_RETRIES", "2"))


class NetError(Exception):
    """Har network failure isi me wrap hoti hai — user ko saaf message milta hai."""

    def __init__(self, message: str, status: Optional[int] = None, kind: str = "network"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind


# ------------------------------------------------------------ shared session
class _PoolAdapter(HTTPAdapter):
    """Connection pooling ONLY — retry intentionally OFF.

    Kyun: urllib3 ka transport-level retry `timeout` ko multiply kar deta hai
    (timeout=2 + 3 transport attempts = 6 second wait, jo caller ne maanga hi nahi).
    Retry ka ek hi malik hona chahiye — wo `_request()` ka apna loop hai, jisme
    backoff + 429/Retry-After handling hai. Isse timeout predictable rehta hai.
    """

    def __init__(self, pool_connections: int = 16, pool_maxsize: int = 32):
        super().__init__(pool_connections=pool_connections, pool_maxsize=pool_maxsize,
                         max_retries=0)


_session: Optional[requests.Session] = None


def session() -> requests.Session:
    """Ek hi shared Session — connection reuse = fast + kam CPU."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "en-IN,en;q=0.9"})
        try:
            s.mount("https://", _PoolAdapter())
            s.mount("http://", _PoolAdapter())
        except Exception:  # noqa: BLE001 - adapter fail ho to plain session chalega
            pass
        _session = s
    return _session


def close_session() -> None:
    """Shutdown / test ke baad connection pool band karo."""
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:  # noqa: BLE001
            pass
        _session = None


def pooled_session(headers: Dict[str, str] = None) -> requests.Session:
    """Ek NAYA session jisme connection-pool adapter mount hai (v55).

    Kab chahiye: jab flow ko apni **cookies** set karni pade (jaise Terabox
    ndus login-cookie). Shared `session()` par cookie set karna DO users ke
    beech cookie leak karwaata (ek ka login dusre ki request me chala jata) —
    wo security bug hai. Ye har baar fresh session deta hai (isolated cookies),
    par andar bhi connection pooling rehti hai (multi-request flows fast).
    """
    s = requests.Session()
    if headers:
        s.headers.update(headers)
    else:
        s.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "en-IN,en;q=0.9"})
    try:
        s.mount("https://", _PoolAdapter())
        s.mount("http://", _PoolAdapter())
    except Exception:  # noqa: BLE001 - adapter fail ho to plain session chalega
        pass
    return s


# ------------------------------------------------------------ SSRF guard
# Ye ranges public internet par routable nahi hoti — inhe hit karna ya to
# meaningless hai ya (cloud metadata) dangerous.
_BLOCKED_NETS = [
    ipaddress.ip_network(n) for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
        "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
        "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
        "::1/128", "fc00::/7", "fe80::/10", "::/128",
    )
]

# Cloud providers ke metadata endpoints — inka DNS public dikhta hai par
# andar ka address link-local hota hai. DNS-resolve karke hi confirm karenge.
_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_private or addr.is_loopback or addr.is_link_local \
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return True
    return any(addr in net for net in _BLOCKED_NETS)


def is_safe_url(url: str, *, allow_ip_literals: bool = True,
                resolve: bool = True) -> tuple:
    """URL safe hai ya nahi.

    Returns (safe: bool, reason: str).

    Checks:
      1. scheme sirf http/https
      2. hostname mojood ho
      3. hostname directly private IP literal na ho
      4. cloud-metadata hostnames na ho
      5. DNS resolve karke resolved IP private na ho (DNS-rebinding ka basic guard)

    `resolve=False` rakho jab DNS lookup ka kharcha na chahiye (jaise
    sirf display ke liye URL parse kar rahe ho).
    """
    try:
        p = urlparse(url if "://" in url else "https://" + url)
    except Exception:  # noqa: BLE001
        return False, "URL parse nahi ho paya."
    if p.scheme not in ("http", "https"):
        return False, f"'{p.scheme}' scheme allowed nahi hai (sirf http/https)."
    host = (p.hostname or "").strip().rstrip(".")
    if not host:
        return False, "URL me website ka naam nahi mila."
    if len(host) > 253:
        return False, "Website ka naam bahut lamba hai."
    if host.lower() in _METADATA_HOSTS:
        return False, "Ye internal cloud address hai — block hai."
    # IP literal?
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        if not allow_ip_literals:
            return False, "Seedha IP address allowed nahi hai — domain bhejo."
        if _ip_is_blocked(host):
            return False, "Ye private / internal IP hai — public info nahi hoti."
        return True, ""
    # domain → DNS resolve karke check
    if resolve:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:  # noqa: BLE001
            # DNS fail hona "unsafe" nahi hai (site down ho sakti hai) — aage badhne do
            return True, ""
        for info in infos:
            ip = info[4][0]
            if _ip_is_blocked(ip):
                return False, f"'{host}' ek private / internal address par point karta hai — block hai."
    return True, ""


# ------------------------------------------------------------ core request
def _request(method: str, url: str, *, params: Dict[str, Any] = None,
             data: Any = None, json: Any = None,
             headers: Dict[str, str] = None,
             timeout: Optional[float] = None,
             retries: Optional[int] = None,
             max_bytes: Optional[int] = None,
             allow_redirects: bool = True,
             stream: bool = False,
             ssrf_check: bool = False,
             referer: str = "") -> requests.Response:
    """Sab requests isi se jaati hain. Kabhi bhi timeout ke bina request nahi jaati."""
    if ssrf_check:
        safe, why = is_safe_url(url)
        if not safe:
            raise NetError(why, kind="blocked")

    h = {"User-Agent": DEFAULT_UA}
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)

    tmo = DEFAULT_TIMEOUT if timeout is None else float(timeout)
    tries = 1 if retries is None else max(1, int(retries) + 1)
    cap = MAX_BYTES if max_bytes is None else int(max_bytes)

    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            r = session().request(
                method, url, params=params, data=data, json=json,
                headers=h, timeout=tmo, allow_redirects=allow_redirects, stream=True,
            )
        except requests.Timeout:
            last = NetError("Server ne jawab dene me bahut time laga diya.", kind="timeout")
            log.debug("timeout %s (attempt %s/%s)", url, attempt + 1, tries)
        except requests.ConnectionError as e:
            last = NetError(f"Server se connect nahi ho paya: {str(e)[:80]}", kind="connect")
        except requests.RequestException as e:
            last = NetError(f"Request fail: {str(e)[:80]}", kind="network")
        else:
            # 429 / 5xx par retry karo (429 ka Retry-Header respect karo)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                wait = _backoff(attempt, r.headers.get("Retry-After"))
                r.close()
                time.sleep(wait)
                last = NetError("Server busy tha.", status=r.status_code, kind="busy")
                continue
            # size cap — body ko cap tak hi padho
            try:
                chunks, got = [], 0
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    got += len(chunk)
                    if got > cap:
                        r.close()
                        raise NetError(
                            f"File bahut badi hai (limit {cap // (1024*1024)} MB).",
                            kind="toobig")
                    chunks.append(chunk)
                r._content = b"".join(chunks)   # noqa: SLF001 - intentional: cap enforce karna
                r._content_consumed = True      # noqa: SLF001
            except NetError:
                raise
            except Exception as e:  # noqa: BLE001
                raise NetError(f"Data padhne me dikkat: {str(e)[:80]}", kind="read")
            if not stream:
                r.close()
            return r
        # retry se pehle wait (429 ke alawa)
        if attempt < tries - 1:
            time.sleep(_backoff(attempt, None))
    raise last if last else NetError("Request fail ho gaya.", kind="network")


def _backoff(attempt: int, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 10.0)
        except ValueError:
            pass
    return min(0.4 * (2 ** attempt), 4.0)


# ------------------------------------------------------------ public helpers
def http_get(url: str, **kw) -> requests.Response:
    return _request("GET", url, **kw)


def http_head(url: str, **kw) -> requests.Response:
    return _request("HEAD", url, **kw)


def http_post(url: str, **kw) -> requests.Response:
    return _request("POST", url, **kw)


def http_delete(url: str, **kw) -> requests.Response:
    """DELETE request (mail.tm account delete jaise endpoints ke liye).

    ⚠️ v55 fix: temp-mail ka `tm_delete()` pehle POST se account delete karne ki
    koshish karta tha — mail.tm sirf DELETE method accept karta hai, isliye
    mailbox hamesha "delete nahi ho paya" ke saath fail hota tha (feature broken).
    """
    return _request("DELETE", url, **kw)


def http_get_json(url: str, **kw) -> Any:
    """GET → JSON. 200 nahi mila ya JSON nahi aaya to NetError."""
    r = _request("GET", url, **kw)
    return _json_or_raise(r)


def http_post_json(url: str, **kw) -> Any:
    r = _request("POST", url, **kw)
    return _json_or_raise(r)


def http_bytes(url: str, **kw) -> bytes:
    """GET → raw bytes (image/audio/pdf download ke liye). Size-capped."""
    r = _request("GET", url, **kw)
    if r.status_code != 200:
        raise NetError(f"Download fail (HTTP {r.status_code}).", status=r.status_code)
    if not r.content:
        raise NetError("Server ne khali file bheji.", kind="empty")
    return r.content


def _json_or_raise(r: requests.Response) -> Any:
    if r.status_code == 404:
        raise NetError("Ye record API ke database me nahi mila.", status=404, kind="notfound")
    if r.status_code == 429:
        raise NetError("API rate-limit hit ho gaya — 1 minute baad try karo.", status=429, kind="ratelimit")
    if r.status_code >= 400:
        raise NetError(f"API ne HTTP {r.status_code} bheja.", status=r.status_code, kind="http")
    try:
        return r.json()
    except ValueError as e:
        raise NetError(f"API ne JSON nahi bheja: {str(e)[:60]}", kind="badjson")
