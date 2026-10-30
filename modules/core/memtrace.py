# -*- coding: utf-8 -*-
"""
memtrace.py — 🩸 leak ka NAAM lene ka aasaan tareeka (opt-in, kharcha tab tak zero).

8 Oct 2026 ko maapa gaya:
    memory 333 MB → 406 MB, gc_runs=3 (safai 3 baar chali), caches: 0/1792 (khaali),
    aur malloc_trim ne bhi 0 MB lautaye.
Yaani RAM kachre se nahi, **live objects** se bhari hai. Aisa "kaun rakha hai?" ka
jawaab andaaze se nahi milta — tracemalloc se milta hai, par wo hamesha on rakhna
512 MB ke free instance par mehnga padta hai. Isliye:

  MEM_TRACE=on            → chalu (boot ke baad se baseline)
  MEM_TRACE_SECONDS=180   → itne baad APNE AAP band + aakhri report yaad rahegi
  /health par line:  "leak-hunt: +12.4 MB in 174s | top: modules/x.py:123 +6.1 MB …"

Report kabhi crash nahi karta; MEM_TRACE unset ho to is file ka koi kharcha nahi
(tracemalloc start hi nahi hota).
"""
from __future__ import annotations

import os
import time

__all__ = ["maybe_start_from_env", "start", "stop", "enabled", "report", "state"]

_ST = {"on": False, "t0": 0.0, "stop_at": 0.0, "base": None, "base_mb": 0.0,
       "final": "", "runs": 0, "err": ""}


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(str(os.environ.get(name) or default)))
    except Exception:                                        # noqa: BLE001
        return default


def enabled() -> bool:
    return bool(_ST["on"])


def state() -> dict:
    return dict(_ST)


def maybe_start_from_env() -> bool:
    """MEM_TRACE=on ho to start karo. Fail ho to chup-chaap 'off'."""
    v = str(os.environ.get("MEM_TRACE") or "").strip().lower()
    if v in ("", "off", "0", "false", "no", "nahi"):
        return False
    secs = _env_int("MEM_TRACE_SECONDS", 180)
    return start(seconds=max(15, min(secs, 3600)))


def start(seconds: int = 180) -> bool:
    if _ST["on"]:
        return True
    try:
        import tracemalloc                                   # noqa: PLC0415
        tracemalloc.start(1)          # 1 frame = kaafi (naam+line) aur sasta
        _ST["on"] = True
        _ST["t0"] = time.time()
        _ST["stop_at"] = _ST["t0"] + float(max(15, int(seconds)))
        import gc                                              # noqa: PLC0415
        gc.collect()
        _ST["base"] = tracemalloc.take_snapshot()
        _ST["base_mb"] = _rss_mb()
        return True
    except Exception as e:                                     # noqa: BLE001
        _ST["on"] = False
        _ST["err"] = f"{type(e).__name__}: {str(e)[:70]}"
        return False


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except Exception:                                          # noqa: BLE001
        return 0.0


def stop(keep_report: bool = True) -> str:
    """Tracemalloc band karo (overhat khatam). Report yaad rakh leta hai."""
    if not _ST["on"]:
        return _ST["final"]
    try:
        if keep_report:
            _ST["final"] = _diff(limit=6)
    except Exception as e:                                     # noqa: BLE001
        _ST["err"] = f"stop:{type(e).__name__}"
    finally:
        _ST["on"] = False
        try:
            import tracemalloc                                 # noqa: PLC0415
            tracemalloc.stop()
        except Exception:                                      # noqa: BLE001
            pass
    return _ST["final"]


def _diff(limit: int = 6) -> str:
    import tracemalloc                                         # noqa: PLC0415
    snap = tracemalloc.take_snapshot()
    base = _ST.get("base")
    if base is None:
        return "tracemalloc on (baseline nahi mila)"
    stats = snap.compare_to(base, "filename")[:limit]
    rows = []
    for st in stats:
        mb = st.size_diff / (1024 * 1024)
        if abs(mb) < 0.05:
            continue
        top = (st.traceback[0].filename or "?").split("/")[-1]
        ln = getattr(st.traceback[0], "lineno", 0) or 0
        rows.append(f"{top}:{ln} {'+' if mb > 0 else ''}{mb:.1f}MB")
    grew = _rss_mb() - float(_ST.get("base_mb") or 0)
    head = (f"+{grew:.1f} MB RSS in {int(time.time() - _ST['t0'])}s"
            f" (limit {_env_int('MEM_TRACE_SECONDS', 180)}s me auto-off)")
    return ("leak-hunt: " + head + (" | " + " , ".join(rows) if rows else " | koi badi growth nahi"))


def report(limit: int = 6) -> str:
    """Health ke liye ek line. Kabhi exception nahi. Off ho to khaali string."""
    try:
        if _ST["on"]:
            if time.time() >= _ST["stop_at"]:
                out = stop(keep_report=True)
                return out or "leak-hunt: khatam (kuch nahi badha)"
            _ST["runs"] += 1
            return _diff(limit=limit)
        if _ST["final"]:
            return "leak-hunt (last run): " + _ST["final"]
        if _ST["err"]:
            return f"leak-hunt: off ({_ST['err']})"
    except Exception as e:                                     # noqa: BLE001
        return f"leak-hunt: unavailable ({type(e).__name__})"
    return ""
