# =====================================================================
#  signed_keys.py — STATELESS API keys (HMAC signed)
# =====================================================================
#  Problem jo ye solve karta hai:
#
#     Render ke free plan ka disk EPHEMERAL hai. Matlab har redeploy /
#     restart par data/keys.json UD jaata hai -> saare user keys dead.
#     Users ka bharosa khatam, owner ko har baar nayi keys deni padti hain.
#
#  Solution: key ke andar hi uski info likh do, aur usse HMAC se SIGN kar do.
#
#     uk1.<payload_base64>.<signature>
#
#     payload = {l: label, x: expiry, d: daily, t: tools, n: nonce}
#
#  - Koi database nahi, koi file nahi -> redeploy pe bhi key zinda.
#  - Key ko chheda (tamper) to signature fail -> reject.
#  - Expiry "perm" = kabhi expire nahi (lifetime).
#  - Secret server ke env (KEY_SIGNING_SECRET) me rehta hai, key me nahi.
#
#  Ye file pura stdlib hai (koi dependency nahi).
# =====================================================================
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PREFIX = "uk1"
DEFAULT_DAILY = 200


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def _secret() -> bytes:
    """PRIMARY signing secret.

    Jaan-boojh kar GATEWAY_KEY se derive hota hai (Render env me wo pehle se
    hai aur kabhi apne aap nahi badalti). Isse ye faayda: redeploy, restart,
    ya env-var sync gadbad — kisi se bhi keys nahi marti. (v2.13.1: pehle
    KEY_SIGNING_SECRET primary tha; Render ke ek deploy ne wo env drop kar di
    to us waqt bani keys mar gayi thin — isliye primary ab GATEWAY_KEY hai.)
    """
    gw = (os.environ.get("GATEWAY_KEY") or "").strip()
    return hashlib.sha256(("uks|fallback|" + gw).encode()).digest()


def _all_secrets() -> list:
    """Verify ke waqt ye sab try hote hain (purani keys zinda rahein)."""
    out = [_secret()]
    explicit = (os.environ.get("KEY_SIGNING_SECRET") or "").strip()
    if explicit:
        out.append(hashlib.sha256(("uks|" + explicit).encode()).digest())
    return out


def _sign(payload_b64: str, secret: bytes = None) -> str:
    key = secret if secret is not None else _secret()
    mac = hmac.new(key, (PREFIX + "." + payload_b64).encode(), hashlib.sha256).digest()
    return _b64(mac)[:27]


def _sig_ok(body: str, sig: str) -> bool:
    for sec in _all_secrets():
        try:
            if hmac.compare_digest(sig, _sign(body, sec)):
                return True
        except Exception:
            continue
    return False


def key_id() -> str:
    """Debug ke liye chhota fingerprint (secret khud kahan leak hota hai)."""
    return hashlib.sha256(_secret()).hexdigest()[:8]


def _norm_expiry(exp) -> str:
    """'perm' | 'YYYY-MM-DD' | '7d' (relative din) | '' -> perm"""
    exp = str(exp or "perm").strip()
    if not exp:
        return "perm"
    if exp.lower() in ("perm", "forever", "lifetime", "never", "*"):
        return "perm"
    if exp.endswith("d"):
        try:
            days = float(exp[:-1])
        except ValueError:
            return "perm"
        return str(int(time.time() + days * 86400))
    if exp.endswith("h"):
        try:
            hours = float(exp[:-1])
        except ValueError:
            return "perm"
        return str(int(time.time() + hours * 3600))
    return exp  # already YYYY-MM-DD ya epoch


def make(label: str, exp="perm", daily=DEFAULT_DAILY, tools="*") -> str:
    """Nayi signed key banao. Isse kahin save karne ki zaroorat NAHI."""
    payload = {
        "l": str(label or "user").strip()[:32],
        "x": _norm_expiry(exp),
        "d": int(daily or DEFAULT_DAILY),
        "t": str(tools or "*").strip()[:120],
        "n": secrets.token_hex(4),
        "iat": int(time.time()),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{PREFIX}.{body}.{_sign(body)}"


def _revoked(nonce_or_label: str) -> bool:
    raw = (os.environ.get("REVOKED_KEYS") or "").strip()
    if not raw:
        return False
    items = {x.strip() for x in raw.replace(";", ",").split(",") if x.strip()}
    return nonce_or_label in items


def parse(token: str):
    """Signed key verify karo. Valid -> dict, warna None. Kabhi raise nahi karta."""
    try:
        if not token or not isinstance(token, str):
            return None
        token = token.strip()
        if not token.startswith(PREFIX + "."):
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        _, body, sig = parts
        if not _sig_ok(body, sig):
            return None
        data = json.loads(_unb64(body).decode())
        if not isinstance(data, dict):
            return None
        if _revoked(str(data.get("n") or "")) or _revoked(str(data.get("l") or "")):
            return {"_revoked": True, "label": data.get("l"), "nonce": data.get("n")}
        return data
    except Exception:
        return None


def to_entry(data: dict) -> dict:
    """signed payload -> server ke andar wala standard key entry."""
    exp = str(data.get("x") or "perm")
    if exp != "perm":
        try:
            exp = time.strftime("%Y-%m-%d", time.gmtime(int(exp)))
        except (ValueError, TypeError):
            exp = "perm"
    entry = {
        "label": str(data.get("l") or "signed"),
        "exp": exp,
        "daily": int(data.get("d") or DEFAULT_DAILY),
        "tools": str(data.get("t") or "*"),
        "allowed": _parse_tools(data.get("t")),
        "src": "signed",
        "nonce": data.get("n"),
        "created_at": time.strftime("%Y-%m-%d", time.gmtime(int(data.get("iat") or time.time()))),
    }
    ep = expiry_epoch(data)
    if ep is not None:
        entry["exp_epoch"] = ep      # second-level precision (date string se ghante ud jaate the)
    return entry


def _parse_tools(raw):
    """tools string -> set ya None (=sab allowed). upstreams ke aliases ke bina,
    server.py ise apne _parse_allowed_tools se normalize kar leta hai."""
    if not raw:
        return None
    raw = str(raw).strip().lower()
    if raw in ("*", "all", "any"):
        return None
    parts = [p.strip() for p in raw.replace("+", ",").replace("|", ",").replace(";", ",").split(",") if p.strip()]
    return set(parts) or None


def expiry_epoch(data: dict):
    """Kab expire hoga (unix ts) ya None = kabhi nahi."""
    exp = str(data.get("x") or "perm")
    if exp == "perm":
        return None
    try:
        return int(float(exp))
    except (ValueError, TypeError):
        return None


def info(token: str) -> dict:
    """Debug/CLI ke liye: signed key ki poori info."""
    d = parse(token)
    if not d:
        return {"valid": False}
    if d.get("_revoked"):
        return {"valid": False, "why": "revoked", "label": d.get("label")}
    exp_at = expiry_epoch(d)
    days_left = None if exp_at is None else round((exp_at - time.time()) / 86400, 2)
    return {
        "valid": True,
        "label": d.get("l"),
        "tools": d.get("t"),
        "daily": d.get("d"),
        "nonce": d.get("n"),
        "never_expires": exp_at is None,
        "days_left": days_left,
    }


if __name__ == "__main__":  # chhota CLI:  python signed_keys.py key <label> [exp] [daily] [tools]
    import sys

    args = sys.argv[1:]
    if args and args[0] == "key":
        label = args[1] if len(args) > 1 else "user"
        exp = args[2] if len(args) > 2 else "perm"
        daily = args[3] if len(args) > 3 else DEFAULT_DAILY
        tools = args[4] if len(args) > 4 else "*"
        k = make(label, exp, daily, tools)
        print(k)
        print(json.dumps(info(k), indent=2), file=sys.stderr)
    elif args and args[0] == "check":
        print(json.dumps(info(args[1] if len(args) > 1 else ""), indent=2, ensure_ascii=False))
    else:
        print("usage:\n  python signed_keys.py key <label> [exp] [daily] [tools]\n  python signed_keys.py check <key>")
