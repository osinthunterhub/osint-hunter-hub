"""
OSINT Hunter Hub - Database with Credits + Premium + Payments
Same as utility-duniya-bot but optimized for 8 tools
UPI: kumaranil98787@axl
"""

import os
import sqlite3
import threading
import time
import json
from datetime import datetime, date, timedelta

# DB path - Render disk if available
def _get_db_path():
    # Try Render disk
    for p in ["/var/data/botdata.db", "botdata.db", "/tmp/botdata.db"]:
        try:
            d = os.path.dirname(p)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            # Test writable
            if os.path.exists(d) or d == "":
                return p
        except:
            continue
    return "botdata.db"

DB_PATH = _get_db_path()
FREE_CREDITS = int(os.getenv("FREE_CREDITS", "10"))   # v2.5.5: bot-1 jaisa SAME system (10)
TG_CREDITS = int(os.getenv("TG_CREDITS", "3"))        # v2.5.5: TG tool ka ALAG pool (3) — bot-1 jaisa
# v2.7 FREE FOR ALL — 1k users / 3-4 months tak sab tools FREE (premium off).
# Premium wapas chahiye toh Render me FREE_FOR_ALL=0 karke redeploy.
FREE_FOR_ALL = str(os.getenv("FREE_FOR_ALL", "1")).strip().lower() in ("1", "true", "yes", "on")

# Thread-local connection reuse for speed
_threads = threading.local()
_schema_ready = False
_init_lock = threading.Lock()

class _ConProxy:
    __slots__ = ("_con",)
    def __init__(self, con):
        object.__setattr__(self, "_con", con)
    def close(self):
        return None
    def __getattr__(self, item):
        return getattr(object.__getattribute__(self, "_con"), item)
    def __setattr__(self, k, v):
        setattr(object.__getattribute__(self, "_con"), k, v)
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

def db():
    global _schema_ready
    con = getattr(_threads, "con", None)
    if con is not None:
        try:
            con.execute("SELECT 1")
            return _ConProxy(con)
        except:
            try:
                con.close()
            except:
                pass
            _threads.con = None
            _schema_ready = False
    
    # New connection
    new_con = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    new_con.execute("PRAGMA journal_mode=WAL;")
    new_con.execute("PRAGMA synchronous=NORMAL;")
    new_con.execute("PRAGMA busy_timeout=15000;")
    
    need_schema = False
    with _init_lock:
        if not _schema_ready:
            need_schema = True
    
    if need_schema:
        _create_schema(new_con)
        with _init_lock:
            _schema_ready = True
    
    _threads.con = new_con
    return _ConProxy(new_con)

def _create_schema(con):
    cur = con.cursor()
    # Users
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        credits INTEGER DEFAULT 25,
        premium_until TEXT DEFAULT '',
        joined_at TEXT DEFAULT '',
        banned INTEGER DEFAULT 0,
        total_uses INTEGER DEFAULT 0
    )""")
    
    # Payments
    cur.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan_key TEXT DEFAULT '',
        plan_name TEXT DEFAULT '',
        amount INTEGER DEFAULT 0,
        plan_days INTEGER DEFAULT 0,
        utr_ref TEXT DEFAULT '',
        shot_file_id TEXT DEFAULT '',
        shot_unique_id TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT '',
        reviewer INTEGER DEFAULT 0,
        reviewed_at TEXT DEFAULT '',
        admin_chat_id INTEGER DEFAULT 0,
        admin_msg_id INTEGER DEFAULT 0,
        note TEXT DEFAULT ''
    )""")
    
    # Meta
    cur.execute("""CREATE TABLE IF NOT EXISTS bot_meta(
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    )""")
    
    # Premium ledger
    cur.execute("""CREATE TABLE IF NOT EXISTS premium_ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT DEFAULT 'grant',
        days INTEGER DEFAULT 0,
        old_until TEXT DEFAULT '',
        new_until TEXT DEFAULT '',
        by_admin INTEGER DEFAULT 0,
        plan_key TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )""")
    
    # Indexes
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pay_status ON payments(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_user ON premium_ledger(user_id)")
    except:
        pass

    # v2.0 — PERSISTENT LOOKUP CACHE (provider 8-11 sec leta hai;
    # ek baar dhoondha hua jawab ab 30 din INSTANT + vault backup se restart ke baad bhi zinda)
    cur.execute("""CREATE TABLE IF NOT EXISTS lookup_cache(
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT '',
        ts INTEGER DEFAULT 0,
        ttl INTEGER DEFAULT 0,
        hits INTEGER DEFAULT 0
    )""")
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lookup_ts ON lookup_cache(ts)")
    except:
        pass
    
    # Migrate old columns if needed
    try:
        cur.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in cur.fetchall()]
        if "credits" not in cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT {FREE_CREDITS}")
        if "premium_until" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN premium_until TEXT DEFAULT ''")
        if "tg_credits" not in cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN tg_credits INTEGER DEFAULT {TG_CREDITS}")
    except:
        pass
    
    con.commit()

# ========== USER FUNCTIONS ==========

def get_user(uid: int, name: str = "", username: str = "") -> dict:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            u = dict(zip(cols, row))
            # Update name/username if provided
            if name and name != u.get("name"):
                cur.execute("UPDATE users SET name=? WHERE user_id=?", (name[:60], uid))
                con.commit()
            if username and username != u.get("username"):
                cur.execute("UPDATE users SET username=? WHERE user_id=?", (username[:60], uid))
                con.commit()
            con.close()
            return u
        else:
            now = datetime.now().isoformat(timespec="seconds")
            cur.execute("INSERT INTO users(user_id, name, username, credits, tg_credits, joined_at, total_uses) VALUES(?,?,?,?,?,?,0)",
                       (uid, (name or "")[:60], (username or "")[:60], FREE_CREDITS, TG_CREDITS, now))
            con.commit()
            cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            con.close()
            return dict(zip(cols, row)) if row else {"user_id": uid, "credits": FREE_CREDITS, "premium_until": "", "banned": 0}
    except Exception as e:
        print(f"get_user error: {e}")
        return {"user_id": uid, "credits": FREE_CREDITS, "premium_until": "", "banned": 0, "name": name, "username": username}

def get_user_row(uid: int):
    return get_user(uid)

def is_banned(uid: int) -> bool:
    try:
        u = get_user(uid)
        return bool(u.get("banned"))
    except:
        return False

def set_ban(uid: int, banned: int = 1) -> bool:
    try:
        con = db()
        con.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if banned else 0, uid))
        con.commit()
        con.close()
        return True
    except:
        return False

def get_credits(uid: int) -> int:
    try:
        u = get_user(uid)
        return int(u.get("credits", FREE_CREDITS))
    except:
        return FREE_CREDITS

# ========== v2.5.5 — TG CREDITS (bot-1 jaisa SAME system) ==========

def get_tg_credits(uid: int) -> int:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT tg_credits FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        con.close()
        if row is None or row[0] is None:
            return TG_CREDITS
        return int(row[0])
    except Exception:
        return TG_CREDITS

def set_tg_credits(uid: int, n: int) -> int:
    try:
        con = db()
        con.execute("UPDATE users SET tg_credits=? WHERE user_id=?", (max(0, int(n)), uid))
        con.commit()
        con.close()
    except Exception:
        pass
    return get_tg_credits(uid)

def spend_tg_credits(uid: int, n: int = 1) -> int:
    """TG credits kam karta hai (0 se neeche nahi). Premium/admin se nahi kat-te."""
    try:
        n = max(0, int(n))
        if FREE_FOR_ALL:
            return get_tg_credits(uid)   # v2.7: free mode — deduction band
        if is_premium_user(uid):
            return get_tg_credits(uid)
        from config import is_admin
        if is_admin(uid):
            return get_tg_credits(uid)
        con = db()
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET tg_credits = MAX(0, COALESCE(tg_credits, ?) - ?) WHERE user_id=?",
            (TG_CREDITS, n, uid))
        con.commit()
        if cur.rowcount == 0:
            get_user(uid)
            cur.execute(
                "UPDATE users SET tg_credits = MAX(0, COALESCE(tg_credits, ?) - ?) WHERE user_id=?",
                (TG_CREDITS, n, uid))
            con.commit()
        cur.execute("SELECT tg_credits FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        con.close()
        return int(row[0]) if row and row[0] is not None else TG_CREDITS
    except Exception:
        return get_tg_credits(uid)

def apply_credits_config() -> str:
    """v2.5.5 — bot-1 jaisa: Render env FREE_CREDITS/TG_CREDITS badlo + redeploy
    -> SAB users ke credits usi number par reset (bot_meta me last cfg saved)."""
    cfg = f"{FREE_CREDITS}/{TG_CREDITS}"
    try:
        old = meta_get("credits_cfg", "")
        if old == cfg:
            return f"credits cfg {cfg} (no change)"
        con = db()
        cur = con.cursor()
        cur.execute("UPDATE users SET credits=?, tg_credits=?", (FREE_CREDITS, TG_CREDITS))
        con.commit()
        con.close()
        meta_set("credits_cfg", cfg)
        return f"credits RESET to {cfg} (was {old or 'none'})"
    except Exception as e:
        return f"credits cfg skip: {str(e)[:80]}"

def is_premium_user(uid: int) -> bool:
    try:
        u = get_user(uid)
        pu = u.get("premium_until", "")
        if not pu:
            return False
        if str(pu).lower().startswith("life"):
            return True
        try:
            exp = datetime.fromisoformat(str(pu).replace("Z", ""))
            return exp > datetime.now()
        except:
            return False
    except:
        return False

def is_premium(u: dict) -> bool:
    if not u:
        return False
    try:
        pu = u.get("premium_until", "")
        if not pu:
            return False
        if str(pu).lower().startswith("life"):
            return True
        exp = datetime.fromisoformat(str(pu).replace("Z", ""))
        return exp > datetime.now()
    except:
        return False

def premium_expiry(u: dict) -> str:
    try:
        pu = u.get("premium_until", "")
        if not pu:
            return "No premium"
        if str(pu).lower().startswith("life"):
            return "Lifetime ♾️"
        exp = datetime.fromisoformat(str(pu).replace("Z", ""))
        delta = exp - datetime.now()
        if delta.total_seconds() <= 0:
            return "Expired"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"{days}d {hours}h left"
        else:
            return f"{hours}h left"
    except:
        return str(u.get("premium_until", ""))

def grant_premium(uid: int, days: int, by_admin: int = 0, plan_key: str = "", note: str = "") -> str:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT premium_until FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        old_until = row[0] if row and row[0] else ""
        
        # Calculate new expiry
        now = datetime.now()
        if old_until:
            try:
                if str(old_until).lower().startswith("life"):
                    # Already lifetime
                    new_until = old_until
                else:
                    old_exp = datetime.fromisoformat(str(old_until).replace("Z", ""))
                    if old_exp > now:
                        new_until = (old_exp + timedelta(days=days)).isoformat(timespec="seconds")
                    else:
                        new_until = (now + timedelta(days=days)).isoformat(timespec="seconds")
            except:
                new_until = (now + timedelta(days=days)).isoformat(timespec="seconds")
        else:
            new_until = (now + timedelta(days=days)).isoformat(timespec="seconds")
        
        if days >= 3650:  # Lifetime
            new_until = "lifetime"
        
        cur.execute("UPDATE users SET premium_until=? WHERE user_id=?", (new_until, uid))
        # If user doesn't exist, create
        if cur.rowcount == 0:
            cur.execute("INSERT INTO users(user_id, premium_until, credits, joined_at) VALUES(?,?,?,?)",
                       (uid, new_until, FREE_CREDITS, now.isoformat(timespec="seconds")))
        
        # Ledger
        cur.execute("INSERT INTO premium_ledger(user_id, action, days, old_until, new_until, by_admin, plan_key, note, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (uid, "grant", days, old_until, new_until, by_admin, plan_key, note, now.isoformat(timespec="seconds")))
        
        con.commit()
        con.close()
        return new_until
    except Exception as e:
        print(f"grant_premium error: {e}")
        return ""

def revoke_premium(uid: int, by_admin: int = 0, note: str = "") -> bool:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT premium_until FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        old_until = row[0] if row and row[0] else ""
        cur.execute("UPDATE users SET premium_until='' WHERE user_id=?", (uid,))
        cur.execute("INSERT INTO premium_ledger(user_id, action, days, old_until, new_until, by_admin, note, created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (uid, "revoke", 0, old_until, "", by_admin, note, datetime.now().isoformat(timespec="seconds")))
        con.commit()
        con.close()
        return True
    except:
        return False

def add_credits(uid: int, n: int = 25) -> int:
    try:
        con = db()
        cur = con.cursor()
        # Ensure user exists
        get_user(uid)
        cur.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (n, uid))
        cur.execute("SELECT credits FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        new_credits = row[0] if row else FREE_CREDITS + n
        con.commit()
        con.close()
        return int(new_credits)
    except Exception as e:
        print(f"add_credits error: {e}")
        return FREE_CREDITS

def set_credits(uid: int, n: int) -> int:
    try:
        con = db()
        con.execute("UPDATE users SET credits=? WHERE user_id=?", (n, uid))
        con.commit()
        con.close()
        return n
    except:
        return 0

def spend_credits(uid: int, n: int = 1) -> int:
    try:
        if FREE_FOR_ALL:
            return get_credits(uid)   # v2.7: free mode — deduction band
        # Check if premium or admin - don't spend
        if is_premium_user(uid):
            return get_credits(uid)
        # Admin check done outside, but we check here too
        from config import is_admin
        if is_admin(uid):
            return get_credits(uid)
        
        con = db()
        cur = con.cursor()
        cur.execute("SELECT credits FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if not row:
            get_user(uid)
            cur.execute("SELECT credits FROM users WHERE user_id=?", (uid,))
            row = cur.fetchone()
        
        current = int(row[0] if row and row[0] is not None else FREE_CREDITS)
        if current <= 0:
            con.close()
            return 0
        
        new_val = max(0, current - n)
        cur.execute("UPDATE users SET credits=?, total_uses=total_uses+1 WHERE user_id=?", (new_val, uid))
        con.commit()
        con.close()
        return new_val
    except Exception as e:
        print(f"spend_credits error: {e}")
        return 0

def add_use(uid: int):
    try:
        con = db()
        con.execute("UPDATE users SET total_uses=total_uses+1 WHERE user_id=?", (uid,))
        con.commit()
        con.close()
    except:
        pass

# ========== PAYMENT FUNCTIONS ==========

def create_payment(uid: int, plan_key: str, plan_name: str, amount: int, plan_days: int,
                   utr: str = "", shot_file_id: str = "", shot_unique_id: str = "") -> int:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("""INSERT INTO payments (user_id, plan_key, plan_name, amount, plan_days, utr_ref, shot_file_id, shot_unique_id, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (uid, plan_key, plan_name, amount, plan_days, utr, shot_file_id, shot_unique_id, "pending", datetime.now().isoformat(timespec="seconds")))
        pid = cur.lastrowid
        con.commit()
        con.close()
        return int(pid or 0)
    except Exception as e:
        print(f"create_payment error: {e}")
        return 0

def get_payment(pid: int) -> dict:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM payments WHERE id=?", (pid,))
        row = cur.fetchone()
        if not row:
            con.close()
            return {}
        cols = [d[0] for d in cur.description]
        con.close()
        return dict(zip(cols, row))
    except:
        return {}

def set_payment_status(pid: int, status: str, reviewer: int = 0, note: str = "") -> bool:
    try:
        con = db()
        con.execute("UPDATE payments SET status=?, reviewer=?, reviewed_at=?, note=? WHERE id=?",
                    (status, reviewer, datetime.now().isoformat(timespec="seconds"), note, pid))
        con.commit()
        con.close()
        return True
    except:
        return False

def set_payment_admin_msg(pid: int, chat_id: int, msg_id: int):
    try:
        con = db()
        con.execute("UPDATE payments SET admin_chat_id=?, admin_msg_id=? WHERE id=?", (chat_id, msg_id, pid))
        con.commit()
        con.close()
    except:
        pass

def utr_exists(utr: str, exclude_pid: int = 0) -> bool:
    if not utr:
        return False
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT id FROM payments WHERE utr_ref=? AND id!=? AND status!='rejected' LIMIT 1", (utr, exclude_pid))
        row = cur.fetchone()
        con.close()
        return bool(row)
    except:
        return False

def pending_payments_count() -> int:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
        n = cur.fetchone()[0]
        con.close()
        return int(n or 0)
    except:
        return 0

def pending_payments(limit: int = 20) -> list:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        con.close()
        return [dict(zip(cols, r)) for r in rows]
    except:
        return []

def recent_payments(limit: int = 20, status: str = "") -> list:
    try:
        con = db()
        cur = con.cursor()
        if status:
            cur.execute("SELECT * FROM payments WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            cur.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        con.close()
        return [dict(zip(cols, r)) for r in rows]
    except:
        return []

def user_payments(uid: int, limit: int = 10) -> list:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        con.close()
        return [dict(zip(cols, r)) for r in rows]
    except:
        return []

def all_user_ids() -> list:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users")
        rows = cur.fetchall()
        con.close()
        return [r[0] for r in rows]
    except:
        return []

def premium_users() -> list:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM users WHERE premium_until!='' AND premium_until IS NOT NULL")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        con.close()
        users = [dict(zip(cols, r)) for r in rows]
        # Filter actually premium
        result = []
        for u in users:
            if is_premium(u):
                result.append(u)
        return result
    except:
        return []

def recent_users(limit: int = 20) -> list:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        con.close()
        return [dict(zip(cols, r)) for r in rows]
    except:
        return []

def credits_stats() -> dict:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*), SUM(credits), SUM(total_uses) FROM users")
        row = cur.fetchone()
        con.close()
        return {"total_users": row[0] or 0, "total_credits": row[1] or 0, "total_uses": row[2] or 0}
    except:
        return {"total_users": 0, "total_credits": 0, "total_uses": 0}

def payment_stats() -> dict:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT status, COUNT(*), SUM(amount) FROM payments GROUP BY status")
        rows = cur.fetchall()
        con.close()
        stats = {}
        for status, count, amount in rows:
            stats[status] = {"count": count, "amount": amount or 0}
        return stats
    except:
        return {}

def meta_get(key: str, default: str = "") -> str:
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT value FROM bot_meta WHERE key=?", (key,))
        row = cur.fetchone()
        con.close()
        return row[0] if row and row[0] else default
    except:
        return default

def meta_set(key: str, value: str) -> bool:
    try:
        con = db()
        con.execute("INSERT INTO bot_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        con.commit()
        con.close()
        return True
    except:
        return False


# ========== v2.0 PERSISTENT LOOKUP CACHE ==========
# Provider naye number par 8-11 second leta hai. Ek baar dhoondha hua jawab
# DB me 30 din rehta hai -> dobara INSTANT (0 ms). Vault backup se deploy ke
# baad bhi zinda rehta hai. Sab kuch guarded — koi error bot ko na todhe.

def cache_get(key: str):
    try:
        now = int(time.time())
        con = db()
        cur = con.cursor()
        cur.execute("SELECT value, ts, ttl FROM lookup_cache WHERE key=?", (str(key),))
        row = cur.fetchone()
        if not row:
            con.close()
            return None
        val, ts, ttl = row[0], int(row[1] or 0), int(row[2] or 0)
        if ttl > 0 and (now - ts) > ttl:
            con.close()
            return None
        con.close()
        return json.loads(val) if val else None
    except Exception:
        return None


def cache_put(key: str, value, ttl: int = 2592000) -> bool:
    try:
        blob = json.dumps(value, ensure_ascii=False)
        con = db()
        con.execute(
            "INSERT INTO lookup_cache(key,value,ts,ttl,hits) VALUES(?,?,?,?,0) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "ts=excluded.ts, ttl=excluded.ttl",
            (str(key), blob, int(time.time()), int(ttl)))
        con.commit()
        con.close()
        return True
    except Exception:
        return False


def cache_hit_bump(key: str) -> None:
    try:
        con = db()
        con.execute("UPDATE lookup_cache SET hits = hits + 1 WHERE key=?", (str(key),))
        con.commit()
        con.close()
    except Exception:
        pass


def cache_stats() -> dict:
    try:
        con = db()
        cur = con.cursor()
        now = int(time.time())
        cur.execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM lookup_cache")
        row = cur.fetchone() or (0, 0)
        total, hits = int(row[0] or 0), int(row[1] or 0)
        cur.execute("SELECT COUNT(*) FROM lookup_cache WHERE ttl=0 OR (?-ts)<=ttl", (now,))
        live = int((cur.fetchone() or (0,))[0] or 0)
        con.close()
        return {"total": total, "live": live, "hits": hits}
    except Exception:
        return {"total": 0, "live": 0, "hits": 0}


def cache_prune(max_rows: int = 5000) -> int:
    try:
        con = db()
        cur = con.cursor()
        now = int(time.time())
        cur.execute("DELETE FROM lookup_cache WHERE ttl>0 AND (?-ts)>ttl", (now,))
        gone = cur.rowcount or 0
        cur.execute("SELECT COUNT(*) FROM lookup_cache")
        left = int((cur.fetchone() or (0,))[0] or 0)
        if left > max_rows:
            cur.execute("DELETE FROM lookup_cache WHERE key IN ("
                        "  SELECT key FROM lookup_cache ORDER BY ts ASC LIMIT ?)",
                        (left - max_rows,))
            gone += cur.rowcount or 0
        con.commit()
        con.close()
        return int(gone)
    except Exception:
        return 0
