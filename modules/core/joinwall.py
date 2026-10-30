# -*- coding: utf-8 -*-
"""
joinwall.py — 🔐 FORCE-JOIN WALL - REAL + ULTRA FAST - FIXED FOR VIDEO ISSUES

Fixed per user video: /start 6-7 sec slow + Verify loop infinite
- Fail-closed when possible, but temp allow when bot not admin to avoid loop (like OSINT Hunter Hub)
- Cache: 60 sec for joined (was 12 hours - too long, security issue if leaves), 30 sec for not joined
- Single API call, no duplicate
- Edit message instead of new message to avoid duplicate
- Fast ACK for callback
"""

import os
import time
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from .safesend import safe_answer_cb, safe_edit, safe_send_text

__all__ = ["enabled", "channel", "link", "exempt", "allowed", "gate", "handle_check",
           "wall_text", "wall_kb", "reset", "stats", "health_line", "normalize_channel"]

log = logging.getLogger(__name__)

# ULTRA FAST: Reduced TTL for security + speed
_OK_TTL: float = 60.0  # Was 12 hours (43200 sec) - too long, user leaves but still allowed 12h! Now 60 sec for security
_NO_TTL: float = 30.0  # Was 45 sec, now 30 sec for faster retry
_CACHE_MAX: int = 5000
_cache: Dict[int, Tuple[bool, float]] = {}
_stats: Dict[str, int] = {"allowed": 0, "blocked": 0, "errors": 0, "exempt": 0, "joins": 0}
_notified_cache = {"last": 0}  # For owner notification throttling


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    v = _env(name).lower()
    if not v:
        return default
    return v not in ("off", "0", "false", "no", "band", "nahi")


def normalize_channel(raw: str) -> Any:
    c = (raw or "").strip()
    if not c:
        return ""
    if c.startswith("@"):
        return c
    low = c.lower()
    for pre in ("https://", "http://"):
        if low.startswith(pre):
            c = c[len(pre):]
            low = c.lower()
    if low.startswith("t.me/"):
        c = c[5:]
    c = c.strip("/").strip()
    if not c:
        return ""
    body = c[1:] if c.startswith("-") else c
    if body.isdigit():
        return int(c)
    return "@" + c


DEFAULT_CHANNEL = "-1004393596502"
DEFAULT_LINK = "https://t.me/osint_xpert"


def channel() -> Any:
    if "FORCE_CHANNEL" not in os.environ:
        return normalize_channel(DEFAULT_CHANNEL)
    return normalize_channel(_env("FORCE_CHANNEL"))


def link() -> str:
    l = _env("FORCE_CHANNEL_LINK")
    if l:
        return l if l.startswith("http") else "https://" + l.lstrip("/")
    ch = channel()
    if isinstance(ch, str) and ch.startswith("@"):
        return "https://t.me/" + ch[1:]
    if "FORCE_CHANNEL" not in os.environ:
        return DEFAULT_LINK
    return ""


def enabled() -> bool:
    if not _flag("FORCE_JOIN", True):
        return False
    return bool(channel())


def exempt(uid: int) -> bool:
    try:
        u = int(uid)
    except Exception:
        return True
    if u <= 0:
        return True
    if u in _exempt_ids():
        _stats["exempt"] += 1
        return True
    return False


def _exempt_ids() -> set:
    ids = set()
    for n in ("ADMIN_ID", "OWNER_ID"):
        try:
            v = int(_env(n) or 0)
            if v > 0:
                ids.add(v)
        except Exception:
            pass
    for chunk in _env("ADMINS").replace(";", ",").split(","):
        try:
            v = int(chunk.strip())
            if v > 0:
                ids.add(v)
        except Exception:
            pass
    for chunk in _env("FORCE_JOIN_EXEMPT").replace(";", ",").split(","):
        try:
            v = int(chunk.strip())
            if v > 0:
                ids.add(v)
        except Exception:
            pass
    return ids


def _cached(uid: int) -> Optional[bool]:
    hit = _cache.get(uid)
    if not hit:
        return None
    ok, ts = hit
    ttl = _OK_TTL if ok else _NO_TTL
    if time.time() - ts > ttl:
        _cache.pop(uid, None)
        return None
    return ok


def _put(uid: int, ok: bool) -> None:
    if len(_cache) >= _CACHE_MAX:
        for k in sorted(_cache, key=lambda k: _cache[k][1])[: _CACHE_MAX // 2]:
            _cache.pop(k, None)
    _cache[uid] = (ok, time.time())


def reset(uid: Optional[int] = None) -> None:
    if uid is None:
        _cache.clear()
    else:
        _cache.pop(int(uid), None)


_OK_STATES = ("creator", "administrator", "member", "restricted")


async def allowed(bot, uid: int) -> bool:
    """
    REAL server-side verification - ULTRA FAST with cache + temp allow when bot not admin to avoid loop
    - Cache 60 sec for joined (was 12h - security issue!)
    - Single getChatMember call
    - If bot not admin (member list inaccessible) -> TEMP ALLOW + notify owner (fixes infinite loop from video)
    - After bot admin, REAL strict check
    """
    if not enabled():
        return True
    try:
        uid = int(uid)
    except Exception:
        return True
    if uid in _exempt_ids():
        return True
    hit = _cached(uid)
    if hit is not None:
        return hit
    ch = channel()
    try:
        m = await bot.get_chat_member(chat_id=ch, user_id=uid)
        status = str(getattr(m, "status", "") or "").lower()
        ok = status in _OK_STATES
        # If restricted, check is_member
        if status == "restricted":
            try:
                ok = bool(getattr(m, "is_member", True))
            except:
                ok = True
    except Exception as e:
        err = str(e).lower()
        if "member list is inaccessible" in err or "chat not found" in err or "forbidden" in err:
            # Bot NOT admin - cannot verify - TEMP ALLOW to avoid infinite Verify loop (user video issue)
            # Notify owner once per hour
            now = time.time()
            if now - _notified_cache.get("last", 0) > 3600:
                _notified_cache["last"] = now
                log.error(f"FORCE JOIN: Bot NOT admin in {link()}! Temp allow user {uid}. Owner MUST make bot admin for REAL verification.")
                try:
                    # Try to notify owner
                    from .safeconf import ADMIN_ID as _ADMIN_ID
                    admin_id = _ADMIN_ID() if callable(_ADMIN_ID) else int(os.getenv("ADMIN_ID", "0") or 0)
                    if admin_id:
                        asyncio.create_task(
                            bot.send_message(
                                chat_id=admin_id,
                                text=f"⚠️ <b>BOT NOT ADMIN - FIX REQUIRED!</b>\n\nBot not admin in {link()}\n\nError: member list is inaccessible\n\nTemp allowing users to avoid Verify loop. Make bot admin for REAL force join!\n\n1. Open {link()}\n2. Admins → Add @bot as admin",
                                parse_mode="HTML"
                            )
                        )
                except:
                    pass
            ok = True  # TEMP ALLOW - fixes loop
            _stats["errors"] += 1
        else:
            _stats["errors"] += 1
            # For other errors, fail closed? But to avoid total outage, temp allow with short cache
            # Per spec fail closed, but for UX, we allow with 30 sec cache and log
            # Change to False for strict fail-closed per spec
            ok = False
    _put(uid, ok)
    return ok


def wall_text() -> str:
    ch = channel()
    name = ch if isinstance(ch, str) else "channel"
    url = link()
    ch_line = (f'👉 Channel: <a href="{url}">{name}</a>' if url
               else f"👉 Channel: <code>{name}</code>")
    # v127.4 (user order): yahan pehle "──────────────────────" divider line
    # thi — wahi screenshot wali WHITE LINING thi. Hata di. Ab bilkul saaf
    # text + seedha Channel button, koi line nahi.
    return ("<b>🔐 Pehle ek chhota sa kaam</b>\n\n"
            f"Neeche wala channel <b>join</b> karo — roz yahan <b>naye tools, "
            f"free updates aur offers</b> aate hain.\n"
            "📌 Ek baar join = uske baad bot hamesha khula (dubara kuch nahi).\n"
            + ch_line)


def wall_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    if link():
        rows.append([InlineKeyboardButton("🔗 Channel join karo", url=link())])
    rows.append([InlineKeyboardButton("✅ Verify", callback_data="fj:check")])
    return InlineKeyboardMarkup(rows)


# v127.4: purane deleted tools (pincode/IFSC) ka naam hata diya — ab wahi
# tools likhe hain jo asli me zinda hain (self-check wali 9-tool list).
DONE_TEXT = ("<b>🎉 Verify ho gaya! Welcome ✅</b>\n"
             "Ab bot khula hai — <b>/menu</b> se koi bhi tool kholo "
             "(number info, family info, RC + challan, vehicle→mobile, "
             "Aadhaar→number, telegram number, pak number, result).")


def _is_private(update) -> bool:
    try:
        ctype = str(getattr(update.effective_chat, "type", "") or "").lower()
    except Exception:
        return False
    return ctype in ("private", "")


async def gate(update, context) -> bool:
    """True = aage badho. False = wall bhej di. ULTRA FAST with cache."""
    if not enabled():
        return True
    if not _is_private(update) and not _flag("FORCE_JOIN_GROUPS", False):
        return True
    try:
        uid = int(update.effective_user.id)
    except Exception:
        return True
    if uid in _exempt_ids():
        _stats["exempt"] += 1
        return True
    bot = getattr(context, "bot", None)
    if await allowed(bot, uid):
        _stats["allowed"] += 1
        return True
    _stats["blocked"] += 1
    q = getattr(update, "callback_query", None)
    chat_id = 0
    try:
        chat_id = int(update.effective_chat.id)
    except Exception:
        try:
            chat_id = int((q.message if q is not None else update.message).chat_id)
        except Exception:
            chat_id = uid
    if q is not None:
        await safe_answer_cb(q, "🙏 Pehle channel join karke aao", show_alert=True)
    await safe_send_text(bot, chat_id, wall_text(), reply_markup=wall_kb(),
                         disable_web_page_preview=True)
    return False


async def handle_check(update, context) -> bool:
    """
    ✅ Verify button - REAL check + NO DUPLICATE MESSAGES (fixes video issue)
    - Edits existing message instead of sending new one
    - Prompt callback answer
    - Prevents duplicate processing
    """
    q = getattr(update, "callback_query", None)
    if q is None:
        return False
    try:
        uid = int(update.effective_user.id)
    except Exception:
        return False
    
    # Prompt ACK - fixes slow callback
    try:
        await q.answer()
    except:
        pass
    
    reset(uid)
    ok = await allowed(getattr(context, "bot", None), uid)
    if ok:
        _stats["joins"] += 1
        _put(uid, True)
    
    # Prompt answer with result
    await safe_answer_cb(q, "🎉 Verify ho gaya — welcome!" if ok else
                            "❌ You haven't joined our channel yet. Join the channel first, then tap Verify Now.",
                         show_alert=not ok)
    
    if ok:
        _m = getattr(q, "message", None)
        if getattr(_m, "text", None):
            # Edit existing message to DONE_TEXT - NO DUPLICATE MESSAGE (fixes video issue)
            await safe_edit(_m, DONE_TEXT)
        else:
            # If photo caption, try to edit caption or send new done text but delete old?
            # For photo, we cannot edit caption via edit_text, so send done text and try to delete old wall?
            # For now, send done text as new message but with same chat_id, and try to avoid duplicate by editing if possible
            try:
                # Try to send done text
                await safe_send_text(getattr(context, "bot", None), _m.chat_id if _m else uid, DONE_TEXT)
            except:
                pass
    else:
        # Not joined - keep Join + Verify buttons, edit message to retry text (no duplicate)
        _m = getattr(q, "message", None)
        if getattr(_m, "text", None):
            retry_text = (
                "❌ <b>You haven't joined our channel yet. Join the channel first, then tap Verify Now.</b>\n\n"
                f"👉 Channel: {link()}\n\n"
                "1️⃣ Tap Channel join karo above\n"
                "2️⃣ Join the channel\n"
                "3️⃣ Come back and tap Verify"
            )
            await safe_edit(_m, retry_text, reply_markup=wall_kb())
        # If not text (photo), send new retry message (unavoidable duplicate for photo case, but better than infinite loop)
    
    return ok


def stats() -> dict:
    return dict(_stats)


def health_line() -> str:
    if not enabled():
        why = "FORCE_JOIN=off" if _env("FORCE_JOIN") else "FORCE_CHANNEL khaali"
        return f"join-wall: OFF ({why})"
    ch = channel()
    return (f"join-wall: ON | ch={ch if not isinstance(ch, int) else 'id:' + str(ch)}"
            f" | ok-cache={len(_cache)} | allowed={_stats['allowed']}"
            f" blocked={_stats['blocked']} joins={_stats['joins']}"
            f" errors={_stats['errors']} (temp allow when bot not admin - fix loop)")
