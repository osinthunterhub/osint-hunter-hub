# -*- coding: utf-8 -*-
"""
janitor.py — 🧹 SELF-CLEANING WATCHDOG (v76 FORTRESS)
=====================================================
v76 se pehle bot ke paas thi: memory watchdog + hang watchdog.
Par 3 "chhupe hue crash kaaran" bache hue the — ye file unhe khatam karti hai:

  1. 💾 DISK BHAR JAATA HAI  (Render free = chhoti ephemeral disk)
     Media tools `tempfile.mkdtemp(prefix="udl_")` banate hain. Agar download
     ke beech me koi error aaye to `shutil.rmtree` tak pahunch hi nahi paata
     aur wo folder /tmp me CHHOOT jaata hai. Din bhar me 500 MB kachra ->
     disk full -> ffmpeg likh hi nahi paata -> tool fail -> "bot mar gaya".

     AB: janitor har 10 minute /tmp saaf karta hai:
         • udl_* / qsc_* / ud_* / tmp* folder+file jo X minute purane
         • sirf apne (prefix wale) — kisi aur cheez ko haath nahi lagata

  2. 👻 MAREEB / ATKE HUE PROCESS (FFMPEG · YT-DLP)
     Agar ek download timeout ho jaye to uska ffmpeg process chalta REHTA hai —
     RAM + CPU khaata rehta hai. 20 aise process = Render free 512 MB full
     = OOM KILL. Pehle inhe koi maarta hi nahi tha.

     AB: janitor ffmpeg / yt-dlp / ffprobe process dhoondhta hai jo
         MAX_PROC_AGE minute se zyada purane hain aur unhe maar deta hai.

  3. 🧹 CACHE KABHI PRUNE NAHI HOTA
     bounded.py ke saare cache registry me hain. Janitor har cycle me expire
     entry nikaalta hai (cache ka faayda rahe, RAM na badhe).

  BONUS: disk 90% se upar gaye to TURANT aggressive safai (intezaar nahi).

----------------------------------------------------------------------
USE (bot.py ke main() me ek line):

    from modules.core.janitor import start_janitor
    start_janitor()

  /sys me dikhane ke liye:
    from modules.core.janitor import janitor_stats
----------------------------------------------------------------------
⚠️ YE FILE KABHI CRASH NAHI KARTI — har step apne try/except me hai.
   Safai fail ho to bhi bot chalega (bas safai nahi hogi).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Dict, List

try:
    from modules.core.bounded import prune_all_caches, clear_all_caches
except Exception:                                                # noqa: BLE001
    def prune_all_caches() -> int:
        return 0

    def clear_all_caches() -> int:
        return 0

import logging

log = logging.getLogger("utility-super-bot.janitor")

__all__ = ["start_janitor", "janitor_stats", "sweep_now", "JAN"]

# =====================================================================
#  CONFIG (sab env se badla ja sakta hai — code chhune ki zaroorat nahi)
# =====================================================================
def _env_int(name: str, default: int, lo: int = 0, hi: int = 100000) -> int:
    try:
        v = int(float(str(os.environ.get(name) or default).strip()))
    except Exception:                                            # noqa: BLE001
        v = default
    return max(lo, min(hi, v))


def _env_float(name: str, default: float, lo: float = 0.0,
               hi: float = 100000.0) -> float:
    try:
        v = float(str(os.environ.get(name) or default).strip())
    except Exception:                                            # noqa: BLE001
        v = default
    return max(lo, min(hi, v))


# safai ka cycle
INTERVAL_MIN = _env_int("JANITOR_INTERVAL_MIN", 10, lo=1, hi=720)
# itne minute purani temp cheez delete
TMP_MAX_AGE_MIN = _env_int("JANITOR_TMP_MAX_AGE_MIN", 45, lo=3, hi=100000)
# itne minute purana ffmpeg/yt-dlp process maaro
PROC_MAX_AGE_MIN = _env_int("JANITOR_PROC_MAX_AGE_MIN", 30, lo=3, hi=100000)
# disk itne % se upar -> turant aggressive safai
DISK_ALERT_PCT = _env_float("JANITOR_DISK_ALERT_PCT", 88.0, lo=10, hi=99)
# safai ON/OFF (off karna ho to JANITOR_ENABLED=0)
ENABLED = str(os.environ.get("JANITOR_ENABLED") or "1").strip().lower() not in (
    "0", "off", "no", "false")

# sirf in prefixes wali cheezen chhooenge — baaki system ko nahi
# (udl_ = media downloader, qsc_ = quick, ud_ = bot temp files, udv = tests)
SAFE_PREFIXES = ("udl_", "qsc_", "ud_", "udv", "ud_q_")

# jin process ko maarna hai (purane / atke hue)
PROC_NAMES = ("ffmpeg", "ffprobe", "yt-dlp", "yt-dlp_x86", "yt_dlp")

JAN: Dict[str, Any] = {
    "runs": 0,
    "tmp_dirs": 0,
    "tmp_files": 0,
    "bytes_freed": 0,
    "procs_killed": 0,
    "cache_pruned": 0,
    "cache_cleared": 0,
    "disk_pct": 0.0,
    "last_run": 0.0,
    "last_error": "",
    "started": False,
}


# =====================================================================
#  HELPERS
# =====================================================================
def _tmp_dir() -> str:
    import tempfile
    try:
        return tempfile.gettempdir()
    except Exception:                                            # noqa: BLE001
        return "/tmp"


def _disk_pct(path: str = "/") -> float:
    """Disk kitni bhari hai (0-100). Fail ho to 0.0 (matlab: pata nahi)."""
    try:
        u = shutil.disk_usage(path)
        if u.total <= 0:
            return 0.0
        return round(100.0 * u.used / u.total, 1)
    except Exception:                                            # noqa: BLE001
        return 0.0


def _size_of(path: str) -> int:
    """File/folder ka size (folder ho to recursively). Kabhi crash nahi."""
    total = 0
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:                                # noqa: BLE001
                    continue
    except Exception:                                            # noqa: BLE001
        pass
    return total


def _rm(path: str) -> int:
    """File ya folder hatao. Returns: kitne bytes free hue."""
    try:
        n = _size_of(path)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
        return n
    except Exception:                                            # noqa: BLE001
        return 0


def _safe_name(name: str) -> bool:
    n = str(name or "")
    return any(n.startswith(p) for p in SAFE_PREFIXES)


# =====================================================================
#  1 — TEMP SWEEPER
# =====================================================================
def sweep_tmp(max_age_min: int = 0) -> Dict[str, int]:
    """Purani temp file/folder hatao. Sirf SAFE_PREFIXES wali.

    Returns: {"dirs": n, "files": n, "bytes": n}
    """
    out = {"dirs": 0, "files": 0, "bytes": 0}
    try:
        age = max_age_min or TMP_MAX_AGE_MIN
        cutoff = time.time() - (age * 60.0)
        base = _tmp_dir()
        if not os.path.isdir(base):
            return out
        for name in os.listdir(base):
            if not _safe_name(name):
                continue                      # doosre program ki cheez — chhooenge nahi
            p = os.path.join(base, name)
            try:
                st = os.stat(p)
            except Exception:                                    # noqa: BLE001
                continue
            # abhi bana hai / abhi use ho raha hai -> chhod do
            if st.st_mtime > cutoff:
                continue
            # hatane se PEHLE yaad rakho ye folder tha ya file
            # (rmtree ke baad isdir() hamesha False hota hai -> galat ginati)
            was_dir = os.path.isdir(p)
            freed = _rm(p)
            if freed or not os.path.exists(p):
                out["bytes"] += freed
                if was_dir:
                    out["dirs"] += 1
                else:
                    out["files"] += 1
    except Exception as e:                                       # noqa: BLE001
        JAN["last_error"] = f"tmp: {type(e).__name__}"
    return out


# =====================================================================
#  2 — ORPHAN PROCESS REAPER  (ffmpeg / yt-dlp)
# =====================================================================
def _kill_old_procs(max_age_min: int = 0) -> int:
    """Bahut purane ffmpeg/yt-dlp process maaro (RAM+CPU bachao).

    psutil available ho to usse, warna /proc se. Dono fail -> 0 (koi nuksan nahi).
    """
    killed = 0
    age = max_age_min or PROC_MAX_AGE_MIN
    try:
        import psutil                                            # type: ignore
        now = time.time()
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "create_time", "ppid"]):
            try:
                nm = (p.info.get("name") or "").lower()
                if not any(nm.startswith(x) for x in PROC_NAMES):
                    continue
                if int(p.info.get("pid") or 0) == me:
                    continue
                ct = float(p.info.get("create_time") or now)
                if (now - ct) < age * 60.0:
                    continue
                p.kill()
                killed += 1
            except Exception:                                    # noqa: BLE001
                continue
        return killed
    except Exception:                                            # noqa: BLE001
        pass

    # ---- fallback: /proc (psutil nahi hai to) ----
    try:
        now = time.time()
        me = os.getpid()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            ipid = int(pid)
            if ipid == me:
                continue
            try:
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8",
                          errors="ignore") as fh:
                    nm = fh.read().strip().lower()
            except Exception:                                    # noqa: BLE001
                continue
            if not any(nm.startswith(x) for x in PROC_NAMES):
                continue
            try:
                ct = os.stat(f"/proc/{pid}").st_ctime
            except Exception:                                    # noqa: BLE001
                continue
            if (now - ct) < age * 60.0:
                continue
            try:
                os.kill(ipid, 9)
                killed += 1
            except Exception:                                    # noqa: BLE001
                continue
    except Exception as e:                                       # noqa: BLE001
        JAN["last_error"] = f"proc: {type(e).__name__}"
    return killed


# =====================================================================
#  3 — CACHE PRUNE
# =====================================================================
def prune_caches(aggressive: bool = False) -> Dict[str, int]:
    out = {"pruned": 0, "cleared": 0}
    try:
        if aggressive:
            out["cleared"] = clear_all_caches()
        else:
            out["pruned"] = prune_all_caches()
    except Exception as e:                                       # noqa: BLE001
        JAN["last_error"] = f"cache: {type(e).__name__}"
    return out


# =====================================================================
#  MAIN SWEEP
# =====================================================================
def sweep_now(aggressive: bool = False) -> Dict[str, Any]:
    """Ek poora safai cycle abhi chalao (test + watchdog dono yahi call karte)."""
    res: Dict[str, Any] = {}
    try:
        # 1) disk kitni bhari hai — pehle dekho
        pct = _disk_pct(_tmp_dir())
        JAN["disk_pct"] = pct
        # disk full ke kareeb ho to umar ka niyam dheela kar do (turant saaf karo)
        urgent = aggressive or pct >= DISK_ALERT_PCT
        age = 3 if urgent else TMP_MAX_AGE_MIN

        # 2) temp safai
        t = sweep_tmp(max_age_min=age)
        # 3) purane process
        k = _kill_old_procs(PROC_MAX_AGE_MIN)
        # 4) cache
        c = prune_caches(aggressive=urgent)

        JAN["tmp_dirs"] += t["dirs"]
        JAN["tmp_files"] += t["files"]
        JAN["bytes_freed"] += t["bytes"]
        JAN["procs_killed"] += k
        JAN["cache_pruned"] += c["pruned"]
        JAN["cache_cleared"] += c["cleared"]
        JAN["runs"] += 1
        JAN["last_run"] = time.time()

        if t["dirs"] or t["files"] or k or urgent:
            log.info("🧹 JANITOR: %s folder + %s file hataye (%.1f MB free) · "
                     "%s purane process maare · disk %.0f%%%s",
                     t["dirs"], t["files"], t["bytes"] / 1048576.0, k, pct,
                     " · URGENT" if urgent else "")
        res = {"ok": True, "urgent": urgent, "disk_pct": pct,
               "tmp": t, "killed": k, "cache": c}
    except Exception as e:                                       # noqa: BLE001
        JAN["last_error"] = str(e)[:120]
        res = {"ok": False, "error": JAN["last_error"]}
    return res


# =====================================================================
#  BACKGROUND LOOP
# =====================================================================
def _loop() -> None:
    # boot par 2 minute ruko (startup ke temp files use ho rahe hote hain)
    time.sleep(120)
    while True:
        try:
            sweep_now()
        except Exception:                                        # noqa: BLE001
            pass
        time.sleep(max(60, INTERVAL_MIN * 60))


def start_janitor() -> bool:
    """Janitor thread chalu karo. Bot ke main() se ek baar call hota hai."""
    try:
        if not ENABLED:
            log.info("🧹 Janitor OFF (JANITOR_ENABLED=0)")
            return False
        if JAN["started"]:
            return True
        JAN["started"] = True
        threading.Thread(target=_loop, daemon=True, name="janitor").start()
        log.info("🧹 Janitor ON — har %s min: temp safai (<%s min purani) · "
                 "purane ffmpeg process · cache prune · disk alert %s%%",
                 INTERVAL_MIN, TMP_MAX_AGE_MIN, DISK_ALERT_PCT)
        return True
    except Exception as e:                                       # noqa: BLE001
        log.warning("janitor start skip: %s", str(e)[:100])
        return False


# =====================================================================
#  STATS (/sys card)
# =====================================================================
def janitor_stats() -> Dict[str, Any]:
    """/sys admin ke liye safai ka report."""
    try:
        return {
            "on": bool(JAN["started"]),
            "runs": JAN["runs"],
            "tmp_dirs": JAN["tmp_dirs"],
            "tmp_files": JAN["tmp_files"],
            "mb_freed": round(JAN["bytes_freed"] / 1048576.0, 1),
            "procs_killed": JAN["procs_killed"],
            "cache_pruned": JAN["cache_pruned"],
            "cache_cleared": JAN["cache_cleared"],
            "disk_pct": JAN["disk_pct"],
            "last_error": JAN["last_error"],
        }
    except Exception:                                            # noqa: BLE001
        return {"on": False}


def janitor_block() -> str:
    """/sys card me lagane ke liye HTML text."""
    try:
        s = janitor_stats()
        if not s.get("on"):
            return ""
        from html import escape as _e
        return (
            "🧹 <b>JANITOR (v76 — khud safai):</b>\n"
            f"   🗑️ {s['tmp_dirs']} folder · {s['tmp_files']} file hataye "
            f"(<b>{s['mb_freed']} MB</b> free)\n"
            f"   👻 Atke hue process maare: <b>{s['procs_killed']}</b>\n"
            f"   🪣 Cache: {s['cache_pruned']} expire hataye · "
            f"{s['cache_cleared']} baar poora saaf\n"
            f"   💾 Disk: <b>{s['disk_pct']}%</b> bhari · "
            f"{s['runs']} safai cycle"
        )
    except Exception:                                            # noqa: BLE001
        return ""
