# =====================================================================
#  HIMANSHU API — v2.15.1  (owner: HIMANSHU)
#  - per-tool restriction: label:key:expiry:daily:tools
#    tools = * (all) or num or num+familyinfo+ip etc
# =====================================================================
import json
import os
import re
import secrets
import concurrent.futures
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import upstreams as U
import signed_keys as SK

# v2.24.4 SPEED: numpro/aadhaarpro ka SHARED race-pool (pehle per-request threads leak ho rahe the)
_RACE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("RACE_WORKERS") or 8), thread_name_prefix="race")

PORT = int(os.environ.get("PORT", "8080"))
START = time.time()
HITS = {"total": 0, "ok": 0, "fail": 0, "per_tool": {}}
PINGS = {"count": 0, "last": None}
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
KEYS_FILE = os.path.join(DATA_DIR, "keys.json")
USAGE_FILE = os.path.join(DATA_DIR, "usage.json")
LOCK = threading.Lock()
_KEYUSAGE: dict = {}

def _ensure_data_dir():
    try: os.makedirs(DATA_DIR, exist_ok=True)
    except: pass

def _load_json(path, default):
    try:
        if os.path.exists(path):
            return json.loads(open(path, encoding="utf-8").read())
    except: pass
    return default

def _save_json(path, obj):
    _ensure_data_dir()
    try:
        tmp=path+".tmp"
        open(tmp,"w",encoding="utf-8").write(json.dumps(obj, ensure_ascii=False, indent=2))
        os.replace(tmp, path)
    except: pass

def _parse_allowed_tools(raw):
    """tools string -> set of valid tools. None = sab allowed (sirf "*"/khaali).
    Galat naam likh diye to deny-all (None nahi) — warna typo se poori key khul jaati."""
    if raw is None: return None
    raw=str(raw).strip().lower()
    if raw in ("*","all","any",""): return None
    parts=[p.strip() for p in re.split(r'[+|,;]+', raw) if p.strip()]
    norm=set()
    for p in parts:
        p=U.ALIASES.get(p,p)
        if p in U.TOOLS: norm.add(p)
    return norm

def _load_keys_file():
    arr=_load_json(KEYS_FILE, [])
    if not isinstance(arr, list): return []
    out=[]
    for e in arr:
        if not isinstance(e, dict): continue
        label=str(e.get("label") or "").strip()[:32]
        key=str(e.get("key") or "").strip()[:64]
        exp=str(e.get("exp") or "perm").strip()[:20]
        try: daily=int(e.get("daily") or 100)
        except: daily=100
        allowed_raw=e.get("tools") or e.get("allowed") or ""
        allowed=_parse_allowed_tools(allowed_raw)
        if not label or not key: continue
        out.append({"label":label,"key":key,"exp":exp,"daily":daily,"tools":allowed_raw,
                    "allowed":list(allowed) if allowed else None,
                    "created_at":e.get("created_at") or time.strftime("%Y-%m-%d")})
    return out

def _save_keys_file(arr):
    # arr expected list of dicts with label,key,exp,daily,tools
    clean=[]
    for e in arr:
        clean.append({"label":e["label"],"key":e["key"],"exp":e["exp"],"daily":e["daily"],
                      "tools":e.get("tools") or "",
                      "created_at":e.get("created_at") or time.strftime("%Y-%m-%d")})
    _save_json(KEYS_FILE, clean)

def _load_usage_file():
    raw=_load_json(USAGE_FILE, {})
    if not isinstance(raw, dict): return
    with LOCK:
        _KEYUSAGE.clear()
        for label, mp in raw.items():
            if not isinstance(mp, dict): continue
            for day,cnt in mp.items():
                try: _KEYUSAGE[(str(label),str(day))]=int(cnt)
                except: pass

def _save_usage_file():
    mp={}
    with LOCK: items=list(_KEYUSAGE.items())
    for (label,day),cnt in items:
        mp.setdefault(label,{})[day]=cnt
    _save_json(USAGE_FILE, mp)

def _keys():
    return [k.strip() for k in (os.environ.get("GATEWAY_KEY") or "").split(",") if k.strip()]

def _api_keys():
    out={}
    raw=os.environ.get("API_KEYS") or ""
    for part in raw.split(","):
        if not part.strip(): continue
        # split max 5 parts: label:key:exp:daily:tools
        f=[x.strip() for x in part.split(":")]
        if len(f)<2 or not f[1]: continue
        label, key = f[0] or f[1], f[1]
        exp = f[2] if len(f)>2 and f[2] else "perm"
        try: daily=int(f[3]) if len(f)>3 and f[3] else 100
        except: daily=100
        tools_raw = f[4] if len(f)>4 else ""
        allowed=_parse_allowed_tools(tools_raw)
        out[key]={"label":label,"exp":exp,"daily":daily,"tools":tools_raw,"allowed":allowed,"src":"env"}
    for e in _load_keys_file():
        # file overrides env for same key
        allowed=_parse_allowed_tools(e.get("tools") or "")
        out[e["key"]]={"label":e["label"],"exp":e["exp"],"daily":e["daily"],
                       "tools":e.get("tools") or "","allowed":allowed,"src":"file",
                       "created_at":e.get("created_at")}
    return out

def _key_alive(e: dict):
    if e.get("exp_epoch"):                      # signed key: exact timestamp
        left=(float(e["exp_epoch"])-time.time())/86400
        return (left>0), "" if left>0 else "key_expired", max(0.0,left)
    exp=e["exp"]
    if exp in ("perm","forever",""): return True,"",None
    if exp.endswith("d"):
        try: n=float(exp[:-1])
        except: return False,"bad-expiry",0
        left=(START+n*86400-time.time())/86400
        return (left>0), "" if left>0 else "key_expired", max(0.0,left)
    today=time.strftime("%Y-%m-%d", time.gmtime())
    if exp < today: return False,"key_expired",0
    try: left=(time.mktime(time.strptime(exp,"%Y-%m-%d"))-time.time())/86400
    except: return False,"bad-expiry",0
    return True,"",max(0.0,left)

def _signing_secret_only():
    return SK._secret()

def _owner_cookie_value() -> str:
    """Owner session cookie — GATEWAY_KEY se HMAC, 1 saal valid, auto-invalid
    ho jaata hai jab owner key badle."""
    import hmac as _hm, hashlib as _hl
    msg = ("ud-owner-v1|" + ",".join(_keys())).encode()
    return _hm.new(_signing_secret_only(), msg, _hl.sha256).hexdigest()[:40]

OWNER_COOKIE = "ud_owner"

def _cookie_ok(cookie_header: str) -> bool:
    if not cookie_header:
        return False
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, val = part.split("=", 1)
        if name.strip() == OWNER_COOKIE:
            import hmac as _hm
            return _hm.compare_digest(val.strip(), _owner_cookie_value()) and bool(_keys())
    return False

def _owner_ok(args, headers=None) -> bool:
    """Owner hai ya nahi — ?key=... se YA session cookie se."""
    k = args.get("key") or ""
    if k and k in _keys():
        return True
    if headers is not None:
        return _cookie_ok(headers.get("Cookie") or "")
    return False

def _normalize_allowed(ent: dict):
    """entry ki tools string se allowed set — aliases resolve, galat naam drop."""
    raw = ent.get("tools")
    if raw is None:
        return ent.get("allowed")
    return _parse_allowed_tools(raw)

def _resolve_key(k: str):
    """key -> (is_owner, entry). entry: env/file key ya SIGNED key."""
    if k and k in _keys():
        return True, None
    ent = _api_keys().get(k) if k else None
    if ent is not None:
        ent["allowed"] = _normalize_allowed(ent)
        return False, ent
    if k:
        data = SK.parse(k)
        if data is not None:
            if data.get("_revoked"):
                return False, {"label": str(data.get("label") or "revoked"), "exp": "perm", "daily": 0,
                               "tools": "", "allowed": set(), "src": "signed", "revoked": True,
                               "nonce": data.get("nonce")}
            _ent = SK.to_entry(data)
            _ent["allowed"] = _normalize_allowed(_ent)
            return False, _ent
    return False, None

# ---- per-minute rate limit (key/purpose based, memory only) ----
_RL: dict = {}
_RL_LOCK = threading.Lock()

def _rate_limit(bucket: str, per_min: int):
    """Sliding-window per-minute limiter. (ok, retry_after_sec, used, limit)"""
    if per_min <= 0:
        return True, 0, 0, 0
    now = time.time()
    with _RL_LOCK:
        arr = _RL.setdefault(bucket, [])
        cutoff = now - 60
        arr[:] = [t for t in arr if t > cutoff]
        if len(arr) >= per_min:
            return False, max(1, int(arr[0] + 60 - now) + 1), len(arr), per_min
        arr.append(now)
        return True, 0, len(arr), per_min

def _client_ip(handler) -> str:
    try:
        fwd = handler.headers.get("X-Forwarded-For") or ""
        if fwd:
            return fwd.split(",")[0].strip()
        return handler.client_address[0]
    except Exception:
        return "?"

def _daycount(label: str) -> int:
    with LOCK:
        return _KEYUSAGE.get((label, time.strftime("%Y%m%d", time.gmtime())), 0)

def _inc_usage(label: str):
    day=time.strftime("%Y%m%d", time.gmtime())
    with LOCK:
        _KEYUSAGE[(label,day)]=_KEYUSAGE.get((label,day),0)+1
    threading.Thread(target=_save_usage_file, daemon=True).start()

def _keepawake():
    # SPEED v2.12.2: SELF-ping Render spin-down NAHI rokta (self traffic ignored).
    # EXTERNAL peer = bot ka /health — bot kabhi na soye (Actions ping bhi hai).
    # v2.24.4 FIX: default peer ab LIVE bot URL (purana 'osint-hunter-hub-bot.onrender.com' 404 de raha tha
    # — matlab keep-awake chain TOOTI thi aur dono services free-tier spin-down kha rahe the)
    url=(os.environ.get("KEEP_PEER_URL") or "https://osint-hunter-hub-bot-zm5u.onrender.com/health").strip()
    if os.environ.get("KEEPAWAKE","on")=="off": return
    time.sleep(60)
    while True:
        try: urllib.request.urlopen(url, timeout=100)
        except: pass
        PINGS["count"]+=1
        PINGS["last"]=int(time.time()-START)
        time.sleep(480)

# ========== HTML TEMPLATES (v2.1) ==========
LANDING_HTML = """<!doctype html>
<html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HIMANSHU API — OSINT & Utility Gateway</title>
<meta name="description" content="Apna khud ka API Hub — 13 tools, 1 gateway, full key control.">
<style>
:root{--bg:#080e1e;--card:#111a2e;--bdr:#1e2a44;--txt:#e6eaf2;--mut:#8ea0c2;--acc:#7dd3fc;--acc2:#a78bfa;--ok:#22c55e;--warn:#f59e0b}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,Segoe UI,Roboto,sans-serif;background:radial-gradient(1200px 600px at 20% -10%,#1a2a5a 0%,transparent 60%),radial-gradient(900px 500px at 90% 0%,#2a1a5a 0%,transparent 60%),var(--bg);color:var(--txt);line-height:1.6}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:10;background:rgba(8,14,30,.85);backdrop-filter:blur(12px);border-bottom:1px solid var(--bdr);display:flex;justify-content:space-between;align-items:center;padding:12px 18px}
.logo{font-weight:800;font-size:18px}.logo span{background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
nav{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}nav a{padding:6px 10px;border:1px solid transparent;border-radius:999px}nav a.cta{border-color:var(--bdr);background:var(--card)}
.wrap{max-width:1100px;margin:0 auto;padding:0 18px}
.hero{padding:54px 0 24px;display:grid;grid-template-columns:1.2fr .8fr;gap:22px;align-items:center}
@media(max-width:860px){.hero{grid-template-columns:1fr}}
.h1{font-size:34px;line-height:1.15;margin:0 0 10px;font-weight:900}.h1 em{font-style:normal;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:var(--mut);font-size:15px;margin-bottom:18px}
.btns{display:flex;gap:10px;flex-wrap:wrap}.btn{padding:10px 16px;border-radius:12px;border:1px solid var(--bdr);background:var(--card);color:var(--txt);font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:6px}.btn.pri{background:linear-gradient(90deg,#0ea5e9,#8b5cf6);border-color:transparent;color:white}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}
@media(max-width:600px){.stats{grid-template-columns:1fr}}
.st{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:12px 14px}.st b{font-size:22px;display:block}.st span{color:var(--mut);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:14px;transition:.15s}.card:hover{border-color:#2a3a66;transform:translateY(-1px)}
.card h3{margin:0 0 4px;font-size:14px}.card p{margin:0;color:var(--mut);font-size:12px;line-height:1.5}.card code{font-size:11px;background:#0a1226;border:1px solid var(--bdr);padding:2px 6px;border-radius:6px;color:var(--acc)}
.sec{padding:26px 0;border-top:1px dashed var(--bdr);margin-top:26px}.sec h2{margin:0 0 8px;font-size:20px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}@media(max-width:720px){.steps{grid-template-columns:1fr}}
.step{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:14px}.step b{display:inline-flex;width:26px;height:26px;border-radius:999px;background:#0ea5e9;color:#fff;align-items:center;justify-content:center;margin-right:6px}
.price{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}@media(max-width:800px){.price{grid-template-columns:1fr}}
.pr{background:var(--card);border:1px solid var(--bdr);border-radius:16px;padding:16px}.pr.pop{border-color:var(--acc2);box-shadow:0 0 0 1px rgba(167,139,250,.25) inset}.pr h3{margin:0 0 6px}.pr ul{margin:8px 0;padding-left:18px;color:var(--mut);font-size:13px}
.foot{padding:24px 0;color:var(--mut);font-size:12px;border-top:1px solid var(--bdr);margin-top:30px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:999px;border:1px solid var(--bdr);background:#0a1226;color:var(--mut)}
pre{white-space:pre-wrap;word-break:break-word;background:#0a1226;border:1px solid var(--bdr);border-radius:10px;padding:10px;font-size:12px;overflow:auto}
</style></head><body>
<header><div class="logo">⚡ <span>HIMANSHU</span> API <span class="badge">v__VER__</span></div>
<nav><a href="/">Home</a><a href="/dashboard">Dashboard</a><a href="/api/docs">Docs</a><a href="#pricing">Pricing</a><a class="cta" href="/dashboard">🔑 Key Banao</a></nav></header>
<div class="wrap">
<section class="hero">
<div>
<div class="h1"><em>HIMANSHU API</em> — OSINT & Utility Gateway</div>
<div class="sub">18 tools ek jagah: Number Info, Family Info, PAN, GST, IP, Pincode, IFSC, Vehicle, Tower, Challan, FASTag, Business Info — sab <b>ek hi /api</b> se. Owner unlimited, demo keys me expiry + daily limit + <b>per-tool limit (sirf NUMBER INFO bhi de sakte ho)</b>. v2.1 LIVE.</div>
<div class="btns"><a class="btn pri" href="/dashboard">🚀 Dashboard Kholo</a><a class="btn" href="/api/docs">📚 API Docs</a><a class="btn" href="#how">❓ Kaise Use Kare</a></div>
<div class="stats">
<div class="st"><b>__UP__</b><span>Uptime</span></div>
<div class="st"><b>__HITS__</b><span>Total Hits</span></div>
<div class="st"><b>13</b><span>Tools Live</span></div>
</div>
</div>
<div>
<pre>GET /api?key=OWNER&tool=num&term=9876543210
→ Number Info

GET /api?key=DEMO&tool=familyinfo&term=12-digit
→ Family Info (NO DATA if not found — v126.3 fix)

Per-tool key example:
label: dost_num
key: AbCd1234
exp: 6d
daily: 200
tools: num   ← sirf NUMBER INFO

API_KEYS=dost_num:AbCd1234:6d:200:num
</pre>
<div class="card"><h3>🔒 Owner vs Demo</h3><p>Owner = unlimited. Demo = expiry + daily + tools limit. Dashboard par meter + tester.</p></div>
</div>
</section>
<section class="sec"><h2>🧰 Tools (13)</h2><div class="grid" id="tools"></div></section>
<section class="sec" id="how"><h2>⚡ 3 Step</h2>
<div class="steps">
<div class="step"><b>1</b> <strong>Key Banao</strong><p>Dashboard → label, expiry (6d/perm/date), daily, tools (* = all, num = only number, num+ip = 2 tools)</p></div>
<div class="step"><b>2</b> <strong>API Call</strong><p><code>/api?key=KEY&tool=TOOL&term=VALUE</code></p></div>
<div class="step"><b>3</b> <strong>Track</strong><p>Dashboard → used today, days left, live/band, meter</p></div>
</div>
</section>
<section class="sec" id="pricing"><h2>💳 Plans</h2>
<div class="price">
<div class="pr"><h3>🎁 Demo 6 Din</h3><p class="badge">₹49</p><ul><li>6d expiry</li><li>200/day</li><li>All or selected tools</li></ul><a class="btn" href="/dashboard">Banao</a></div>
<div class="pr pop"><h3>🔥 Permanent</h3><p class="badge">₹299</p><ul><li>perm lifetime</li><li>1000-5000/day</li><li>Per-tool select</li></ul><a class="btn pri" href="/dashboard">Banao</a></div>
<div class="pr"><h3>⚙️ Custom</h3><p class="badge">Apni marzi</p><ul><li>1d/3d/30d/date</li><li>50-10000/day</li><li>num only bhi</li></ul><a class="btn" href="/dashboard">Banao</a></div>
</div>
</section>
<div class="foot"><div>© 2026 <b>HIMANSHU</b> · All rights reserved · <a href="https://t.me/ANONYMOUS_XH" target="_blank">Telegram: @ANONYMOUS_XH</a> · v__VER__ · <a href="/health">/health</a> · <a href="/tools">/tools</a></div><div>__HOST__</div></div>
</div>
<script>
const TOOLS=__TOOLS_JSON__;
document.getElementById('tools').innerHTML=Object.entries(TOOLS).map(([k,v])=>`<div class="card"><h3>${k}</h3><p>${v.desc||v}</p><code>/api?key=KEY&tool=${k}&term=...</code><br><span class="badge">${v.source||''}</span></div>`).join('');
</script>
</body></html>
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — HIMANSHU API</title>
<style>
:root{--bg:#080e1e;--card:#111a2e;--bdr:#1e2a44;--txt:#e6eaf2;--mut:#8ea0c2;--acc:#7dd3fc;--acc2:#a78bfa;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--txt)}
header{position:sticky;top:0;background:rgba(8,14,30,.9);backdrop-filter:blur(10px);border-bottom:1px solid var(--bdr);padding:12px 16px;display:flex;justify-content:space-between;align-items:center}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1;min-width:200px}
input,select,button,textarea{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--bdr);background:#0a1226;color:var(--txt);font-size:13px}
button{cursor:pointer;font-weight:700}button.pri{background:linear-gradient(90deg,#0ea5e9,#8b5cf6);border-color:transparent;color:#fff}
button.sec{background:var(--card)} .mut{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid var(--bdr);text-align:left}th{color:var(--mut);font-size:11px;text-transform:uppercase}
.badge{font-size:10px;padding:2px 8px;border-radius:999px;border:1px solid var(--bdr);background:#0a1226}
.meter{height:6px;background:#0a1226;border-radius:99px;overflow:hidden;margin-top:6px}.meter div{height:100%}
pre{white-space:pre-wrap;word-break:break-word;background:#0a1226;border:1px solid var(--bdr);border-radius:10px;padding:10px;font-size:12px;max-height:360px;overflow:auto}
.login{max-width:420px;margin:60px auto}.hide{display:none}
</style></head><body>
<header><div>⚡ <b>HIMANSHU</b> API — Dashboard <span class="badge">v__VER__</span></div><div><a href="/" style="color:var(--acc)">Home</a> · <a href="/api/docs" style="color:var(--acc)">Docs</a> · <a href="https://t.me/ANONYMOUS_XH" target="_blank" style="color:var(--acc)">@ANONYMOUS_XH</a></div></header>
<div class="wrap">
<div id="loginBox" class="card login">
<h2 style="margin:0 0 8px">🔑 Owner Login</h2><p class="mut">Apni GATEWAY_KEY daalo (Render → Environment → GATEWAY_KEY). Login ke baad ek <b>HttpOnly cookie</b> ban jaati hai — key browser ke JS me kahin save nahi hoti. Demo key se login nahi hoga.</p>
<input id="ownerKey" type="password" placeholder="GATEWAY_KEY yahan daalo (kisi ko mat batao)">
<button class="pri" onclick="doLogin()" style="margin-top:10px">Login → Dashboard</button>
<p class="mut" style="margin-top:10px">Key kahan milegi? REAL zip → RENDER-ENV-GATEWAY-ASLI.txt → GATEWAY_KEY. Ya Render dashboard → utility-api → Environment → GATEWAY_KEY.</p>
<div id="loginMsg" class="mut"></div>
</div>
<div id="dash" class="hide">
<div class="row">
<div class="card"><b id="st_total">0</b><div class="mut">Total Keys</div></div>
<div class="card"><b id="st_live">0</b><div class="mut">Live</div></div>
<div class="card"><b id="st_dead">0</b><div class="mut">Band</div></div>
<div class="card"><b id="st_hits">0</b><div class="mut">Total Hits</div></div>
</div>
<div class="card"><h3>➕ Nayi Key Banao — Examples ke saath</h3>
<p class="mut">Format: label:key:expiry:daily:tools — tools = * (all) ya num (sirf number) ya num+familyinfo+ip</p>
<div class="row">
<div><label class="mut">Label (dost ka naam)</label><input id="f_label" placeholder="dost1"></div>
<div><label class="mut">Key (khali = auto)</label><input id="f_key" placeholder="AbCd1234"></div>
<div><label class="mut">Expiry</label><select id="f_exp"><option value="6d">6 din (demo)</option><option value="3d">3 din</option><option value="1d">1 din</option><option value="30d">30 din</option><option value="perm" selected>perm — lifetime</option><option value="custom">Custom YYYY-MM-DD</option></select><input id="f_exp_custom" class="hide" placeholder="2026-12-31" style="margin-top:6px"></div>
<div><label class="mut">Daily Limit</label><select id="f_daily"><option>50</option><option selected>200</option><option>500</option><option>1000</option><option>5000</option></select></div>
<div><label class="mut">Tools (v2.1 — kisi bhi API ke liye)</label><select id="f_tools">
<option value="*">* — saare 13 tools (all)</option>
<option value="num">num — sirf NUMBER INFO</option>
<option value="familyinfo">familyinfo — sirf FAMILY INFO (Aadhaar)</option>
<option value="gst">gst — sirf GST INFO</option>
<option value="ip">ip — sirf IP INFO</option>
<option value="ifsc">ifsc — sirf IFSC INFO</option>
<option value="pincode">pincode — sirf PINCODE INFO</option>
<option value="vehicle">vehicle — sirf RC/VEHICLE</option>
<option value="tgnum">tgnum — sirf TELEGRAM NUMBER</option>
<option value="challan">challan — sirf E-CHALLAN</option>
<option value="phonebiz">phonebiz — Phone → Business Info</option>
<option value="num+familyinfo">num+familyinfo — 2 tools (Number + Family)</option>
<option value="num+familyinfo+vehicle">num+familyinfo+vehicle — 3 tools</option>
<option value="ip+gst+ifsc+pincode">ip+gst+ifsc+pincode — 4 free tools</option>
<option value="custom">Custom likho (jaise: num+ip+gst)</option>
</select><input id="f_tools_custom" class="hide" placeholder="jaise: familyinfo+ip ya num+gst" style="margin-top:6px"></div>
</div>
<div class="row" style="margin-top:10px"><div><label class="mut">Key type</label>
<select id="f_mode"><option value="signed" selected>♾️ Signed — redeploy/lifetime proof (recommended)</option><option value="stored">🗄️ Stored — file me (redeploy pe ja sakti hai)</option></select></div></div>
<div class="row" style="margin-top:10px"><button class="pri" onclick="createKey()">Create Key</button><button class="sec" onclick="genKey()">🎲 Random Key</button></div>
<div id="createMsg" class="mut" style="margin-top:8px"></div>
<div id="readyBox" class="hide" style="margin-top:12px;background:#0a1226;border:1px solid #1e2a44;border-radius:12px;padding:12px">
<h3 style="margin:0 0 8px">✅ Ready URLs — Dost ko direct bhejo (tumhare example jaisa)</h3>
<p class="mut">Format tumhare diye hue example jaisa: <code>https://__HOST__/api?key=KEY&tool=TOOL&term=</code><br>Neeche har tool ke liye ready URL hai — Copy dabao aur dost ko bhejo.</p>
<div id="readyUrls" style="display:grid;gap:8px"></div>
<div style="margin-top:10px" class="mut"><b>Dost ko bhejne ka message (auto):</b></div>
<pre id="readyMsg" style="margin-top:6px"></pre>
</div>
<div class="mut" style="margin-top:10px">
<b>Examples:</b><br>
• 6 din demo: label=dost6, exp=6d, daily=200, tools=* → <code>dost6:AbCd1234:6d:200:*</code><br>
• Permanent: label=buyer1, exp=perm, daily=1000, tools=* → <code>buyer1:XyZw9876:perm:1000:*</code><br>
• Sirf NUMBER INFO 6 din: label=dost_num, exp=6d, daily=200, tools=num → <code>dost_num:AbCd1234:6d:200:num</code><br>
• Custom 10 din sirf 2 tools: label=custom1, exp=10d, daily=100, tools=num+ip
</div>
</div>
<div class="card"><h3>🔑 Saari Keys</h3><div style="overflow:auto"><table id="tbl"><thead><tr><th>Label</th><th>Key</th><th>Expiry</th><th>Daily</th><th>Tools</th><th>Used</th><th>Status</th><th>Action</th></tr></thead><tbody></tbody></table></div></div>
<div class="row">
<div class="card"><h3>🧪 API Tester</h3>
<div class="row"><div><label class="mut">Tool</label><select id="t_tool"></select></div><div><label class="mut">Term</label><input id="t_term" placeholder="9876543210"></div></div>
<div class="row" style="margin-top:10px"><button class="pri" onclick="testApi()">Test Karo</button><button class="sec" onclick="document.getElementById('t_out').textContent=''">Clear</button></div>
<pre id="t_out">Yahan result ayega...</pre>
</div>
<div class="card"><h3>📊 Usage</h3><pre id="u_out">Loading...</pre></div>
</div>
<div class="card"><h3>❓ Tumne pucha: sirf NUMBER INFO kaise du?</h3><p class="mut">Dashboard me Tools = <code>num</code> chuno. Ya env me <code>dost_num:AbCd1234:6d:200:num</code> likho. Is key se /api?tool=num hi chalega, familyinfo ya pan maangega to 403 tool_not_allowed ayega. Permanent ke liye exp=perm, 6 din ke liye exp=6d.</p><button class="sec" onclick="logout()">Logout</button></div>
</div>
</div>
<script>
const VER="__VER__", HOST="__HOST__";
function getOwner(){return''}                       /* v2.13: key ab localStorage me nahi — HttpOnly cookie */
function logout(){
  fetch('/api/owner/logout',{method:'POST'}).finally(()=>{try{localStorage.removeItem('ud_owner')}catch(e){};location.reload()});
}
function doLogin(){
  const k=document.getElementById('ownerKey').value.trim();
  if(!k){document.getElementById('loginMsg').textContent='Key khali hai';return}
  document.getElementById('loginMsg').textContent='Check kar raha hoon...';
  fetch('/api/owner/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k})})
    .then(r=>r.json().then(j=>({s:r.status,j})))
    .then(({s,j})=>{
      if(s===200&&j.ok){document.getElementById('loginMsg').textContent='✅ Login ho gaya — reload...';location.reload()}
      else{document.getElementById('loginMsg').textContent='❌ Galat owner key ('+s+'): '+(j.msg||'')}
    }).catch(e=>{document.getElementById('loginMsg').textContent='Error: '+e});
}
function initDash(){
  // cookie session check (v2.13 — key browser me store nahi hoti)
  fetch('/api/owner/whoami').then(r=>r.json()).then(j=>{
    if(!j.ok){document.getElementById('loginBox').classList.remove('hide');return}
    document.getElementById('loginBox').classList.add('hide');
    document.getElementById('dash').classList.remove('hide');
    loadKeys(); loadUsage(); loadTools();
  }).catch(()=>{document.getElementById('loginBox').classList.remove('hide')});
  return;
}
function loadTools(){
  fetch('/tools').then(r=>r.json()).then(j=>{
    const sel=document.getElementById('t_tool'); sel.innerHTML='';
    Object.keys(j.tools||{}).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=k;sel.appendChild(o)})
  });
}
function loadKeys(){
  const k=getOwner();
  fetch('/api/keys/list?key='+encodeURIComponent(k)).then(r=>r.json()).then(j=>{
    if(!j.ok && !j.keys){document.getElementById('tbl').querySelector('tbody').innerHTML='<tr><td colspan=8>Auth fail: '+(j.msg||'owner key galat')+'</td></tr>';return}
    const rows=j.keys||[]; document.getElementById('st_total').textContent=rows.length;
    document.getElementById('st_live').textContent=rows.filter(r=>r.alive).length;
    document.getElementById('st_dead').textContent=rows.filter(r=>!r.alive).length;
    document.getElementById('st_hits').textContent=j.hits_total||0;
    const tb=document.getElementById('tbl').querySelector('tbody'); tb.innerHTML='';
    rows.forEach(r=>{
      const bar=Math.min(100, Math.round((r.used_today/r.daily_limit*100)||0));
      const col=bar<70?'var(--ok)':bar<100?'var(--warn)':'var(--bad)';
      const tools=r.tools||'*';
      tr=document.createElement('tr');
      tr.innerHTML=`<td><b>${r.label}</b><br><span class="badge">${r.src||''}</span></td>
      <td><code>${r.key}</code><br><button onclick="copyText('${r.key_full||r.key}')" style="width:auto;padding:2px 8px;font-size:11px">Copy Key</button></td>
      <td>${r.expiry}<br><span class="mut">${r.days_left==null?'♾':r.days_left.toFixed(1)+'d'}</span></td>
      <td>${r.daily_limit}</td>
      <td><code>${tools}</code></td>
      <td>${r.used_today}<div class="meter"><div style="width:${bar}%;background:${col}"></div></div></td>
      <td>${r.alive?'🟢 LIVE':'🔴 '+(r.why||'band')}</td>
      <td><button style="width:auto;padding:4px 8px" onclick="deleteKey('${r.label}')">Delete</button><br><button style="width:auto;padding:4px 8px;margin-top:4px" onclick="buildReadyUrls('${r.key_full||r.key}','${tools}')">URLs</button><br><button style="width:auto;padding:4px 8px;margin-top:4px" onclick="copyLink('${r.key_full||r.key}')">Link</button></td>`;
      tb.appendChild(tr);
    });
  });
}
function loadUsage(){
  const k=getOwner();
  fetch('/api/usage?key='+encodeURIComponent(k)).then(r=>r.json()).then(j=>{
    document.getElementById('u_out').textContent=JSON.stringify(j,null,2).slice(0,4000);
  });
}
function genKey(){
  const chars='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
  let s=''; for(let i=0;i<8;i++) s+=chars[Math.floor(Math.random()*chars.length)];
  document.getElementById('f_key').value=s;
}
function getSampleTerm(tool){
  const samples={num:'9876543210',familyinfo:'401635555849',aadhar:'401635555849',gst:'19BOKPS7056D1ZI',ip:'8.8.8.8',ifsc:'SBIN0000001',pincode:'110001',paknum:'03001234567',vehicle:'BR01AB1234',tgnum:'@username',pakvehicle:'ABC123',tower:'9876543210',challan:'DL8CAB1234',fastag:'RJ14CV0002',phonebiz:'9985814792'};
  return samples[tool]||'VALUE';
}
function buildReadyUrls(key, toolsRaw){
  const host=HOST;
  let tools=[];
  if(!toolsRaw || toolsRaw==='*' || toolsRaw==='all'){tools=['num','familyinfo','aadhar','gst','ip','ifsc','pincode','paknum','vehicle','tgnum','pakvehicle','tower','challan','fastag','phonebiz'];}
  else{tools=toolsRaw.split(/[+|,;]+/).map(t=>t.trim()).filter(Boolean);}
  const box=document.getElementById('readyBox');
  const cont=document.getElementById('readyUrls');
  cont.innerHTML='';
  const urls=[];
  tools.forEach(tool=>{
    const base=`https://${host}/api?key=${key}&tool=${tool}&term=`;
    const example=`https://${host}/api?key=${key}&tool=${tool}&term=${getSampleTerm(tool)}`;
    urls.push({tool, base, example});
    const div=document.createElement('div');
    div.style.cssText='background:#111a2e;border:1px solid #1e2a44;border-radius:10px;padding:8px 10px';
    div.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap"><div><b>${tool}</b><br><code style="font-size:11px;word-break:break-all">${base}</code><br><span class="mut" style="font-size:11px">Example: ${example}</span></div><div style="display:flex;gap:6px"><button onclick="copyText('${base}')" style="width:auto;padding:6px 10px;font-size:12px">Copy URL</button><button onclick="copyText('${example}')" style="width:auto;padding:6px 10px;font-size:12px;background:#0a1226">Copy Example</button></div></div>`;
    cont.appendChild(div);
  });
  // ready message for friend
  const firstKey=urls[0]?urls[0].base:'';
  let msg=`Bhai ye le teri API key:

Key: ${key}
Expiry: ${document.getElementById('f_exp').value}  Daily: ${document.getElementById('f_daily').value}  Tools: ${toolsRaw}
Base URL: https://${host}/api

Ready URLs (term khali hai, value jod ke call karo):
`;
  urls.forEach(u=>{msg+=`${u.tool}: ${u.base}VALUE
`;});
  msg+=`
Examples:
`;
  urls.slice(0,4).forEach(u=>{msg+=`${u.example}
`;});
  msg+=`
Note: term ke baad apni value daalo — jaise num ke liye mobile number.`;
  document.getElementById('readyMsg').textContent=msg;
  box.classList.remove('hide');
  box.scrollIntoView({behavior:'smooth'});
}
function createKey(){
  const k=getOwner(); const label=document.getElementById('f_label').value.trim();
  let exp=document.getElementById('f_exp').value; if(exp==='custom') exp=document.getElementById('f_exp_custom').value.trim();
  const daily=document.getElementById('f_daily').value.trim(); const key=document.getElementById('f_key').value.trim();
  let tools=document.getElementById('f_tools').value; if(tools==='custom') tools=document.getElementById('f_tools_custom').value.trim();
  if(!label){document.getElementById('createMsg').textContent='Label likho';return}
  if(!exp){document.getElementById('createMsg').textContent='Expiry chuno';return}
  fetch('/api/keys/create?key='+encodeURIComponent(k),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,key,exp,daily,tools,signed:(document.getElementById('f_mode')?.value||'signed')==='signed'})}).then(r=>r.json()).then(j=>{
    document.getElementById('createMsg').textContent=j.ok?('✅ Ban gaya: '+j.entry.key+' tools='+ (j.entry.tools||'*')):('❌ '+(j.msg||JSON.stringify(j)));
    if(j.ok){
      loadKeys();
      buildReadyUrls(j.entry.key, j.entry.tools||'*');
    }
  }).catch(e=>{document.getElementById('createMsg').textContent='Error '+e});
}
function deleteKey(label){
  if(!confirm('Delete '+label+' ?')) return;
  const k=getOwner();
  fetch('/api/keys/delete?key='+encodeURIComponent(k)+'&label='+encodeURIComponent(label),{method:'DELETE'}).then(r=>r.json()).then(j=>{
    alert(j.ok?'Deleted':'Fail: '+(j.msg||JSON.stringify(j))); loadKeys();
  });
}
function copyText(t){navigator.clipboard.writeText(t).then(()=>alert('Copied: '+t));}
function copyLink(key){
  // old behavior: copy num link, but now also show all ready urls
  const toolsRaw=prompt('Kaunse tools ke liye link chahiye? * = all, num = only number, ya num+familyinfo etc', '*')||'*';
  buildReadyUrls(key, toolsRaw);
  const link=`https://${HOST}/api?key=${key}&tool=num&term=`;
  copyText(link);
}
function testApi(){
  const k=getOwner(); const tool=document.getElementById('t_tool').value; const term=document.getElementById('t_term').value.trim();
  if(!term){alert('Term likho');return}
  document.getElementById('t_out').textContent='Loading...';
  fetch(`/api?key=${encodeURIComponent(k)}&tool=${encodeURIComponent(tool)}&term=${encodeURIComponent(term)}`).then(r=>r.json()).then(j=>{
    document.getElementById('t_out').textContent=JSON.stringify(j,null,2).slice(0,8000);
  }).catch(e=>{document.getElementById('t_out').textContent='Error '+e});
}
document.getElementById('f_exp').addEventListener('change',e=>{
  document.getElementById('f_exp_custom').classList.toggle('hide', e.target.value!=='custom');
});
document.getElementById('f_tools').addEventListener('change',e=>{
  document.getElementById('f_tools_custom').classList.toggle('hide', e.target.value!=='custom');
});
initDash();
</script>
</body></html>
"""

class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    # v2.18 SECURITY: banner se version/stack leak band (attacker ko fingerprint na mile)
    server_version = "API"
    sys_version = ""
    def log_message(self,fmt,*a): pass

    def _sec_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-site")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    def _send(self,code,obj,headers=None):
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Access-Control-Allow-Origin","*")
        self._sec_headers()
        for hk,hv in (headers or []):
            self.send_header(hk,hv)
        self.end_headers(); self.wfile.write(body)
    def _send_html(self,code,html):
        body=html.encode()
        self.send_response(code)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("X-Robots-Tag","noindex, nofollow")
        self.send_header("Access-Control-Allow-Origin","*")
        self._sec_headers()
        self.end_headers(); self.wfile.write(body)
    def _read_body(self):
        try:
            ln=int(self.headers.get("Content-Length") or 0)
            if ln: return self.rfile.read(ln).decode("utf-8","ignore")
        except: pass
        return ""
    def do_GET(self):
        q=urlparse(self.path); p=q.path.rstrip("/") or "/"; args={k:v[0] for k,v in parse_qs(q.query).items()}
        # ── v2.21: PERSONAL LIFETIME API — shuruu-style simple endpoints ──
        #  /num_info?key=..&query=..  -> andar hi /api?tool=..&term=.. ban jaata hai
        #  (auth, per-tool, daily-limit, rate-limit sab wahi reuse — koi duplicate code nahi)
        _ALIASES = {
            "/num_info": "num", "/aadhar_info": "aadhaar2num", "/aadhaar_info": "aadhaar2num",
            "/family_info": "familyinfo", "/rc_num": "vehicletomobile",
            "/truecaller_info": "tc", "/tg_info": "tgnum", "/vehicle_info": "vehicle",
            "/dl_info": "dl", "/fastag_info": "fastag", "/imei_info": "imei",
            "/challan_info": "challan", "/ip_info": "ip", "/web_extract": "webextract",
            "/phonebiz_info": "phonebiz", "/lpg_info": "lpg",
            "/upi_info": "upi",  # v2.24.6 — LakshitX upi-verify
            "/insta_info": "insta", "/instagram_info": "insta",  # v2.24.7 — native
        }
        if p in _ALIASES:
            args.setdefault("term", args.get("query") or args.get("q") or args.get("number") or "")
            args["tool"] = _ALIASES[p]
            p = "/api"
        host=self.headers.get("Host") or os.environ.get("RENDER_EXTERNAL_URL") or "localhost"
        if p in ("","/"):
            if "json" in args or "application/json" in (self.headers.get("Accept") or ""):
                self._send(200,{"service":"utility-api","version":U.VERSION,"status":"ok","uptime_sec":int(time.time()-START),"tools":len(U.TOOLS),"endpoints":["/health","/tools","/api/docs","/dashboard","/api?key=..&tool=..&term=.."],"usage":HITS})
                return
            tools_json=json.dumps({k:{"desc":v,"source":"native"} for k,v in U.TOOLS.items()})
            html=LANDING_HTML.replace("__VER__",U.VERSION).replace("__HOST__",host).replace("__UP__",f"{int((time.time()-START)//3600)}h {int((time.time()-START)%3600//60)}m").replace("__HITS__",str(HITS["total"])).replace("__TOOLS_JSON__",tools_json)
            self._send_html(200,html); return
        if p=="/dashboard":
            html=DASHBOARD_HTML.replace("__VER__",U.VERSION).replace("__HOST__",host)
            self._send_html(200,html); return
        if p=="/health":
            self._send(200,{"ok":True,"service":"HIMANSHU API","owner":"HIMANSHU","telegram":"@ANONYMOUS_XH","version":U.VERSION,"uptime_sec":int(time.time()-START),"auth":bool(_keys()),"native":list(U.NATIVE),"proxy":[],"hits":HITS,"keepawake":PINGS,"keys_file":os.path.exists(KEYS_FILE),"usage_file":os.path.exists(USAGE_FILE),"signed_keys":True,"sign_key_id":SK.key_id(),"rate_limit_per_min":int(os.environ.get("RATE_LIMIT_PER_MIN") or 120)}); return
        if p=="/tools":
            tools={}
            for k,v in U.TOOLS.items():
                tools[k]={"desc":v,"source":"native" if k in U.NATIVE_HANDLERS else "proxy",
                          "status":"degraded" if k in U.DEGRADED else "live",
                          "note":U.DEGRADED.get(k)}
            self._send(200,{"tools":tools,"degraded":[k for k in U.TOOLS if k in U.DEGRADED]}); return
        if p in ("/site", "/terminal", "/hub"):
            import pathlib as _pl
            _f = _pl.Path(__file__).resolve().parent / "public" / "site.html"
            try:
                self._send_html(200, _f.read_text(encoding="utf-8")); return
            except Exception:
                self._send(500, {"ok": False, "why": "site file missing"}); return
        if p in ("/keystudio", "/studio"):
            import pathlib as _pl
            _f = _pl.Path(__file__).resolve().parent / "public" / "keystudio.html"
            try:
                self._send_html(200, _f.read_text(encoding="utf-8")); return
            except Exception:
                self._send(500, {"ok": False, "why": "keystudio file missing"}); return
        if p in ("/api/docs","/docs"):
            self._send(200,{"service":"HIMANSHU API","owner":"HIMANSHU","telegram":"@ANONYMOUS_XH","version":U.VERSION,"base":f"https://{host}",
                            "auth":"GATEWAY_KEY owner unlimited, API_KEYS demo/bikau expiry+daily:tools",
                            "personal_api":"shuruu-style: /num_info /aadhar_info /family_info /rc_num /truecaller_info /tg_info /vehicle_info /dl_info /fastag_info /imei_info /challan_info /ip_info /web_extract /phonebiz_info — sab ?key=KEY&query=VALUE (lifetime: owner key)",
                            "endpoints":{"/":"Landing HTML ?json=1 JSON","/dashboard":"Dashboard owner-only","/health":"Health","/tools":"Tool list","/api/docs":"Docs","/api?key=KEY&tool=TOOL&term=VALUE":"Main lookup","/api/keys/list?key=OWNER":"List keys","/api/keys/create?key=OWNER POST":"{label,key?,exp,daily,tools}","/api/keys/delete?key=OWNER&label=LABEL DELETE":"Delete","/api/usage?key=OWNER":"Usage","/keys?key=OWNER&ui=1":"Old HTML"},
                            "tools":U.TOOLS,
                            "examples":[f"https://{host}/api?key=OWNER&tool=num&term=9876543210",f"https://{host}/api?key=OWNER&tool=familyinfo&term=12digit",f"https://{host}/api?key=OWNER&tool=ip&term=8.8.8.8"],
                            "key_format":"label:key:expiry:daily:tools  expiry=perm|YYYY-MM-DD|Nd  daily=number  tools=* or num or num+familyinfo",
                            "per_tool_example":"dost_num:AbCd1234:6d:200:num  → sirf NUMBER INFO"})
            return
        if p=="/keys":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner key"}); return
            rows=[]
            for k,e in _api_keys().items():
                alive,why,left=_key_alive(e)
                rows.append({"label":e["label"],"key":k[:4]+"…"+k[-2:],"key_full":k,"expiry":e["exp"],"days_left":round(left,1) if left is not None else None,"daily_limit":e["daily"],"used_today":_daycount(e["label"]),"alive":alive,"why":why or None,"src":e.get("src"),"tools":e.get("tools") or "*"})
            payload={"ok":True,"owner_keys":len(_keys()),"keys":rows,"format":"label:key:expiry:daily:tools (tools=* or num or num+familyinfo)"}
            want_html=("ui" in args or "text/html" in (self.headers.get("Accept") or ""))
            if want_html:
                import html as _h
                cards=[]
                for r in rows:
                    badge="🟢 LIVE" if r["alive"] else f"🔴 BAND ({r['why']})"
                    cards.append(f"<div style='border:1px solid #1e293b;padding:10px;margin:8px;border-radius:10px'><b>{_h.escape(r['label'])}</b> {badge}<br>key={_h.escape(r['key'])} exp={_h.escape(r['expiry'])} tools={_h.escape(r['tools'])} used={r['used_today']}/{r['daily_limit']}</div>")
                html=f"<html><head><meta charset=utf-8><title>Keys</title></head><body style='background:#0b1220;color:#e5e7eb;font-family:system-ui;padding:16px'><h2>Keys ({len(rows)})</h2>{''.join(cards)}<p><a href='/dashboard'>New Dashboard → /dashboard</a></p></body></html>"
                self._send_html(200,html)
            else: self._send(200,payload)
            return
        if p=="/api/keys/list":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner key"}); return
            rows=[]
            for k,e in _api_keys().items():
                alive,why,left=_key_alive(e)
                rows.append({"label":e["label"],"key":k[:4]+"…"+k[-2:],"key_full":k,"expiry":e["exp"],"days_left":round(left,1) if left is not None else None,"daily_limit":e["daily"],"used_today":_daycount(e["label"]),"alive":alive,"why":why or None,"src":e.get("src"),"tools":e.get("tools") or "*"})
            self._send(200,{"ok":True,"keys":rows,"hits_total":HITS["total"]}); return
        if p=="/api/db":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner"}); return
            if args.get("save"):
                n=U._save_db()
                self._send(200,{"ok":True,"saved_records":n,"file":"data/mydb.json"}); return
            self._send(200,{"ok":True,"my_database":U.db_stats(),
                            "how":"Har query ka result apne aap data/mydb.json me jama hota hai (10 min me save). Restart ke baad wapas load. Repeat lookups upstream par jaate hi nahi -> aapka API dheere-dheere khud ka data use karta hai.",
                            "save_now":"GET /api/db?save=1"}); return
        if p=="/api/keys/export-env":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner"}); return
            rows=[]
            for k,e in _api_keys().items():
                rows.append(f"{e['label']}:{k}:perm:{e['daily']}:{e.get('tools') or '*'}")
            env_str=",".join(rows)
            self._send(200,{"ok":True,
                            "how":"Render -> Environment -> API_KEYS me ye poori line paste karo. Iske baad ye saari keys PERMANENT hain (redeploy pe bhi nahi marti, kyunki env disk pe nahi hoti).",
                            "count":len(rows),
                            "api_keys_env":env_str,
                            "note":"exp=perm matlab lifetime. Naye keys dashboard se 'Signed' banaao — unhe env ki zaroorat hi nahi."}); return
        if p=="/api/usage":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner key"}); return
            with LOCK: mp={}
            for (label,day),cnt in _KEYUSAGE.items():
                mp.setdefault(label,{})[day]=cnt
            self._send(200,{"ok":True,"usage":mp,"today":time.strftime("%Y%m%d")}); return
        if p=="/api/stats":
            is_owner=_owner_ok(args,self.headers)
            data={"ok":True,"version":U.VERSION,"uptime_sec":int(time.time()-START),"hits":HITS,"tools":len(U.TOOLS)}
            if is_owner: data["keys_count"]=len(_api_keys()); data["owner"]=True
            self._send(200,data); return
        if p=="/api/status":
            self._send(200,{"ok":True,"service":"HIMANSHU API","owner":"HIMANSHU","telegram":"@ANONYMOUS_XH","version":U.VERSION,
                            "uptime_sec":int(time.time()-START),
                            "tools_total":len(U.TOOLS),
                            "tools_live":len([t for t in U.TOOLS if t not in U.DEGRADED]),
                            "degraded":U.DEGRADED,
                            "cache":U.cache_info(),
                            "xtrace":U.xtrace_state(),
                            "my_database":U.db_stats(),
                            "permanent":U.permanent_tools(),
                            "custom_providers":U.env_providers(),
                            "stats":U.stats_snapshot(),
                            "limits":{"rate_limit_per_min":int(os.environ.get("RATE_LIMIT_PER_MIN") or 120),
                                      "max_concurrent":int(os.environ.get("MAX_CONCURRENT") or 32)}}); return
        if p=="/api/owner/whoami":
            ok=_owner_ok(args,self.headers)
            self._send(200 if ok else 401,{"ok":ok,"owner":ok,"signed_keys":True,"version":U.VERSION}); return
        if p=="/api":
            k=args.get("key") or ""; owner,ent=_resolve_key(k)
            if not owner and ent is None:
                self._send(401,{"ok":False,"why":"auth","msg":"galat ya missing key — dashboard se key banao"}); return
            if ent is not None and ent.get("revoked"):
                self._send(401,{"ok":False,"why":"key_revoked","msg":"ye key revoke kar di gayi hai — owner se nayi lo"}); return
            if ent is not None:
                rl_ok,rl_retry,_ru,rl_lim=_rate_limit("upm:"+str(ent.get("label")),int(os.environ.get("RATE_LIMIT_PER_MIN") or 120))
                if not rl_ok:
                    self._send(429,{"ok":False,"why":"rate_limit","msg":f"bahut tez — {rl_lim}/min limit, {rl_retry}s baad try karo","retry_after":rl_retry},[("Retry-After",str(rl_retry))]); return
                alive,why,_left=_key_alive(ent)
                if not alive:
                    self._send(401,{"ok":False,"why":why,"msg":"ye key expire ho chuki hai"}); return
                # per-tool check v2.1
                allowed=ent.get("allowed")
                tool_tmp=(args.get("tool") or "").strip().lower()
                tool_tmp=U.ALIASES.get(tool_tmp, tool_tmp)
                if allowed is not None and tool_tmp not in allowed:
                    _sc=",".join(sorted(allowed))
                    self._send(403,{"ok":False,"why":"tool_not_allowed",
                                    "msg":(f"ye key sirf {_sc} ke liye hai" if _sc else "ye key kisi tool ke liye set nahi hai — owner se nayi lo"),
                                    "allowed":sorted(list(allowed))}); return
                if _daycount(ent["label"])>=ent["daily"]:
                    self._send(429,{"ok":False,"why":"daily_limit","msg":f"aaj ke {ent['daily']} hits poore ho gaye"}); return
                _inc_usage(ent["label"])
            tool=(args.get("tool") or "").strip().lower(); term=(args.get("term") or "").strip()
            if not term:
                self._send(400,{"ok":False,"why":"validation","msg":"term bhejo — jaise ?tool=num&term=9876543210"}); return
            HITS["total"]+=1
            pt=HITS["per_tool"].setdefault(tool,0)
            res=U.lookup(tool,term)
            HITS["per_tool"][tool]=pt+1
            HITS["ok" if res.get("ok") else "fail"]+=1
            res.setdefault("status", "success" if res.get("ok") else "error")
            self._send(429 if res.get("why")=="rate_limit" else 200, res); return
        if p=="/siteapi":
            # ── HUNTER TERMINAL public lookup proxy (v2.19) ──
            # - asli GATEWAY_KEY yahin hai (browser ko kabhi nahi milti)
            # - tool ALLOWLIST + per-IP rate limit (30/min) — abuse safe
            _sp=_client_ip(self)
            _ok,_retry,_u,_l=_rate_limit("site:"+_sp,30)
            if not _ok:
                self._send(429,{"ok":False,"why":"rate_limit","msg":"Slow down — too many requests."},[("Retry-After",str(_retry))]); return
            tool=(args.get("tool") or "").strip().lower(); term=(args.get("term") or "").strip()
            _t0=time.time()
            def fin(res):
                res=dict(res or {})
                res["ms"]=int((time.time()-_t0)*1000)
                res.setdefault("api","OSINT HUNTER HUB")
                self._send(200,res)
            if not tool or not term:
                fin({"ok":False,"why":"validation","msg":"tool and term required"}); return
            try:
                if tool=="imei":
                    d=re.sub(r"\D","",str(term))
                    if len(d)!=15:
                        fin({"ok":False,"why":"validation","msg":"Need a 15-digit IMEI."}); return
                    # v2.22: gateway imei tool (TAC device database ke saath) — ek hi code-path
                    fin(U.lookup("imei", d)); return

                if tool=="result":
                    fin({"ok":False,"why":"seasonal",
                         "msg":"BSEB result servers are closed right now — this module opens automatically during result season (Feb–Apr). All other modules are live."}); return
                if tool=="ipinfo":
                    ip=_sp if term.lower() in ("me","my") else term.strip()
                    if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$",ip):
                        fin({"ok":False,"why":"validation","msg":"Need a valid IPv4 (8.8.8.8) or type: me"}); return
                    fin(U.lookup("ip",ip)); return
                if tool=="challan2":
                    v=re.sub(r"\s+","",str(term).upper())
                    if not re.match(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$",v):
                        fin({"ok":False,"why":"validation","msg":"Need an RC like UP16FB2070."}); return
                    _c,_b=U.http_get("https://challaninfoapibest.vercel.app/vehicletochallaninfo?vehicle="+urllib.parse.quote(v),45)
                    _j=json.loads(_b) if (_c==200 and _b) else None
                    if not isinstance(_j,dict):
                        fin({"ok":False,"why":"upstream","msg":"Challan portal busy — 10-45s ka live scrape hai, dobara try karo."}); return
                    _nums=_j.get("challan_numbers") or []
                    _dets=[x for x in (_j.get("details") or []) if isinstance(x,dict) and x.get("accusedName")]
                    _recent=[f"{x.get('accusedName','—')} · {x.get('accusedType','—')}"+(f" · #{x.get('challanId')}" if x.get('challanId') else "") for x in _dets[:6]]
                    fin({"ok":True,"source":"challan-portal","data":{"vehicle":v,"total_challans":len(_nums),
                        "challan_numbers":_nums[:25],"recent_details":_recent or None,
                        "status":("challans on record" if _nums else "no challans found")}}); return
                if tool=="dl":
                    _m=re.match(r"^(.+?)[\s/, -]+(\d{2}-\d{2}-\d{4})$",str(term).strip())
                    if not _m:
                        fin({"ok":False,"why":"validation","msg":"Format: DLNUMBER DD-MM-YYYY (e.g. UP25 20210027727 01-01-2001)"}); return
                    _dl=re.sub(r"\s+"," ",_m.group(1).strip().upper()); _dob=_m.group(2)
                    _c,_b=U.http_get("https://driving-licence-info-api-abhigyan.vercel.app/dlinfoapi?dl="+urllib.parse.quote(_dl)+"&dob="+urllib.parse.quote(_dob),30)
                    # v2.24.5 HONESTY: provider paused/down ho to 'notfound' jhooth mat bolo — upstream batao
                    if _c!=200:
                        fin({"ok":False,"why":"upstream","msg":"DL provider abhi paused/down hai — owner naya provider laga dega, thodi der baad try karo."}); return
                    _j=json.loads(_b) if _b else None
                    if not isinstance(_j,dict) or not _j.get("ok"):
                        fin({"ok":False,"why":"notfound","msg":"DL not found for this number + DOB."}); return
                    _j.pop("captcha_attempts",None)
                    fin({"ok":True,"source":"sarathi-portal","data":_j}); return
                if tool=="fastag":
                    v=re.sub(r"\s+","",str(term).upper())
                    if not re.match(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$",v):
                        fin({"ok":False,"why":"validation","msg":"Need an RC like RJ14CV0002."}); return
                    _c,_b=U.http_get("https://abhigyan-fastag-info.vercel.app/vehtofastaginfo?veh="+urllib.parse.quote(v),25)
                    _j=json.loads(_b) if (_c==200 and _b) else None
                    if not isinstance(_j,dict) or not (_j.get("found") or _j.get("tags")):
                        fin({"ok":False,"why":"notfound","msg":"No FASTag records for this vehicle."}); return
                    _tags=[f"{t.get('bank_name','?')} · {t.get('status','?')}"+(f" · issued {t.get('issue_date')}" if t.get('issue_date') else "")+(f" · {t.get('vehicle_class')}" if t.get('vehicle_class') else "") for t in (_j.get("tags") or [])]
                    fin({"ok":True,"source":"fastag-netc","data":{"vehicle":v,"total_tags":_j.get("count",len(_tags)),
                        "active_tags":_j.get("active_count"),"banks":sorted({t.get('bank_name') for t in (_j.get('tags') or []) if t.get('bank_name')}),
                        "tags_detail":_tags[:12]}}); return
                if tool in ("numpro","aadhaarpro"):
                    # v2.24.4 SPEED+LEAK-FIX: pehle har request par 2 naye threads + 0.05s busy-wait
                    # tha (CPU burn + timeout par thread leak). Ab SHARED pool + Future.result(timeout):
                    # event-driven, threads pool me wapas, koi leak nahi.
                    _pairs={"numpro":[("num",15),("aadhaar2num",12)],"aadhaarpro":[("aadhaar",18),("aadhaar2num",12)]}[tool]
                    _futs={tt:_RACE_POOL.submit(U.lookup,tt,term) for tt,_tmo in _pairs}
                    _box={}
                    for tt,_tmo in _pairs:
                        try: _box[tt]=_futs[tt].result(timeout=_tmo)
                        except Exception: _box[tt]=None
                    rich=_box.get(_pairs[0][0]); fast=_box.get(_pairs[1][0])
                    if tool=="numpro":
                        if isinstance(rich,dict) and rich.get("ok"):
                            if isinstance(fast,dict) and fast.get("ok"):
                                rich.setdefault("data",{})["linked_aadhaar_count"]=(fast.get("data") or {}).get("count")
                            fin(rich); return
                        fin(fast if (isinstance(fast,dict) and fast.get("ok")) else {"ok":False,"why":"notfound","msg":"No records found for this number."}); return
                    if isinstance(rich,dict) and rich.get("ok"):
                        _d=rich.setdefault("data",{})
                        _recs=_d.get("records") or []
                        _fix={"fathersName":"father","phoneNumber":"phone","otherNumber":"alt"}
                        _d["records"]=[{_fix.get(k,k):v for k,v in _r.items() if v not in (None,"",[""],"null")} for _r in _recs[:10]]
                        if isinstance(fast,dict) and fast.get("ok"):
                            _d["fast_numbers"]=(fast.get("data") or {}).get("mobile")
                        fin(rich); return
                    fin(fast if (isinstance(fast,dict) and fast.get("ok")) else {"ok":False,"why":"notfound","msg":"No records found for this reference."}); return
                if tool in ("num","tc","tgnum","familyinfo","vehicletomobile","aadhaar2num","phonebiz","webextract","ip"):
                    fin(U.lookup(tool,term)); return
                fin({"ok":False,"why":"unknown_tool","msg":"Unknown module."})
            except Exception:
                fin({"ok":False,"why":"upstream","msg":"Source busy — thodi der baad try karo."})
            return
        self._send(404,{"ok":False,"why":"route","msg":"aisa koi rasta nahi — /api/docs dekho"})
    def do_POST(self):
        q=urlparse(self.path); p=q.path.rstrip("/") or "/"; args={k:v[0] for k,v in parse_qs(q.query).items()}
        body_raw=self._read_body()
        try: body=json.loads(body_raw) if body_raw else {}
        except: body={}
        if p=="/api/owner/login":
            # v2.18 SECURITY: brute-force guard (pehle koi limit nahi thi)
            _ip=_client_ip(self)
            _ok,_retry,_used,_lim=_rate_limit("login:"+_ip,int(os.environ.get("LOGIN_RPM") or 5))
            if not _ok:
                self._send(429,{"ok":False,"why":"too_many_attempts",
                                "msg":f"bahut zyada login attempts — {_retry}s baad try karo"},
                           [("Retry-After",str(_retry))]); return
            k=str(body.get("key") or args.get("key") or "").strip()
            if not k or k not in _keys():
                self._send(401,{"ok":False,"why":"auth","msg":"ye owner (GATEWAY_KEY) nahi hai"}); return
            age=int(os.environ.get("OWNER_SESSION_DAYS") or 365)*86400
            cookie=f"{OWNER_COOKIE}={_owner_cookie_value()}; Path=/; Max-Age={age}; HttpOnly; Secure; SameSite=Lax"
            self._send(200,{"ok":True,"msg":"owner login ho gaya — ab key ki zaroorat nahi","expires_days":age//86400},[("Set-Cookie",cookie)]); return
        if p=="/api/owner/logout":
            self._send(200,{"ok":True,"msg":"logout"},[("Set-Cookie",f"{OWNER_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")]); return
        if p=="/api/keys/create":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner key"}); return
            label=str(body.get("label") or "").strip()[:32]
            key=str(body.get("key") or "").strip()[:64]
            exp=str(body.get("exp") or "perm").strip()[:20]
            daily_raw=body.get("daily") or 200
            try: daily=int(daily_raw)
            except: daily=200
            tools_raw=str(body.get("tools") or body.get("allowed") or "*").strip()[:100]
            if not label:
                self._send(400,{"ok":False,"why":"validation","msg":"label likho"}); return
            def _ready_urls(key_val, tools_field):
                host=(self.headers.get("Host") or os.environ.get("RENDER_EXTERNAL_URL") or "localhost")
                scheme="http" if (host.startswith("localhost") or host.startswith("127.") or host.startswith("0.0.0.0")) else "https"
                base=f"{scheme}://{host}/api"
                picked=[t for t in (tools_field or "*").replace("|",",").replace(";",",").replace("+",",").split(",") if t.strip()]
                if not picked or picked==["*"]:
                    picked=list(U.TOOLS.keys())[:6]
                out={}
                for t in picked[:8]:
                    t=t.strip()
                    if t in U.TOOLS:
                        ex=U._EXAMPLES.get(t) if hasattr(U,"_EXAMPLES") else ""
                        out[t]=f"{base}?key={key_val}&tool={t}" + (f"&term={ex}" if ex else "&term=<value>")
                return out
            mode=str(body.get("mode") or "").strip().lower()
            if mode in ("signed","stateless","hmac") or body.get("signed") is True:
                sk=SK.make(label,exp,daily,tools_raw)
                inf=SK.info(sk)
                self._send(200,{"ok":True,"mode":"signed",
                                "msg":"Signed key ban gayi — kabhi expire nahi hogi, redeploy pe bhi zinda rahegi",
                                "entry":{"label":label,"key":sk,"exp":exp,"daily":daily,"tools":tools_raw},
                                "ready_urls":_ready_urls(sk,tools_raw),
                                "scope":"ye key SIRF in tools par chalegi: "+tools_raw,
                                "info":inf,
                                "revoke_hint":"Render env REVOKED_KEYS me daalo: "+str(inf.get("nonce"))}); return
            if not key:
                key=secrets.token_urlsafe(6).replace("-","").replace("_","")[:8]
            arr=_load_keys_file()
            found=False
            for e in arr:
                if e["label"]==label:
                    e["key"]=key; e["exp"]=exp; e["daily"]=daily; e["tools"]=tools_raw; found=True; break
            if not found:
                arr.append({"label":label,"key":key,"exp":exp,"daily":daily,"tools":tools_raw,"created_at":time.strftime("%Y-%m-%d")})
            _save_keys_file(arr)
            self._send(200,{"ok":True,"msg":"key ban gayi","entry":{"label":label,"key":key,"exp":exp,"daily":daily,"tools":tools_raw}})
            return
        if p=="/api":
            k=args.get("key") or body.get("key") or ""; owner,ent=_resolve_key(k)
            if not owner and ent is None:
                self._send(401,{"ok":False,"why":"auth","msg":"galat ya missing key"}); return
            if ent is not None and ent.get("revoked"):
                self._send(401,{"ok":False,"why":"key_revoked","msg":"ye key revoke kar di gayi hai"}); return
            if ent is not None:
                rl_ok,rl_retry,_ru,rl_lim=_rate_limit("upm:"+str(ent.get("label")),int(os.environ.get("RATE_LIMIT_PER_MIN") or 120))
                if not rl_ok:
                    self._send(429,{"ok":False,"why":"rate_limit","msg":f"bahut tez — {rl_lim}/min limit, {rl_retry}s baad try karo","retry_after":rl_retry},[("Retry-After",str(rl_retry))]); return
                alive,why,_left=_key_alive(ent)
                if not alive:
                    self._send(401,{"ok":False,"why":why,"msg":"expire ho chuki"}); return
                tool_tmp=(args.get("tool") or body.get("tool") or "").strip().lower()
                tool_tmp=U.ALIASES.get(tool_tmp, tool_tmp)
                allowed=ent.get("allowed")
                if allowed is not None and tool_tmp and tool_tmp not in allowed:
                    _sc2=",".join(sorted(allowed))
                    self._send(403,{"ok":False,"why":"tool_not_allowed",
                                    "msg":(f"ye key sirf {_sc2} ke liye hai" if _sc2 else "ye key kisi tool ke liye set nahi hai")}); return
                if _daycount(ent["label"])>=ent["daily"]:
                    self._send(429,{"ok":False,"why":"daily_limit","msg":f"aaj ke {ent['daily']} hits poore"}); return
                _inc_usage(ent["label"])
            tool=(args.get("tool") or body.get("tool") or "").strip().lower()
            term=(args.get("term") or body.get("term") or body.get("q") or body.get("query") or "").strip()
            if not term:
                self._send(400,{"ok":False,"why":"validation","msg":"term bhejo"}); return
            HITS["total"]+=1
            res=U.lookup(tool,term)
            HITS["per_tool"][tool]=HITS["per_tool"].get(tool,0)+1
            HITS["ok" if res.get("ok") else "fail"]+=1
            self._send(200,res); return
        self._send(404,{"ok":False,"why":"route","msg":"POST rasta nahi"})
    def do_DELETE(self):
        q=urlparse(self.path); p=q.path.rstrip("/") or "/"; args={k:v[0] for k,v in parse_qs(q.query).items()}
        if p=="/api/keys/delete":
            if not _owner_ok(args,self.headers):
                self._send(401,{"ok":False,"why":"auth","msg":"sirf owner key"}); return
            label=args.get("label") or ""
            if not label:
                self._send(400,{"ok":False,"why":"validation","msg":"label bhejo"}); return
            arr=_load_keys_file()
            new_arr=[e for e in arr if e["label"]!=label]
            if len(new_arr)==len(arr):
                self._send(404,{"ok":False,"why":"notfound","msg":"file me ye label nahi — env wali key ko Render env se hatao"}); return
            _save_keys_file(new_arr)
            self._send(200,{"ok":True,"msg":f"{label} delete ho gaya"}); return
        self._send(404,{"ok":False,"why":"route"})

def main():
    _ensure_data_dir(); _load_usage_file()
    # v2.15.1 — provider results ko disk se wapas laao (VPS/restart proof)
    try:
        import xtrace as _X
        _X.start_maintenance()
        U.start_db_maintenance()
    except Exception as _e:
        print("[xtrace] maintenance start fail:", _e, flush=True)
    threading.Thread(target=_keepawake, daemon=True).start()
    srv=ThreadingHTTPServer(("0.0.0.0",PORT),H)
    print(f"⚡ HIMANSHU API v{U.VERSION} chalu — port {PORT} {'(auth ON)' if _keys() else '(⚠️ GATEWAY_KEY set karo!)'}",flush=True)
    srv.serve_forever()

if __name__=="__main__": main()
