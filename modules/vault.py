# -*- coding: utf-8 -*-
"""
🛡️ v2.0 VAULT — OSINT Hunter Hub ka data kabhi khoyega nahi
================================================================================
ASLI PROBLEM:
    Render free plan ka filesystem "ephemeral" hai. Deploy / restart / maintenance
    par `botdata.db` (users, credits, premium, PENDING PAYMENTS) poora saaf ho
    jata tha. Matlab: naya deploy = saare premium users gayab, credits reset,
    pending payments delete. (utility-duniya-bot me bhi yahi problem thi — wahan
    vault laga hai, ab yahan bhi.)

AB KAISE FIX:
    1) HAR 10 MIN: DB ka ENCRYPTED backup GitHub par (branch: `vault-backup`).
       Branch alag hai isliye Render ka auto-deploy trigger NAHI hota.
    2) NAYA DEPLOY (DB khaali): backup se data wapas aa jata hai.
    3) Backup AES-level encrypted (SHAKE256 keystream + HMAC-SHA256) hai —
       repo public hai tab bhi koi padh nahi sakta. Zero nayi dependency.
    4) SAFETY: agar local DB me users hain to restore KABHI overwrite nahi karta
       (galat backup se data kharab ho hi nahi sakta).

ENV (Render me set):
    VAULT_GITHUB_TOKEN   — GitHub PAT (repo scope)          [zaroori]
    VAULT_REPO           — default savecontentrestricted-pixel/osint
    VAULT_BRANCH         — default vault-backup
    VAULT_KEY            — encryption key (na ho to BOT_TOKEN se ban jata hai)
    VAULT_BACKUP_MINUTES — default 10

ADMIN COMMANDS (bot me):
    /vault    — vault ki health report
    /backup   — abhi turant backup banao
    /restore  — DB khaali ho to backup se wapas laao
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time

_VAULT_PATH = "botdata.db.enc"
_state = {
    "last_ok": 0.0,
    "last_err": "",
    "last_bytes": 0,
    "count": 0,
    "restored": "",
    "started": 0.0,
}
_lock = threading.Lock()
_started = False


def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()


def token() -> str:
    return _env("VAULT_GITHUB_TOKEN") or _env("GITHUB_TOKEN")


def repo() -> str:
    return _env("VAULT_REPO", "savecontentrestricted-pixel/osint")


def branch() -> str:
    return _env("VAULT_BRANCH", "vault-backup")


def _secret() -> str:
    k = _env("VAULT_KEY")
    if k:
        return k
    try:
        from config import BOT_TOKEN
        return "osintvault-" + (BOT_TOKEN or "nokey")
    except Exception:
        return "osintvault-nokey"


# ============================ crypto (no deps) ============================
def _keystream(key: bytes, n: int) -> bytes:
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.shake_256(key + ctr.to_bytes(8, "big")).digest(64)
        ctr += 1
    return bytes(out[:n])


def _derive(secret: str) -> bytes:
    return hashlib.sha256(("osint-vault-v1|" + secret).encode()).digest()


def encrypt_blob(data: bytes, secret: str) -> bytes:
    """OSV1 | salt(16) | hmac(32) | ciphertext"""
    salt = os.urandom(16)
    key = _derive(secret + salt.hex())
    ks = _keystream(key, len(data))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    mac = hmac.new(key, ct, hashlib.sha256).digest()
    return b"OSV1|" + salt + mac + ct


def decrypt_blob(blob: bytes, secret: str):
    try:
        if not blob or not blob.startswith(b"OSV1|"):
            return None
        salt = blob[5:21]
        mac = blob[21:53]
        ct = blob[53:]
        key = _derive(secret + salt.hex())
        if not hmac.compare_digest(mac, hmac.new(key, ct, hashlib.sha256).digest()):
            return None
        ks = _keystream(key, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks))
    except Exception:
        return None


# ============================ github helpers ============================
def _api(path: str) -> str:
    return f"https://api.github.com/repos/{repo()}{path}"


def _gh(method: str, url: str, payload=None, timeout: int = 30):
    import fasthttp as requests
    tok = token()
    if not tok:
        raise RuntimeError("VAULT_GITHUB_TOKEN missing")
    h = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return requests.request(method, url, headers=h, json=payload, timeout=timeout)


def ensure_branch() -> bool:
    r = _gh("GET", _api(f"/git/ref/heads/{branch()}"))
    if r.status_code == 200:
        return True
    r2 = _gh("GET", _api("/git/ref/heads/main"))
    if r2.status_code != 200:
        raise RuntimeError(f"main ref HTTP {r2.status_code}")
    sha = (r2.json() or {}).get("object", {}).get("sha")
    r3 = _gh("POST", _api("/git/refs"), {"ref": f"refs/heads/{branch()}", "sha": sha})
    return r3.status_code in (200, 201)


def _remote() -> tuple:
    r = _gh("GET", _api(f"/contents/{_VAULT_PATH}?ref={branch()}"))
    if r.status_code == 200:
        j = r.json() or {}
        try:
            data = base64.b64decode((j.get("content") or "").replace("\n", ""))
        except Exception:
            data = b""
        return j.get("sha"), data
    return None, b""


# ============================ db path / checkpoint ============================
def db_path() -> str:
    from modules.database import DB_PATH
    return DB_PATH


def _checkpoint():
    """WAL ko main file me flush karo (warna latest rows backup me na aayein)."""
    try:
        from modules.database import db as _db
        con = _db()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        con.commit()
        con.close()
    except Exception:
        pass


def _count_users(path: str) -> int:
    try:
        import sqlite3
        c = sqlite3.connect(path, timeout=5)
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cur.fetchone():
            c.close()
            return 0
        cur.execute("SELECT COUNT(*) FROM users")
        n = int((cur.fetchone() or [0])[0] or 0)
        c.close()
        return n
    except Exception:
        return 0


# ============================ backup / restore ============================
def backup_now(force: bool = False) -> bool:
    try:
        if not token():
            with _lock:
                _state["last_err"] = "VAULT_GITHUB_TOKEN not set"
            return False
        _checkpoint()
        p = db_path()
        if not os.path.exists(p):
            return False
        with open(p, "rb") as f:
            raw = f.read()
        if not raw:
            return False
        enc = encrypt_blob(raw, _secret())
        ensure_branch()
        sha, cur = _remote()
        if sha and cur == enc and not force:
            with _lock:
                _state["last_ok"] = time.time()
            return True
        payload = {
            "message": f"vault backup {time.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            "content": base64.b64encode(enc).decode(),
            "branch": branch(),
        }
        if sha:
            payload["sha"] = sha
        r = _gh("PUT", _api(f"/contents/{_VAULT_PATH}"), payload, timeout=60)
        ok = r.status_code in (200, 201)
        with _lock:
            if ok:
                _state.update(last_ok=time.time(), last_bytes=len(raw),
                              count=_state["count"] + 1, last_err="")
            else:
                _state["last_err"] = f"HTTP {r.status_code}: {(r.text or '')[:140]}"
        return ok
    except Exception as e:
        with _lock:
            _state["last_err"] = str(e)[:150]
        return False


def fetch_backup():
    try:
        _, data = _remote()
        if not data:
            return None
        return decrypt_blob(data, _secret())
    except Exception:
        return None


def boot_restore() -> str:
    """Naye deploy par (DB khaali) GitHub backup se data wapas laao."""
    try:
        p = db_path()
        users_local = _count_users(p) if os.path.exists(p) else 0
        if users_local > 0:
            msg = f"local DB OK ({users_local} users) — restore skip"
            with _lock:
                _state["restored"] = msg
                _state["started"] = time.time()
            print(f"[VAULT] {msg}")
            return msg
        if not token():
            msg = "no VAULT_GITHUB_TOKEN — restore skip"
            with _lock:
                _state["restored"] = msg
            print(f"[VAULT] {msg}")
            return msg
        raw = fetch_backup()
        if not raw:
            msg = "no backup found yet (fresh start)"
            with _lock:
                _state["restored"] = msg
                _state["started"] = time.time()
            print(f"[VAULT] {msg}")
            return msg
        for suf in ("-wal", "-shm"):
            try:
                if os.path.exists(p + suf):
                    os.remove(p + suf)
            except Exception:
                pass
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "wb") as f:
            f.write(raw)
        n = _count_users(p)
        msg = f"RESTORED from vault: {len(raw)} bytes, {n} users"
        with _lock:
            _state["restored"] = msg
            _state["started"] = time.time()
        print(f"[VAULT] ✅ {msg}")
        return msg
    except Exception as e:
        msg = f"restore error: {str(e)[:120]}"
        with _lock:
            _state["restored"] = msg
        print(f"[VAULT] {msg}")
        return msg


def backup_soon(delay: int = 5) -> None:
    """Payment/premium change ke turant baad background me backup."""
    def _run():
        try:
            time.sleep(delay)
            backup_now(force=True)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# v2.17.1 — backup CHUPCHAAP fail nahi hoga. 3 lagataar fail = admin ko Telegram alert.
FAIL_ALERT_AFTER = int(_env("VAULT_FAIL_ALERT_AFTER", "3") or 3)
_fails = {"n": 0, "alerted": False}
_on_fail_cb = None


def set_fail_callback(fn) -> None:
    """bot.py yahan admin-alert function set karta hai."""
    global _on_fail_cb
    _on_fail_cb = fn


def start_backup_loop() -> None:
    global _started
    if _started:
        return
    _started = True
    mins = 10
    try:
        mins = max(2, int(_env("VAULT_BACKUP_MINUTES", "10") or 10))
    except Exception:
        mins = 10

    def _loop():
        time.sleep(20)
        while True:
            try:
                ok = backup_now()
            except Exception:
                ok = False
            if ok:
                _fails["n"] = 0
                _fails["alerted"] = False
            else:
                _fails["n"] += 1
                err = status().get("last_err") or "unknown"
                print(f"[VAULT] ⚠️ backup FAIL #{_fails['n']}: {err}", flush=True)
                if _fails["n"] >= FAIL_ALERT_AFTER and not _fails["alerted"]:
                    _fails["alerted"] = True
                    try:
                        if _on_fail_cb:
                            _on_fail_cb(_fails["n"], err)
                    except Exception:
                        pass
            time.sleep(mins * 60)

    threading.Thread(target=_loop, daemon=True).start()
    print(f"[VAULT] auto-backup ON → {repo()}@{branch()} (har {mins} min, encrypted)")


def status() -> dict:
    with _lock:
        st = dict(_state)
    st["repo"] = f"{repo()}@{branch()}"
    st["token_set"] = bool(token())
    st["age_s"] = int(time.time() - st["last_ok"]) if st["last_ok"] else -1
    st["db_path"] = db_path()
    try:
        st["db_users"] = _count_users(st["db_path"])
        st["db_bytes"] = os.path.getsize(st["db_path"]) if os.path.exists(st["db_path"]) else 0
    except Exception:
        pass
    return st
