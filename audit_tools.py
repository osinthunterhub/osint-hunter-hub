#!/usr/bin/env python3
"""Bot ke HAR tool ka live audit — latency, success, gateway tool mapping."""
import os, sys, time, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KEY = os.environ["OWNER"]
os.environ["MYNUM_API_URL"] = "https://utility-api-we9m.onrender.com/api"
os.environ["MYNUM_API_KEY"] = KEY
os.environ["FAMINFO_API_URL"] = "https://utility-api-we9m.onrender.com/api"
os.environ["FAMINFO_API_KEY"] = KEY
os.environ["UPIINFO_API_URL"] = "https://utility-api-we9m.onrender.com/api"
os.environ["UPIINFO_API_KEY"] = KEY
os.environ.setdefault("BOT_TOKEN", "123:abc")
os.environ.setdefault("ADMIN_ID", "1")

results = []


def run(name, fn, *a, cold=True):
    t0 = time.time()
    try:
        r = fn(*a)
        ms = int((time.time() - t0) * 1000)
        ok = bool(isinstance(r, dict) and (r.get("ok") or r.get("data") or r.get("records")))
        nf = bool(isinstance(r, dict) and (r.get("not_found") or r.get("notfound")))
        err = "" if ok or nf else str((r or {}).get("error") or "")[:60]
        results.append({"tool": name, "ms": ms, "ok": ok, "notfound": nf, "err": err})
        print(f"  {name:<22} {ms:>7}ms  {'OK' if ok else ('NOTFOUND' if nf else 'FAIL')}  {err}")
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        results.append({"tool": name, "ms": ms, "ok": False, "err": "EXC " + str(e)[:50]})
        print(f"  {name:<22} {ms:>7}ms  EXCEPTION {str(e)[:60]}")


print("\n=== BOT TOOLS — LIVE AUDIT (gateway = utility-api) ===\n")
try:
    from modules.number_info import lookup_number
    run("num/numinfo", lookup_number, "9006640786")
    run("num (2nd=bot cache)", lookup_number, "9006640786")
except Exception:
    traceback.print_exc()

try:
    from modules.family_info import lookup_family
    run("familyinfo/ration", lookup_family, "123412341234")
except Exception as e:
    print("  familyinfo import:", str(e)[:80])

try:
    from modules.telegram_tools import lookup_tg
    run("tginfo/tgnum", lookup_tg, "9492915392")
except Exception as e:
    print("  telegram_tools:", str(e)[:80])

try:
    from modules.gw_tools import lookup_phonebiz, lookup_echallan
    run("phonebiz", lookup_phonebiz, "9876543210")
    run("challan", lookup_echallan, "UP16FB2070")
except Exception as e:
    print("  gw_tools:", str(e)[:80])

try:
    import modules.userapi as uapi
    for kind, term in (("rc", "UP16FB2070"), ("vehicletomobile", "DL8CAF5030"), ("aadhaar2num", "123412341234")):
        run("userapi:" + kind, uapi.lookup, kind, term)
except Exception as e:
    print("  userapi:", str(e)[:100])

try:
    from modules.website_extractor import website_extractor
    run("wextract", website_extractor, "https://example.com")
except Exception as e:
    print("  wextract:", str(e)[:80])

print("\n=== SUMMARY ===")
ok = [r for r in results if r["ok"]]
slow = [r for r in results if r["ms"] > 5000]
print(f"  tools tested: {len(results)} | ok: {len(ok)} | notfound: {len([r for r in results if r.get('notfound')])} | fail: {len([r for r in results if not r['ok'] and not r.get('notfound')])}")
print(f"  slow (>5s): {[(r['tool'], r['ms']) for r in slow]}")
json.dump(results, open("/tmp/bot_audit.json", "w"), indent=1)
