# -*- coding: utf-8 -*-
"""
vault.py — 🛡️ PREMIUM VAULT: Data kabhi khoyega nahi, premium kabhi nahi jayega
==============================================================================
YE FILE AAPKI SABSE BADI PROBLEM KA PERMANENT FIX HAI.

----------------------------------------------------------------------
ASLI PROBLEM KYA THI (asli jadd):
----------------------------------------------------------------------
Bot ka poora data (users, VIP premium, payments, credits, referrals) ek SQLite
file `botdata.db` me rehta hai. Wo file Render ke server par padi hoti hai.

Render ka **free plan** ek "ephemeral filesystem" deta hai. Iska matlab:
    • Deploy karo          → filesystem WIPE -> botdata.db GAYAB
    • Bot restart ho       → botdata.db GAYAB
    • Service sleep/wake   → botdata.db GAYAB
    • Render maintenance   → botdata.db GAYAB

Aapke render.yaml me `disk:` section THA HI NAHI. Isliye jab bhi aap code
push karte the aur Render naya deploy karta tha, **poora database saaf ho
jata tha** — saare premium users delete, saara revenue record delete.

Ye bot ki galti nahi thi, sirf storage ki galti thi. Ab fix hai.

----------------------------------------------------------------------
AB KAISE FIX HAI — 4 LAYERS:
----------------------------------------------------------------------

  LAYER 1 — 🏠 STABLE PATH
      DB ab pehle `/var/data/` (Render disk ka standard mount point) dekhta
      hai. Kal aap Render par disk laga do, bot khud wahan data rakhega —
      koi code change nahi.

  LAYER 2 — 📦 AUTO-BACKUP (do jagah, dono free)
      (a) TELEGRAM  — har backup aapko bot se ek chhoti file ke roop me milta
          hai (owner DM me). Aapko dikhega ki backup ho raha hai = bharosa.
      (b) GITHUB    — aapka GitHub token use karke backup ek private folder me
          chala jata hai. Repo public ho to bhi koi nahi padh sakta —
          **backup AES-level encrypted hota hai** (SHAKE256 keystream +
          HMAC-SHA256). Zero nayi dependency.

  LAYER 3 — 🔀 SMART MERGE RESTORE (yahi asli jaadu hai)
      Restore "overwrite" NAHI karta — **MERGE** karta hai. Matlab:
          • Premium jiski expiry zyada, WAHI jeetega (kabhi ghatt nahi hogi)
          • Credits kam nahi honge
          • Referrals kam nahi honge
          • Ban hataya nahi jayega
          • Purana payment record kabhi delete nahi hoga
      Isliye galat backup se bhi aapka data KHARAB nahi ho sakta.

  LAYER 4 — 🔒 PREMIUM FLOOR GUARD
      Har restore se pehle bot gin leta hai: "kitne VIP users hain?"
      Agar restore ke baad VIP count kam ho jaye -> RESTORE ABORT.
      Purana (achi wali) DB wapas lag jaata hai.
      >>> PREMIUM USER ADMIK TARAH SE KABHI KAM NAHI HO SAKTA. <<<

----------------------------------------------------------------------
ADMIN COMMANDS (bot me):
    /vault          — vault ki poori health report
    /backup         — abhi turant backup banao
    /restore        — GitHub/Telegram se sabse accha backup merge karo
    /vips           — saare premium users ki list (report file)
----------------------------------------------------------------------
"""
from __future__ import annotations

import asyncio          # v75 FIX: pehle ye import module-level par NAHI tha.
#                        `backup_soon()` ke andar `await asyncio.sleep()` use hota
#                        tha lekin `asyncio` sirf ek doosre function ke andar
#                        import tha -> NameError -> VIP grant/payment approve hone
#                        par turant backup wala path CRASH ho jata tha.
#                        (pyflakes ne pakda: vault.py:1261 undefined name)
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

log = logging.getLogger("utility-super-bot.vault")

__all__ = [
    "db_path", "connect", "Vault", "vault",
    "snapshot_rows", "merge_rows", "premium_floor_report",
    "encrypt_blob", "decrypt_blob", "premium_rank", "is_lifetime",
]

# =====================================================================
#  LAYER 1 — STABLE DB PATH
# =====================================================================
_RENDER_DISK_DIRS = ("/var/data", "/data", "/mnt/data", "/opt/data")


# ----------------------------------------------------------------------
# v66: MAIN EVENT LOOP ka registry
# ----------------------------------------------------------------------
# PROBLEM (aapke log me dikha):
#   "Telegram backup fail: Unknown error in HTTP implementation:
#    RuntimeError('<asyncio.locks.Event object ...> is bound to a
#    different event loop')"
# Wajah: auto-backup ka thread `asyncio.run()` se NAYA loop banata tha,
#        par Telegram bot ka HTTP client PURANE (main) loop se juda tha.
# Ilaaj: thread se hamesha MAIN loop par kaam bhejo
#        (run_coroutine_threadsafe) — naya loop kabhi mat banao.
# ----------------------------------------------------------------------
_MAIN_LOOP = None
_MAIN_LOOP_LOCK = threading.Lock()


def set_main_loop(loop=None) -> None:
    """Bot boot hote waqt main loop register karo (ek hi baar)."""
    global _MAIN_LOOP
    try:
        if loop is None:
            import asyncio as _a
            loop = _a.get_running_loop()
        with _MAIN_LOOP_LOCK:
            _MAIN_LOOP = loop
    except Exception as e:                                       # noqa: BLE001
        logging.getLogger("vault").debug("set_main_loop skip: %s", str(e)[:80])


def main_loop():
    """Registered main loop (ya None)."""
    with _MAIN_LOOP_LOCK:
        lp = _MAIN_LOOP
    try:
        if lp is not None and not lp.is_closed():
            return lp
    except Exception:                                            # noqa: BLE001
        pass
    return None


def run_coro_blocking(coro, timeout: float = 120.0):
    """Kisi bhi thread se coroutine chalao — SAFE tarike se.

    - Main loop zinda hai  -> run_coroutine_threadsafe (wahi loop, same objects)
    - Nahi hai (CLI/tests) -> asyncio.run (naya loop, koi bot client nahi)
    """
    lp = main_loop()
    if lp is not None:
        try:
            import asyncio as _a
            fut = _a.run_coroutine_threadsafe(coro, lp)
            return fut.result(timeout=timeout)
        except Exception as e:                                   # noqa: BLE001
            logging.getLogger("vault").warning("main-loop task fail: %s", str(e)[:120])
            return None
    try:
        import asyncio as _a
        return _a.run(coro)
    except Exception as e:                                       # noqa: BLE001
        logging.getLogger("vault").warning("vault task fail: %s", str(e)[:120])
        return None


def _writable_dir(path: str) -> bool:
    try:
        if not os.path.isdir(path):
            return False
        probe = os.path.join(path, ".vault_write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:                                            # noqa: BLE001
        return False


def db_path() -> str:
    """DB file ka pura path — chuno is tarah:

    1. `DB_PATH` env (aapne khud diya ho to wahi sabse pehle)
    2. `DATA_DIR` env + botdata.db
    3. `/var/data/botdata.db`   <- Render persistent disk ka standard mount
                                   (kal disk laga do, bot khud yahan shift ho jayega)
    4. `./botdata.db`           <- abhi yahi chal raha hai (backward compatible)

    Ye function KABHI exception nahi deta.
    """
    try:
        explicit = (os.environ.get("DB_PATH") or "").strip()
        if explicit:
            d = os.path.dirname(os.path.abspath(explicit))
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:                                    # noqa: BLE001
                pass
            return explicit
        data_dir = (os.environ.get("DATA_DIR") or "").strip()
        if data_dir:
            try:
                os.makedirs(data_dir, exist_ok=True)
            except Exception:                                    # noqa: BLE001
                pass
            if _writable_dir(data_dir):
                return os.path.join(data_dir, "botdata.db")
        # Render disk ka default mount point — khud detect hota hai
        if not os.environ.get("VAULT_NO_DISK_DETECT"):
            for d in _RENDER_DISK_DIRS:
                if _writable_dir(d):
                    return os.path.join(d, "botdata.db")
    except Exception:                                            # noqa: BLE001
        pass
    return "botdata.db"


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """SQLite connection jo kabhi 'database is locked' par nahi atakta.

    • WAL mode   — ek banda padh raha ho aur doosra likh raha ho, dono chalte
                   hain. Pehle "database is locked" aata tha (kai users ek saath
                   tool chalate hi crash).
    • busy_timeout — 15 second tak line me wait karta hai (pehle 5s tha).
    • synchronous=NORMAL — free plan par tez + safe.
    """
    p = path or db_path()
    con = sqlite3.connect(p, timeout=15.0, check_same_thread=False)
    for pragma in (
        "PRAGMA journal_mode=WAL",       # ek reader + ek writer ek saath
        "PRAGMA busy_timeout=15000",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA temp_store=MEMORY",
        "PRAGMA foreign_keys=ON",
    ):
        try:
            con.execute(pragma)
        except Exception:                                        # noqa: BLE001
            pass
    return con


# =====================================================================
#  LAYER 3 ke helpers — premium_until ki TULNA (sabse zaroori logic)
# =====================================================================
def is_lifetime(val: Any) -> bool:
    return str(val or "").strip().lower() in ("lifetime", "life", "forever", "hamesha",
                                              "9999", "inf", "infinite")


def _parse_dt(val: Any):
    """Har tarah ki date string ko datetime me badlo — kabhi exception nahi.

    Ye ek ASLI BUG ka fix hai: purane code me `datetime.fromisoformat()` seedha
    use hota tha. Agar value me timezone ho (`2026-01-01T00:00:00+00:00`) to
    wo 'aware' datetime banta hai, aur `aware > naive` compare karne par
    **TypeError** aata tha -> except me chala jata -> `is_premium()` False ->
    >>> USER KA PREMIUM CHALU HOTE HUE BHI BAND DIKHTA THA. <<<
    Ab har format chalta hai: Z, +05:30, space, sirf date, epoch.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    s = str(val).strip()
    if not s or is_lifetime(s):
        return None
    # epoch seconds?
    if s.replace(".", "", 1).isdigit() and len(s) >= 9:
        try:
            return datetime.fromtimestamp(float(s))
        except Exception:                                        # noqa: BLE001
            pass
    cand = s.replace("Z", "+00:00").replace("z", "+00:00")
    for attempt in (cand, s, s.replace(" ", "T"), s[:19], s[:16]):
        try:
            d = datetime.fromisoformat(attempt)
            return d.replace(tzinfo=None) if d.tzinfo else d
        except Exception:                                        # noqa: BLE001
            continue
    for fmt in ("%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 4].strip(), fmt)
        except Exception:                                        # noqa: BLE001
            continue
    return None


def premium_rank(val: Any) -> float:
    """Premium ki 'taakat' — jitna bada, utna zyada premium.

        lifetime            -> 9.9e12  (sabse upar, hamesha jeetega)
        valid future date   -> epoch seconds
        khaali / purani     -> 0.0
    """
    if is_lifetime(val):
        return 9.9e12
    d = _parse_dt(val)
    if d is None:
        return 0.0
    try:
        return float(d.timestamp())
    except Exception:                                            # noqa: BLE001
        return 0.0


def premium_max(a: Any, b: Any) -> str:
    """Dono me se ZYADA premium chuno. Kabhi ghatt nahi karega."""
    ra, rb = premium_rank(a), premium_rank(b)
    if ra <= 0 and rb <= 0:
        return str(a or b or "")
    win = a if ra >= rb else b
    if is_lifetime(win):
        return "lifetime"
    return str(win)


# =====================================================================
#  LAYER 4 — snapshot + merge engine
# =====================================================================
_SETTINGS_KEYS = ("bot_meta", "cloner_configs", "users", "payments", "vip_grants")


def _table_names(con: sqlite3.Connection) -> list:
    try:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name NOT LIKE 'sqlite_%'")
        return [r[0] for r in cur.fetchall()]
    except Exception:                                            # noqa: BLE001
        return []


def snapshot_rows(path: Optional[str] = None) -> dict:
    """Poora DB -> {table: [row_dict, ...]}. Kisi bhi table ke missing hone par
    chup-chaap skip (purani DB bhi chalegi)."""
    out: dict = {}
    p = path or db_path()
    if not os.path.exists(p):
        return out
    try:
        con = sqlite3.connect(p, timeout=10.0)
        try:
            for t in _table_names(con):
                try:
                    cur = con.execute(f"SELECT * FROM {t}")       # noqa: S608 (internal)
                    cols = [d[0] for d in cur.description] if cur.description else []
                    out[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
                except Exception:                                # noqa: BLE001
                    continue
        finally:
            con.close()
    except Exception as e:                                       # noqa: BLE001
        log.warning("snapshot padha nahi gaya: %s", str(e)[:120])
    return out


def _val(row: dict, *names, default=None):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return default


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:                                            # noqa: BLE001
        return default


def merge_users(local: list, remote: list) -> tuple:
    """👑 SABSE ZAROORI FUNCTION — do users tables ka PREMIUM-SAFE merge.

    RULE SET (design by aapki requirement "premium delete na ho"):
      premium_until : DONO ka max      (zyada premium hamesha jeetega)
      credits       : DONO ka max      (credits kabhi kam nahi honge)
      referrals     : DONO ka max
      banned        : max              (ban kabhi nahi hatega)
      referred_by   : pehla non-zero
      last_date     : dono me se NAYA (naye wale ka uses_today lega)
      joined_at     : dono me se PURANA (join date peeche nahi jayegi)
      name/username : naye wale ka, warna jo bhara hua hai
      trial_count   : max

    Returns (merged_rows, report_dict)
    """
    rep = {"premium_kept": 0, "premium_upgraded": 0, "credits_kept": 0,
           "only_local": 0, "only_remote": 0, "total": 0}
    lmap = {_int(r.get("user_id")): r for r in (local or []) if _int(r.get("user_id"))}
    rmap = {_int(r.get("user_id")): r for r in (remote or []) if _int(r.get("user_id"))}
    keys = set(lmap) | set(rmap)
    out = []
    for uid in keys:
        a, b = lmap.get(uid), rmap.get(uid)
        if a is None:
            out.append(dict(b))
            rep["only_remote"] += 1
            continue
        if b is None:
            out.append(dict(a))
            rep["only_local"] += 1
            continue
        # ---- dono me hai: har field ko safety ke saath milao
        m = dict(a)
        pa, pb = a.get("premium_until", ""), b.get("premium_until", "")
        best = premium_max(pa, pb)
        m["premium_until"] = best
        ra, rb = premium_rank(pa), premium_rank(pb)
        if max(ra, rb) > 0:
            if pa and best == pa:
                rep["premium_kept"] += 1
            if rb > ra and pb:
                rep["premium_upgraded"] += 1
        m["credits"] = max(_int(a.get("credits")), _int(b.get("credits")))
        if m["credits"] > _int(a.get("credits")):
            rep["credits_kept"] += 1
        m["referrals"] = max(_int(a.get("referrals")), _int(b.get("referrals")))
        m["banned"] = max(_int(a.get("banned")), _int(b.get("banned")))
        m["trial_count"] = max(_int(a.get("trial_count")), _int(b.get("trial_count")))
        m["referred_by"] = _int(_val(a, "referred_by", default=0)) or \
            _int(_val(b, "referred_by", default=0))
        # ---- kaun NAYA hai (activity ke hisaab se)
        da, db_ = str(a.get("last_date") or ""), str(b.get("last_date") or "")
        newer, older = (a, b) if db_ <= da else (b, a)
        m["last_date"] = max(da, db_) or newer.get("last_date", "")
        m["uses_today"] = _int(newer.get("uses_today"))
        m["name"] = str(_val(a, "name", default="") or _val(b, "name", default="") or "")
        m["username"] = str(_val(a, "username", default="") or _val(b, "username", default="") or "")
        j = [str(x) for x in (a.get("joined_at"), b.get("joined_at")) if x]
        m["joined_at"] = min(j) if j else ""
        td = [str(x) for x in (a.get("trial_date"), b.get("trial_date")) if x]
        m["trial_date"] = max(td) if td else ""
        out.append(m)
    rep["total"] = len(out)
    return out, rep


def merge_rows(local: dict, remote: dict) -> tuple:
    """Poore DB ka premium-safe merge. Returns (merged_dict, report)."""
    report: dict = {}
    merged: dict = {}

    for t in set(list(local.keys()) + list(remote.keys())):
        la, ra = (local or {}).get(t) or [], (remote or {}).get(t) or []

        if t == "users":
            rows, rep = merge_users(la, ra)
            merged[t] = rows
            report["users"] = rep
            continue

        # ---- baaki tables: KEY ke hisaab se union (purana record kabhi na toote)
        if t == "bot_meta":
            keyf = "key"
        elif t == "cloner_configs":
            keyf = "user_id"
        elif t in ("payments", "vip_grants"):
            keyf = "id"
        else:
            keyf = None

        if not keyf or not la or not ra:
            # ek taraf khaali hai -> jitne hain sab le lo (dedupe by key agar possible)
            allr = list(la) + list(ra)
            if keyf:
                seen, ded = set(), []
                for r in allr:
                    k = r.get(keyf)
                    if k in seen:
                        continue
                    seen.add(k)
                    ded.append(r)
                allr = ded
            merged[t] = allr
            report[t] = {"merged": len(allr)}
            continue

        # key-based union: LOCAL jeetta hai (naya data), missing REMOTE se aata hai
        out, seen = [], set()
        for r in la:
            k = r.get(keyf)
            seen.add(k)
            out.append(dict(r))
        added = 0
        for r in ra:
            k = r.get(keyf)
            if k in seen:
                continue
            seen.add(k)
            out.append(dict(r))
            added += 1
        merged[t] = out
        report[t] = {"kept_local": len(la), "added_from_backup": added}

    return merged, report


def premium_floor_report(rows_or_path) -> dict:
    """Kitne premium users hain — restore se pehle aur baad me ginte hain."""
    rows = rows_or_path
    if not isinstance(rows, list):
        snap = snapshot_rows(rows_or_path)
        rows = snap.get("users", [])
    now = datetime.now()
    lifetime = 0
    active = 0
    for r in rows or []:
        p = r.get("premium_until", "")
        if is_lifetime(p):
            lifetime += 1
        else:
            d = _parse_dt(p)
            if d and d > now:
                active += 1
    return {"lifetime": lifetime, "active": active, "total_premium": lifetime + active}


# =====================================================================
#  ENCRYPTION — repo public ho to bhi data safe
# =====================================================================
_MAGIC = b"UDVLT1"


def _keystream(key: bytes, n: int) -> bytes:
    """SHAKE256 based CSPRNG keystream — koi nayi library nahi chahiye."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.shake_256(key + counter.to_bytes(8, "big")).digest(64)
        counter += 1
    return bytes(out[:n])


def _derive_key(secret: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", str(secret).encode("utf-8"),
                               b"utility-duniya-vault-v1", 60000, dklen=32)


def encrypt_blob(data: bytes, secret: str) -> bytes:
    """Data ko encrypt karo. Format: MAGIC | salt | nonce | ciphertext | hmac"""
    raw = bytes(data or b"")
    salt = os.urandom(16)
    nonce = os.urandom(16)
    key = _derive_key(secret) + salt
    ks = _keystream(key + nonce, len(raw))
    ct = bytes(a ^ b for a, b in zip(raw, ks))
    tag = hmac.new(key + nonce, ct, hashlib.sha256).digest()
    return _MAGIC + salt + nonce + ct + tag


def decrypt_blob(blob: bytes, secret: str) -> Optional[bytes]:
    """Decrypt — galat key / toota data ho to None (crash nahi)."""
    try:
        b = bytes(blob or b"")
        if not b.startswith(_MAGIC):
            return b                      # purana unencrypted backup bhi chalega
        body = b[len(_MAGIC):]
        if len(body) < 48:
            return None
        salt, nonce = body[:16], body[16:32]
        tag = body[-32:]
        ct = body[32:-32]
        key = _derive_key(secret) + salt
        if not hmac.compare_digest(hmac.new(key + nonce, ct, hashlib.sha256).digest(), tag):
            log.warning("Vault backup ka HMAC match nahi hua — galat key ya file kharab")
            return None
        ks = _keystream(key + nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks))
    except Exception as e:                                       # noqa: BLE001
        log.warning("decrypt fail: %s", str(e)[:120])
        return None


# =====================================================================
#  THE VAULT
# =====================================================================
class Vault:
    """Backup + restore manager. Saare operations crash-proof hain."""

    BACKUP_PREFIX = "vault/udb_"
    KEEP_ON_GITHUB = 6          # repo me itne backup rakho (rotation)
    KEEP_LOCAL = 4

    def __init__(self) -> None:
        self.secret = self._secret()
        self.last_backup: dict = {"ok": False, "at": "", "where": "", "bytes": 0, "why": ""}
        self.last_restore: dict = {"ok": False, "at": "", "where": "", "why": "", "report": {}}
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.bot = None                     # bot.py set karega
        self.owner_id = 0
        self.backup_chat: Optional[int] = None
        self.stats = {"backups": 0, "restores": 0, "failures": 0, "last_bytes": 0}
        self._gh_cache: dict = {}

    # ------------------------------------------------------------ config
    def _secret(self) -> str:
        """Encryption key. VAULT_KEY diya ho to wahi, warna BOT_TOKEN se
        banaya jata hai (wo bhi secret hai) — isliye backup hamesha encrypted."""
        explicit = (os.environ.get("VAULT_KEY") or "").strip()
        if explicit:
            return explicit
        base = (os.environ.get("BOT_TOKEN") or "").strip()
        return "k-" + hashlib.sha256(("ud-vault::" + (base or "no-token")).encode()).hexdigest()

    def key_source(self) -> str:
        """Vault ki encryption key KAHAN se aa rahi hai (diagnostic).

        8 Oct 2026 ko user ne BotFather se token revoke kiya — aur key `BOT_TOKEN`
        se derive hoti thi, isliye `vault-backup` branch ke saare purane backups
        decrypt hona band ho gaye (restore bola "koi valid backup nahi mila",
        /health par `failures=1`). Render env me `VAULT_KEY` set karne par key
        token se independent ho jaati hai — token ghumao, data bacha rahega.
        """
        return "env" if (os.environ.get("VAULT_KEY") or "").strip() else "BOT_TOKEN"

    def gh_token(self) -> str:
        return (os.environ.get("GITHUB_BACKUP_TOKEN")
                or os.environ.get("VAULT_GITHUB_TOKEN")
                or os.environ.get("GITHUB_TOKEN") or "").strip()

    def gh_repo(self) -> str:
        return (os.environ.get("VAULT_GITHUB_REPO") or "").strip()

    def gh_branch(self) -> str:
        """⚠️ SABSE ZAROORI DETAIL — backup branch 'main' NAHI hona chahiye!

        Render ke auto-deploy ka matlab hai: **main branch par koi bhi push =
        naya deploy**. Agar hum backup main me daalte, to:
            backup push -> Render deploy -> filesystem wipe -> bot restart ->
            backup push -> Render deploy -> ... (infinite redeploy loop!)

        Isliye backup ek ALAG branch me jata hai (default: `vault-backup`).
        Render sirf 'main' dekhta hai, isliye us branch ka push koi deploy
        trigger nahi karta. Zero extra repo, zero extra kharcha.

        Chaho to alag private repo bhi de sakte ho:
            VAULT_GITHUB_REPO = yourname/vault-private
        """
        return (os.environ.get("VAULT_GITHUB_BRANCH") or "vault-backup").strip() or "vault-backup"

    def gh_ready(self) -> bool:
        return bool(self.gh_token() and self.gh_repo() and "/" in self.gh_repo())

    def _gh_default_branch(self) -> str:
        """Repo ka default branch (branch banane ke liye base chahiye)."""
        try:
            if "default" in self._gh_cache:
                return str(self._gh_cache["default"])
        except Exception:                                        # noqa: BLE001
            pass
        ok, data = self._gh("GET", f"/repos/{self.gh_repo()}")
        name = "main"
        if ok and isinstance(data, dict):
            name = str(data.get("default_branch") or "main")
        try:
            self._gh_cache["default"] = name
        except Exception:                                        # noqa: BLE001
            pass
        return name

    def _gh_ensure_branch(self) -> bool:
        """Backup branch (vault-backup) na ho to banado. Returns True agar ready."""
        want = self.gh_branch()
        try:
            ok, data = self._gh("GET",
                                f"/repos/{self.gh_repo()}/branches/{want}")
            if ok:
                return True
        except Exception:                                        # noqa: BLE001
            pass
        base = self._gh_default_branch()
        ok, data = self._gh("GET", f"/repos/{self.gh_repo()}/git/ref/heads/{base}")
        sha = ""
        if ok and isinstance(data, dict):
            sha = str(((data.get("object") or {}).get("sha")) or "")
        if not sha:
            return False
        ok2, resp = self._gh("POST", f"/repos/{self.gh_repo()}/git/refs",
                             {"ref": f"refs/heads/{want}", "sha": sha})
        if ok2:
            log.info("🌿 Vault branch '%s' banaya (Render isko deploy nahi karega)", want)
            return True
        # race: kisi aur ne bana di
        if "already exists" in str(resp).lower():
            return True
        log.warning("Vault branch nahi ban paya: %s", str(resp)[:150])
        return False

    def telegram_ready(self) -> bool:
        return bool(self.bot and self.backup_chat)

    def interval_minutes(self) -> int:
        try:
            return max(5, int(float(os.environ.get("VAULT_BACKUP_MINUTES") or 30)))
        except Exception:                                        # noqa: BLE001
            return 30

    def enabled(self) -> bool:
        """VAULT_ENABLED=off se poora vault band ho jata hai (kuch nahi tootega)."""
        v = str(os.environ.get("VAULT_ENABLED", "on")).strip().lower()
        return v not in ("off", "0", "false", "no", "band")

    # ------------------------------------------------------------ blob
    def dump_encrypted(self) -> Optional[bytes]:
        """DB ka encrypted snapshot."""
        p = db_path()
        if not os.path.exists(p):
            return None
        try:
            tmp = io.BytesIO()
            # SQLite ka apna safe backup API — file copy se behtar (WAL safe)
            src = sqlite3.connect(p, timeout=10.0)
            try:
                dst = sqlite3.connect(":memory:")
                with dst:
                    src.backup(dst)
                for line in dst.iterdump():
                    tmp.write((line + "\n").encode("utf-8"))
                dst.close()
            finally:
                src.close()
            plain = tmp.getvalue()
            return encrypt_blob(plain, self.secret)
        except Exception as e:                                   # noqa: BLE001
            log.warning("dump fail (%s) — raw file copy par switch", str(e)[:100])
            try:
                with open(p, "rb") as f:
                    return encrypt_blob(f.read(), self.secret)
            except Exception as e2:                              # noqa: BLE001
                log.error("raw dump bhi fail: %s", str(e2)[:100])
                return None

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    # ------------------------------------------------------------ GITHUB
    def _gh(self, method: str, path: str, payload: Optional[dict] = None,
            timeout: int = 45) -> tuple:
        """GitHub REST call. Returns (ok, json_or_text)."""
        import urllib.error
        import urllib.request
        url = "https://api.github.com" + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.gh_token()}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "utility-duniya-vault/1.0")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:                                    # noqa: BLE001
                body = ""
            return False, f"HTTP {e.code}: {body[:200]}"
        except Exception as e:                                   # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e)[:150]}"
        try:
            return True, json.loads(body) if body else {}
        except Exception:                                        # noqa: BLE001
            return True, body

    def _gh_put(self, name: str, blob: bytes) -> tuple:
        """Ek backup file repo me daalo (ya update karo)."""
        path = f"/repos/{self.gh_repo()}/contents/{name}"
        ok, cur = self._gh("GET", path + f"?ref={self.gh_branch()}")
        sha = None
        if ok and isinstance(cur, dict):
            sha = cur.get("sha")
        payload = {
            "message": f"vault: backup {name.split('/')[-1]}",
            "content": base64.b64encode(blob).decode("ascii"),
            "branch": self.gh_branch(),
        }
        if sha:
            payload["sha"] = sha
        return self._gh("PUT", path, payload, timeout=90)

    def _gh_list(self) -> list:
        """Repo me jitne vault backups hain — naye se purane."""
        path = f"/repos/{self.gh_repo()}/contents/vault?ref={self.gh_branch()}"
        ok, data = self._gh("GET", path)
        if not ok or not isinstance(data, list):
            return []
        items = [d for d in data if str(d.get("name", "")).startswith("udb_")]
        items.sort(key=lambda d: str(d.get("name", "")), reverse=True)
        return items

    def _gh_get_blob(self, name: str) -> Optional[bytes]:
        path = f"/repos/{self.gh_repo()}/contents/{name}?ref={self.gh_branch()}"
        ok, data = self._gh("GET", path, timeout=90)
        if not ok or not isinstance(data, dict):
            return None
        try:
            return base64.b64decode(data.get("content", ""))
        except Exception:                                        # noqa: BLE001
            return None

    def _gh_rotate(self) -> int:
        """Purane backups hatao — repo phoolta nahi rahega."""
        removed = 0
        try:
            items = self._gh_list()
            for it in items[self.KEEP_ON_GITHUB:]:
                self._gh("DELETE", f"/repos/{self.gh_repo()}/contents/{it['path']}",
                         {"message": "vault: purana backup hata diya",
                          "sha": it.get("sha"), "branch": self.gh_branch()})
                removed += 1
        except Exception:                                        # noqa: BLE001
            pass
        return removed

    # ------------------------------------------------------------ BACKUP
    async def backup_now(self, reason: str = "manual", also_telegram: bool = True) -> dict:
        """Turant backup. Kabhi exception nahi — dict return karta hai."""
        with self._lock:
            stamp = self._stamp()
            result = {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                      "reason": reason, "github": "", "telegram": "", "bytes": 0}
            if not self.enabled():
                result["why"] = "vault band hai (VAULT_ENABLED=off)"
                return result
            blob = self.dump_encrypted()
            if not blob:
                self.stats["failures"] += 1
                result["why"] = "DB file nahi mili ya dump fail"
                self.last_backup = result
                return result
            result["bytes"] = len(blob)
            self.stats["last_bytes"] = len(blob)

            # safety: backup se pehle premium count note karo (record ke liye)
            try:
                result["premium"] = premium_floor_report(db_path())
            except Exception:                                    # noqa: BLE001
                result["premium"] = {}

            # ---- (a) GITHUB (offsite, encrypted)
            if self.gh_ready():
                # branch ensure — pehli baar khud ban jayega (Render deploy
                # trigger na ho, isliye alag branch me)
                try:
                    if not self._gh_ensure_branch():
                        log.warning("vault branch ready nahi — GitHub backup skip")
                        self.gh_ready = lambda: False          # type: ignore[method-assign]
                except Exception as _be:                        # noqa: BLE001
                    log.debug("branch ensure skip: %s", str(_be)[:90])
                name = f"{self.BACKUP_PREFIX}{stamp}.enc"
                ok, resp = self._gh_put(name, blob)
                if ok:
                    result["github"] = name
                    self.last_backup.update({"ok": True, "at": result["at"],
                                             "where": f"github:{name}", "bytes": len(blob),
                                             "why": ""})
                    self._gh_cache[name] = time.time()
                    try:
                        removed = self._gh_rotate()
                        result["rotated"] = removed
                    except Exception:                            # noqa: BLE001
                        pass
                else:
                    result["github_err"] = str(resp)[:200]
                    log.warning("GitHub backup fail: %s", str(resp)[:160])
                    # v107: 401 = token mara/revoke hua — user ko log me hi
                    # poora ilaaj likha mil jaye (Render env step-by-step).
                    if "401" in str(resp) or "Bad credentials" in str(resp):
                        result["github_err"] = (
                            "GitHub token expire/revoke ho chuka hai (HTTP 401). "
                            "ILAAJ: Render dashboard → ye service → Environment → "
                            "VAULT_GITHUB_TOKEN (ya GITHUB_TOKEN) me NAYA personal "
                            "access token daalo (repo scope) → Save → service "
                            "restart karo. Tab tak Telegram backup chalta rahega.")
                        log.warning("🔑 v107: GitHub token 401 — Render env me "
                                    "VAULT_GITHUB_TOKEN/GITHUB_TOKEN update karo "
                                    "(naya PAT, repo scope), phir restart")
            else:
                result["github"] = "off (VAULT_GITHUB_REPO / token set nahi)"

            # ---- (b) TELEGRAM (aapko dikh jayega)
            if also_telegram and self.telegram_ready():
                try:
                    cap = (f"🛡️ <b>VAULT BACKUP</b>\n"
                           f"🕒 {datetime.now().strftime('%d-%m-%Y %H:%M')}\n"
                           f"📦 {result['bytes']/1024:.1f} KB (encrypted)\n"
                           f"👑 VIP: {result.get('premium', {}).get('total_premium', '?')}\n"
                           f"💡 <i>Ise delete na karein — data recovery ka backup hai.</i>")
                    bio = io.BytesIO(blob)
                    bio.name = f"udb_{stamp}.enc"
                    sent = await self.bot.send_document(
                        chat_id=self.backup_chat, document=bio, filename=bio.name,
                        caption=cap, parse_mode="HTML", disable_notification=True)
                    result["telegram"] = "sent"
                    try:
                        self._save_sidecar(int(sent.message_id))
                    except Exception:                            # noqa: BLE001
                        pass
                    result["ok"] = True
                except Exception as e:                           # noqa: BLE001
                    result["telegram"] = f"fail: {type(e).__name__}"
                    log.warning("Telegram backup fail: %s", str(e)[:140])
            elif also_telegram:
                result["telegram"] = "off"

            if result["github"] and not result["github"].startswith("off"):
                result["ok"] = True
            if not result["ok"]:
                self.stats["failures"] += 1
                result["why"] = (result.get("github_err") or result.get("telegram")
                                 or "koi storage available nahi")
            else:
                self.stats["backups"] += 1
                self.last_backup.update({"ok": True, "at": result["at"],
                                         "where": result["github"] if result["github"] and
                                         not result["github"].startswith("off") else "telegram",
                                         "bytes": len(blob), "why": ""})
                # local copy bhi rakho (turant restore ke liye)
                self._save_local(blob, stamp)
            self.last_backup = result
            return result

    # ------------------------------------------------------------ local copies
    def local_dir(self) -> str:
        d = os.environ.get("VAULT_LOCAL_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(db_path())) or ".", "vault_local")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:                                        # noqa: BLE001
            pass
        return d

    def sidecar_path(self) -> str:
        return os.path.join(self.local_dir(), "vault_meta.json")

    def _save_sidecar(self, msg_id: int) -> None:
        try:
            data = {}
            sp = self.sidecar_path()
            if os.path.exists(sp):
                with open(sp) as f:
                    data = json.load(f)
            data.setdefault("tg_ids", [])
            data["tg_ids"] = ([msg_id] + [i for i in data["tg_ids"] if i != msg_id])[:5]
            data["last_backup"] = datetime.now().isoformat(timespec="seconds")
            with open(sp, "w") as f:
                json.dump(data, f)
        except Exception:                                        # noqa: BLE001
            pass

    def _load_sidecar(self) -> dict:
        try:
            with open(self.sidecar_path()) as f:
                return json.load(f)
        except Exception:                                        # noqa: BLE001
            return {}

    def _save_local(self, blob: bytes, stamp: str) -> None:
        try:
            d = self.local_dir()
            with open(os.path.join(d, f"udb_{stamp}.enc"), "wb") as f:
                f.write(blob)
            files = sorted([x for x in os.listdir(d) if x.startswith("udb_")], reverse=True)
            for old in files[self.KEEP_LOCAL:]:
                try:
                    os.remove(os.path.join(d, old))
                except Exception:                                # noqa: BLE001
                    pass
        except Exception:                                        # noqa: BLE001
            pass

    # ------------------------------------------------------------ RESTORE
    def _best_candidates(self) -> list:
        """Sabse accha backup pehle — GitHub, phir local, phir Telegram."""
        cands: list = []
        if self.gh_ready():
            for i, meta in enumerate(self._gh_list()):
                cands.append({"src": "github", "name": meta["name"],
                              "path": meta.get("path", ""), "rank": i})
        try:
            d = self.local_dir()
            local = sorted([x for x in os.listdir(d) if x.startswith("udb_")], reverse=True)
            for i, n in enumerate(local):
                cands.append({"src": "local", "name": n,
                              "path": os.path.join(d, n), "rank": i})
        except Exception:                                        # noqa: BLE001
            pass
        sc = self._load_sidecar()
        for i, mid in enumerate(sc.get("tg_ids", []) or []):
            cands.append({"src": "telegram", "name": f"tg_{mid}", "msg_id": mid, "rank": i})
        return cands

    def _fetch(self, cand: dict) -> Optional[bytes]:
        """Candidate se encrypted bytes lao."""
        try:
            if cand["src"] == "github":
                # ⚠️ poora PATH chahiye ("vault/udb_xxx.enc"), sirf filename
                # nahi — warna GitHub 404 deta hai aur restore chup-chaap
                # fail ho jata tha (yahi bug tha).
                return self._gh_get_blob(cand.get("path") or cand["name"])
            if cand["src"] == "local":
                with open(cand["path"], "rb") as f:
                    return f.read()
            if cand["src"] == "telegram":
                # v66: bina loop banaye, main loop par bhejo
                f = run_coro_blocking(self.bot.get_file(cand["msg_id"]),
                                      timeout=60)
                if f is None:
                    return None
                buf = io.BytesIO()
                f.download_to_memory(buf)
                return buf.getvalue()
        except Exception as e:                                   # noqa: BLE001
            log.debug("candidate fetch fail (%s): %s", cand["src"], str(e)[:90])
        return None

    def _plain_to_rows(self, plain: bytes) -> Optional[dict]:
        """Backup ke SQL dump ko ek temp DB me chalao aur rows padho."""
        try:
            tmp_fd, tmp_path = __import__("tempfile").mkstemp(suffix=".db",
                                                             prefix="ud_restore_")
            os.close(tmp_fd)
            try:
                con = sqlite3.connect(tmp_path)
                text = plain.decode("utf-8", "replace")
                if text.lstrip().startswith("BEGIN TRANSACTION"):
                    con.executescript(text)
                else:
                    con.close()
                    with open(tmp_path, "wb") as f:
                        f.write(plain)
                    con = sqlite3.connect(tmp_path)
                con.commit()
                out = {}
                for t in _table_names(con):
                    try:
                        cur = con.execute(f"SELECT * FROM {t}")   # noqa: S608
                        cols = [d[0] for d in cur.description] if cur.description else []
                        out[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
                    except Exception:                            # noqa: BLE001
                        continue
                con.close()
                return out
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:                                # noqa: BLE001
                    pass
        except Exception as e:                                   # noqa: BLE001
            log.warning("backup parse fail: %s", str(e)[:140])
            return None

    def apply_rows(self, merged: dict) -> bool:
        """Merged rows ko ASLI DB me likho — premium-safe tareeke se.

        Likhne se pehle purani value se kam nahi hoga (extra safety, merge ke
        baad bhi).
        """
        p = db_path()

        # 🐞 v111 FIX — "apply_rows fail: no such table: users"
        #
        # Render ka free plan har deploy/restart par BILKUL KHAALI filesystem
        # deta hai. Boot par ye kram chalta tha:
        #     1. vault restore chala  -> yahan raw sqlite se DB khola
        #     2. DB file to ban gayi par usme koi TABLE hi nahi thi
        #     3. UPDATE users ...     -> "no such table: users" -> restore FAIL
        #     4. uske BAAD pehla user aaya, tab database.py ne tables banaye
        #
        # Natija: backup to ban raha tha, par WAPAS KABHI RESTORE NAHI HOTA
        # THA. Har restart par saare users / premium / credits gayab.
        # Yahi aapke Render logs me dikh raha tha.
        #
        # Ilaaj: likhne se PEHLE database.py se schema bana lo. Ye lazy import
        # hai (circular import se bachne ke liye) aur `CREATE TABLE IF NOT
        # EXISTS` hai, isliye DB pehle se bhara ho to kuch nahi bigadta.
        try:
            import database as _db
            _db.db()                      # schema pakka bana do
        except Exception as e:                                   # noqa: BLE001
            log.warning("schema ensure skip: %s", str(e)[:120])

        try:
            con = connect(p)
        except Exception:                                        # noqa: BLE001
            return False
        try:
            con.execute("BEGIN IMMEDIATE")
            # users: per-row UPDATE, aur premium ko kabhi ghatt na karo
            cur = con.cursor()
            for r in merged.get("users", []):
                uid = _int(r.get("user_id"))
                if not uid:
                    continue
                cur.execute("SELECT premium_until, credits, referrals, banned FROM users "
                            "WHERE user_id=?", (uid,))
                ex = cur.fetchone()
                if ex:
                    r = dict(r)
                    r["premium_until"] = premium_max(ex[0], r.get("premium_until"))
                    r["credits"] = max(_int(ex[1]), _int(r.get("credits")))
                    r["referrals"] = max(_int(ex[2]), _int(r.get("referrals")))
                    r["banned"] = max(_int(ex[3]), _int(r.get("banned")))
                    sets = ", ".join(f"{c}=?" for c in r.keys() if c != "user_id")
                    vals = [r[c] for c in r.keys() if c != "user_id"] + [uid]
                    try:
                        cur.execute(f"UPDATE users SET {sets} WHERE user_id=?", vals)  # noqa: S608
                    except Exception:                            # noqa: BLE001
                        # koi column naya/purana ho — sirf safe columns likho
                        cur.execute(
                            "UPDATE users SET premium_until=?, credits=?, referrals=?, "
                            "banned=?, referred_by=? WHERE user_id=?",
                            (r.get("premium_until", ""), _int(r.get("credits")),
                             _int(r.get("referrals")), _int(r.get("banned")),
                             _int(r.get("referred_by")), uid))
                else:
                    cols = [c for c in r.keys()]
                    ph = ", ".join("?" for _ in cols)
                    try:
                        cur.execute(f"INSERT OR IGNORE INTO users ({', '.join(cols)}) "
                                    f"VALUES ({ph})", [r[c] for c in cols])  # noqa: S608
                    except Exception:                            # noqa: BLE001
                        pass
            # baaki tables: sirf MISSING rows daalo (kuch delete nahi)
            for t in ("payments", "vip_grants"):
                for r in merged.get(t, []):
                    if not _int(r.get("id")):
                        continue
                    cols = list(r.keys())
                    ph = ", ".join("?" for _ in cols)
                    try:
                        cur.execute(f"INSERT OR IGNORE INTO {t} ({', '.join(cols)}) "
                                    f"VALUES ({ph})", [r[c] for c in cols])  # noqa: S608
                    except Exception:                            # noqa: BLE001
                        continue
            for r in merged.get("bot_meta", []):
                try:
                    cur.execute("INSERT INTO bot_meta(key,value) VALUES(?,?) "
                                "ON CONFLICT(key) DO NOTHING",
                                (r.get("key"), r.get("value")))
                except Exception:                                # noqa: BLE001
                    continue
            for r in merged.get("cloner_configs", []):
                uid = _int(r.get("user_id"))
                if not uid:
                    continue
                try:
                    cur.execute("SELECT 1 FROM cloner_configs WHERE user_id=?", (uid,))
                    if not cur.fetchone():
                        cols = list(r.keys())
                        ph = ", ".join("?" for _ in cols)
                        cur.execute(f"INSERT OR IGNORE INTO cloner_configs ({', '.join(cols)}) "
                                    f"VALUES ({ph})", [r[c] for c in cols])  # noqa: S608
                except Exception:                                # noqa: BLE001
                    continue
            con.commit()
            return True
        except Exception as e:                                   # noqa: BLE001
            log.error("apply_rows fail: %s", str(e)[:160])
            try:
                con.rollback()
            except Exception:                                    # noqa: BLE001
                pass
            return False
        finally:
            try:
                con.close()
            except Exception:                                    # noqa: BLE001
                pass

    def restore_now(self, reason: str = "manual", max_try: int = 4) -> dict:
        """🔒 PREMIUM-FLOOR PROTECTED RESTORE.

        Sabse accha backup uthao -> merge karo -> likhne se pehle CHECK karo ki
        premium users kam nahi hue. Kam hue to ABORT (aapka data bacha rahega).
        """
        with self._lock:
            out = {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                   "reason": reason, "tried": [], "why": "", "report": {}}
            if not self.enabled():
                out["why"] = "vault band hai"
                return out

            before = premium_floor_report(db_path())
            out["premium_before"] = before
            local_rows = snapshot_rows(db_path())
            local_users = local_rows.get("users", [])
            best = None

            for cand in self._best_candidates()[:max_try]:
                out["tried"].append(f"{cand['src']}:{cand['name']}")
                blob = self._fetch(cand)
                if not blob:
                    out.setdefault("skipped", []).append(f"{cand['name']}: fetch fail")
                    log.warning("restore: %s fetch nahi hua (%s)", cand["name"], cand["src"])
                    continue
                plain = decrypt_blob(blob, self.secret)
                if not plain:
                    out.setdefault("skipped", []).append(f"{cand['name']}: decrypt fail")
                    continue
                rows = self._plain_to_rows(plain)
                if not rows or not rows.get("users"):
                    out.setdefault("skipped", []).append(f"{cand['name']}: parse fail")
                    continue
                merged, rep = merge_rows(local_rows, rows)
                # ---------- 🔒 LAYER 4: PREMIUM FLOOR CHECK
                after = premium_floor_report(merged.get("users", []))
                if after["total_premium"] < before["total_premium"]:
                    out.setdefault("blocked", []).append(
                        {"cand": cand["name"], "would_lose":
                         before["total_premium"] - after["total_premium"]})
                    log.error("🛑 RESTORE BLOCKED (%s) — VIP %s se %s ho jate. "
                              "Aapka purana data safe hai.", cand["name"],
                              before["total_premium"], after["total_premium"])
                    continue
                # credit check bhi
                if sum(_int(u.get("credits")) for u in merged.get("users", [])) < \
                   sum(_int(u.get("credits")) for u in local_users):
                    out.setdefault("blocked", []).append({"cand": cand["name"],
                                                          "would_lose": "credits"})
                    continue
                best = {"cand": cand, "merged": merged, "report": rep, "after": after}
                break

            if not best:
                out["why"] = "koi valid backup nahi mila (ya sab blocked hue)"
                if self.key_source() == "BOT_TOKEN":
                    # sabse common asli wajah: BOT_TOKEN rotate ho gaya (key usse banti thi)
                    out["why"] += (" — ⚠️ vault ki key BOT_TOKEN se banti hai; token rotate "
                                   "hone par purane backups decrypt NAHI honge. Ilaja: Render "
                                   "env me VAULT_KEY set karo (purane token se bana derived "
                                   "key = wapas padh jaoge), ya /vault se naya baseline bana lo.")
                self.stats["failures"] += 1
                self.last_restore = out
                return out

            # ---------- file-level safety copy (kuch galat ho to wapas)
            try:
                safe_copy = db_path() + f".prerestore.{self._stamp()}.bak"
                if os.path.exists(db_path()):
                    shutil.copy2(db_path(), safe_copy)
                    out["safety_copy"] = os.path.basename(safe_copy)
            except Exception:                                    # noqa: BLE001
                pass

            if not self.apply_rows(best["merged"]):
                out["why"] = "DB me likhne me dikkat aayi"
                self.stats["failures"] += 1
                self.last_restore = out
                return out

            # ---------- POST-CHECK: sach me premium kam nahi hua?
            after_real = premium_floor_report(db_path())
            out["premium_after"] = after_real
            if after_real["total_premium"] < before["total_premium"]:
                out["ok"] = False
                out["why"] = (f"POST-CHECK FAIL: VIP {before['total_premium']} -> "
                              f"{after_real['total_premium']}. Safety copy: "
                              f"{out.get('safety_copy', '-')}")
                log.error("🛑 %s", out["why"])
                self.stats["failures"] += 1
                self.last_restore = out
                return out

            out["ok"] = True
            out["source"] = f"{best['cand']['src']}:{best['cand']['name']}"
            out["report"] = best["report"]
            self.stats["restores"] += 1
            self.last_restore = out
            log.info("✅ RESTORE OK (%s) — VIP %s -> %s, users %s",
                     out["source"], before["total_premium"], after_real["total_premium"],
                     out["report"].get("users", {}).get("total", "?"))
            return out

    # ------------------------------------------------------------ background loop
    def start_background(self, bot, owner_id: int, chat_id: Optional[int] = None) -> None:
        """Auto-backup thread chalu karo (har VAULT_BACKUP_MINUTES me)."""
        if not self.enabled():
            return
        self.bot = bot
        self.owner_id = owner_id
        self.backup_chat = chat_id or owner_id or None
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="vault-backup")
        self._thread.start()
        log.info("🛡️ VAULT auto-backup ON — har %s minute | github=%s | telegram=%s",
                 self.interval_minutes(), "on" if self.gh_ready() else "off",
                 "on" if self.telegram_ready() else "off")

    def _loop(self) -> None:
        """Backup loop. Pehla backup 90 second baad (bot boot hone do)."""
        import asyncio
        self._stop.wait(90)
        while not self._stop.is_set():
            try:
                if self.bot and self.enabled():
                    # v66: naya loop MAT banao — main loop par bhejo,
                    # warna "bound to a different event loop" error aata hai
                    run_coro_blocking(self.backup_now(reason="auto"), timeout=300)
            except Exception as e:                               # noqa: BLE001
                log.warning("auto-backup loop error (chalta rahega): %s", str(e)[:130])
            try:
                self._stop.wait(self.interval_minutes() * 60)
            except Exception:                                    # noqa: BLE001
                self._stop.wait(300)

    async def backup_soon(self, reason: str = "event") -> None:
        """Zaroori event (VIP grant / payment approve) par turant backup —
        isliye premium grant kabhi lost na ho."""
        if not self.enabled():
            return
        try:
            await asyncio.sleep(0.2)
            await self.backup_now(reason=reason, also_telegram=False)
        except Exception as e:                                   # noqa: BLE001
            log.debug("backup_soon skip: %s", str(e)[:110])

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ report
    def status_card(self) -> str:
        """Admin panel ke liye vault ka report (HTML)."""
        from html import escape as hesc
        p = db_path()
        try:
            size = os.path.getsize(p) if os.path.exists(p) else 0
        except Exception:                                        # noqa: BLE001
            size = 0
        try:
            floor = premium_floor_report(p)
        except Exception:                                        # noqa: BLE001
            floor = {"lifetime": 0, "active": 0, "total_premium": 0}
        ok = lambda b: "🟢" if b else "⚪"                       # noqa: E731
        lb = self.last_backup or {}
        lr = self.last_restore or {}
        lines = [
            "🛡️ <b>PREMIUM VAULT — STATUS</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🗄️ <b>DB path:</b> <code>{hesc(p)}</code> ({size/1024:.1f} KB)",
            f"🔐 <b>Encryption:</b> 🟢 ON (SHAKE256 + HMAC)",
            f"🐙 <b>GitHub backup:</b> {ok(self.gh_ready())} "
            f"{hesc(self.gh_repo() or '(VAULT_GITHUB_REPO set nahi)')}",
            f"📨 <b>Telegram backup:</b> {ok(self.telegram_ready())}",
            f"⏱️ <b>Auto interval:</b> har {self.interval_minutes()} minute",
            f"📈 <b>Backups:</b> {self.stats['backups']} ✅ | "
            f"<b>Restores:</b> {self.stats['restores']} ✅ | "
            f"<b>Failures:</b> {self.stats['failures']}",
            "",
            f"👑 <b>VIP users (asli ginti):</b> Lifetime {floor['lifetime']} · "
            f"Active {floor['active']} · <b>Total {floor['total_premium']}</b>",
        ]
        if lb:
            lines += ["", f"🧾 <b>Last backup:</b> {hesc(str(lb.get('at', '-')))}",
                      f"   {hesc(str(lb.get('why') or lb.get('where') or '-'))[:160]}"]
        if lr:
            lines += [f"♻️ <b>Last restore:</b> {hesc(str(lr.get('at', '-')))} "
                      f"({'OK' if lr.get('ok') else 'fail'})"]
        lines += ["", "💡 <i>Aapka data 2 jagah backup hota hai. Deploy/restart par "
                  "bhi premium users safe rahenge.</i>"]
        return "\n".join(lines)

    def premium_list_rows(self) -> list:
        """Saare premium users — report file ke liye."""
        snap = snapshot_rows(db_path())
        now = datetime.now()
        out = []
        for u in snap.get("users", []):
            p = u.get("premium_until", "")
            life = is_lifetime(p)
            d = _parse_dt(p)
            if not life and not (d and d > now):
                continue
            out.append({
                "user_id": _int(u.get("user_id")),
                "name": str(u.get("name") or ""),
                "username": str(u.get("username") or ""),
                "premium_until": "LIFETIME" if life else (d.strftime("%d-%m-%Y") if d else "-"),
                "joined": str(u.get("joined_at") or "")[:10],
                "credits": _int(u.get("credits")),
                "referrals": _int(u.get("referrals")),
                "expired": False,
            })
        # purane premium bhi dikhao (expired) — record ke liye
        have = {r["user_id"] for r in out}
        for u in snap.get("users", []):
            uid = _int(u.get("user_id"))
            if uid in have or not u.get("premium_until"):
                continue
            d = _parse_dt(u.get("premium_until"))
            out.append({"user_id": uid, "name": str(u.get("name") or ""),
                        "username": str(u.get("username") or ""),
                        "premium_until": d.strftime("%d-%m-%Y") if d else "-",
                        "joined": str(u.get("joined_at") or "")[:10],
                        "credits": _int(u.get("credits")),
                        "referrals": _int(u.get("referrals")), "expired": True})
        out.sort(key=lambda r: (r["expired"], r["user_id"]))
        return out


# Singleton
vault = Vault()
