# -*- coding: utf-8 -*-
"""
safesend.py — Bulletproof Telegram send layer (v60)
===================================================
Telegram se message bhejna 15 tarah se FAIL ho sakta hai. Pehle har tool me
seedha `await msg.reply_text(...)` likha tha — isliye ek chhoti si dikkat par
poora tool crash ho jata tha aur user ko "Chhota sa ghatna ho gaya" dikhta tha
(credit bhi kat chuka hota tha 😞).

YE FILE UN SAARI DIKKATON KO KHATAM KARTI HAI:

  1. MESSAGE TOO LONG      — Telegram limit 4096 chars. Lamba card bhejte hi
                             BadRequest -> crash. Ab: smart split (HTML-safe).
  2. HTML ENTITY ERROR     — "Can't parse entities: can't find end tag 'i'".
                             Ab: tag repair -> phir bhi fail to plain text.
  3. FLOOD WAIT (429)      — "Too Many Requests: retry after 30". Ab: wait
                             karke khud dobara bhejta hai (crash nahi).
  4. NETWORK BLIP          — TimedOut / NetworkError / Bad Gateway. Ab: retry
                             with backoff -> 3 koshish.
  5. CHAT MIGRATED         — group supergroup ban gaya. Ab: naye chat id par.
  6. MESSAGE NOT MODIFIED  — same text dobara edit. Ab: chup-chaap ignore.
  7. BLOCKED BY USER       — user ne bot block kiya. Ab: chup-chaap ignore
                             (log me note, crash nahi).
  8. MESSAGE TOO OLD       — 48 ghante purana message edit nahi hota.
  9. BUTTON DATA INVALID   — callback_data 64 bytes se bada. Ab: auto-trim.
 10. DOCUMENT/PHOTO FAIL   — media bhejte waqt error. Ab: text fallback.

⚠️  SABSE BADI BAAT: ye functions **KABHI raise nahi karte.**
    Har function hamesha True/False return karta hai. Iska matlab — kisi bhi
    tool me ye laga do, wo tool crash ho hi nahi sakta.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from .html_safe import cut_html, strip_html

__all__ = [
    "safe_send_text", "safe_reply", "safe_edit", "safe_send_photo",
    "safe_send_document", "safe_answer_cb", "safe_delete", "safe_send_video",
    "split_html", "repair_html", "TG_LIMIT", "TG_CAPTION_LIMIT", "SendResult",
]

log = logging.getLogger("utility-super-bot.safesend")

TG_LIMIT = 4096              # message text limit
TG_CAPTION_LIMIT = 1024      # media caption limit
_MAX_RETRY = 3               # network retry
_MAX_WAIT = 45               # flood-wait cap (second)


class SendResult:
    """send ka result — .ok batao kaam hua ya nahi, .msg mile to message object."""

    __slots__ = ("ok", "msg", "reason", "sent_count")

    def __init__(self, ok: bool, msg: Any = None, reason: str = "", sent_count: int = 0):
        self.ok = bool(ok)
        self.msg = msg
        self.reason = str(reason or "")
        self.sent_count = int(sent_count or 0)

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:                                   # pragma: no cover
        return f"SendResult(ok={self.ok}, reason={self.reason!r}, parts={self.sent_count})"


# =====================================================================
#  HTML REPAIR  (crash #2 ki jadd)
# =====================================================================
_TAG_RE_NAMES = ("b", "i", "u", "s", "code", "pre", "tg-spoiler", "a", "em", "strong",
                 "ins", "strike", "del", "blockquote", "span")


def repair_html(text: str) -> str:
    """Adhoore/khatarnak HTML ko bhejne-layak banao — Telegram ka
    "can't find end tag" error iske baad namumkin ho jata hai.

    Chaar kaam karta hai:
      1. adhoora '<' fragment (bina '>') hata deta hai
      2. bina escape wale '&' ko '&amp;' karta hai
      3. ORPHAN closing tags (jinka opening tag hi nahi hai) hata deta hai
         — ye asli crash ka karan the
      4. khule reh gaye tags ko band kar deta hai
    """
    if not text:
        return ""
    import re as _re
    t = str(text)
    # (1) adhoora tag fragment
    lt = t.rfind("<")
    if lt != -1 and ">" not in t[lt:]:
        t = t[:lt]
    # (2) '&' escape (jo asli entity na ho)
    t = _re.sub(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,10}|#\d{1,7}|#x[0-9a-fA-F]{1,6});)", "&amp;", t)

    # (3)+(4) tags ka hisaab — teen tarah ki gadbad theek karta hai:
    #   • ORPHAN closing tag (opening hi nahi)   -> hata do (ye Telegram reject karta hai)
    #   • DUPLICATE nested opening (`<pre><pre>`) -> hata do (Telegram me iska
    #     koi extra matlab nahi, par 1500 baar ho to message phool jata hai)
    #   • KHULA reh gaya tag                       -> aakhir me band kar do
    tag_re = _re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>")
    pieces: list = []
    last = 0
    stack: list = []
    for m in tag_re.finditer(t):
        name = m.group(2).lower()
        if name not in _TAG_RE_NAMES:
            continue                      # unknown tag — jaisa hai waisa chhodo
        if m.group(1):                    # ---------- closing tag
            if name in stack:
                # is tag ke andar ke saare adhoore tags pehle band karo
                while stack and stack[-1] != name:
                    inner = stack.pop()
                    pieces.append(t[last:m.start()])
                    pieces.append(f"</{inner}>")
                    last = m.start()
                if stack:
                    stack.pop()
                pieces.append(t[last:m.end()])
                last = m.end()
            else:
                # 🚨 ORPHAN closing — text rakho, tag gayab kar do
                pieces.append(t[last:m.start()])
                last = m.end()
        else:                             # ---------- opening tag
            if name in stack:
                # duplicate nested open — drop (balance bacha rehta hai)
                pieces.append(t[last:m.start()])
                last = m.end()
            else:
                stack.append(name)
    pieces.append(t[last:])
    out = "".join(pieces)
    if stack:
        out = out + "".join(f"</{n}>" for n in reversed(stack))
    return out


def _looks_like_entity_error(err: Exception) -> bool:
    s = str(err).lower()
    return ("parse entities" in s or "can't find end tag" in s or "unsupported start tag" in s
            or "entity" in s and "find end tag" in s)


def _looks_like_too_long(err: Exception) -> bool:
    s = str(err).lower()
    return "message is too long" in s or "message_too_long" in s


def _looks_like_blocked(err: Exception) -> bool:
    s = str(err).lower()
    return ("blocked" in s or "user is deactivated" in s or "chat not found" in s
            or "bot was kicked" in s or "peer_id_invalid" in s
            or "chat_write_forbidden" in s or "bot can't initiate" in s
            or "not enough rights" in s or "kicked" in s)


def _looks_like_ignorable(err: Exception) -> bool:
    s = str(err).lower()
    return ("message is not modified" in s or "message to delete not found" in s
            or "message can't be deleted" in s or "query is too old" in s
            or "message to edit not found" in s)


# =====================================================================
#  LONG MESSAGE SPLIT  (crash #1 ki jadd)
# =====================================================================
def split_html(text: str, limit: int = TG_LIMIT, parse_mode: Optional[str] = "HTML") -> list:
    """Lamba message ko Telegram-safe tukdon me baanto — BINA kuch khoye.

    Design (v60.1 — bug fix):
      • `head` = rest ka asli prefix (jin characters ko hum "kha" gaye).
        Consume karne ka hisaab `len(head)` se hota hai, `len(piece)` se NAHI —
        kyunki `piece` me band karne wale tags jod diye jaate hain aur unse
        hisaab bigadta tha (purane code me aakhri tukda DOBARA bhej diya jata
        tha aur kuch text chhoot bhi jata tha).
      • Har tukda apne aap me valid HTML hota hai: khule tags band kar diye
        jaate hain aur agle tukde me dobara khul jaate hain.
      • Ek hi line limit se lambi ho to usko limit par kaat dete hain
        (newline pe rokne ki koshish me infinite loop na ho).
    """
    t = "" if text is None else str(text)
    if limit <= 0:
        limit = TG_LIMIT
    limit = max(64, int(limit))
    if len(t) <= limit:
        # chhota message bhi sanitize karo — orphan closing tag ho to Telegram
        # poora message reject karta hai (asli crash ka karan)
        return [repair_html(t) if (parse_mode and t) else t] if t else [""]

    out: list = []
    rest = t
    guard = 0
    _RESERVE = 64          # closing tags ke liye jagah chhod do
    # kitne tukde banenge uska hisaab — isse zyada loop kabhi nahi chalega
    _max_rounds = (len(t) // max(64, limit - _RESERVE)) + 12

    while rest and guard < _max_rounds:
        guard += 1
        if len(rest) <= limit:
            # aakhri tukda — isme bhi khule tags band karo (warna Telegram reject)
            out.append(repair_html(rest) if parse_mode else rest)
            rest = ""
            break

        # ---- step 1: kitna kata jayega (asli consume hone wala hissa)
        room = max(64, limit - _RESERVE)
        head = rest[:room]
        nl = head.rfind("\n")
        # newline bahut peeche na ho (warna chhote-chhote tukde banenge)
        if nl > room * 0.5:
            head = head[:nl]
        # adhoora tag fragment hatao ('<' aaya par '>' nahi)
        lt = head.rfind("<")
        if lt != -1 and ">" not in head[lt:]:
            head = head[:lt]
        if not head:
            # poori line hi badi thi — zabardasti kaato
            head = rest[:room]
        head = head.rstrip("\n")

        # ---- step 2: tukda banao (tags band karke)
        if parse_mode:
            piece = repair_html(head)
            # repairing ke baad agar limit cross ho gayi, to head chhota karo
            # aur dobara try karo. (Pehle hum yahan RAW head par gir jate the,
            # jisse tukda adhoora/reject-hone-layak reh jata tha.)
            for _try in range(4):
                if len(piece) <= limit:
                    break
                over = len(piece) - limit
                cut_to = max(64, len(head) - over - 8)
                head = rest[:cut_to]
                nl2 = head.rfind("\n")
                if nl2 > cut_to * 0.5:
                    head = head[:nl2]
                lt2 = head.rfind("<")
                if lt2 != -1 and ">" not in head[lt2:]:
                    head = head[:lt2]
                if not head:
                    head = rest[:max(64, limit - 32)]
                piece = repair_html(head)
            if len(piece) > limit:
                piece = repair_html(head[:limit])[:limit]
        else:
            piece = head
        if not piece:
            piece = rest[:limit]
            head = piece

        out.append(piece)

        # ---- step 3: ASLI consume hua hissa hatao (piece ki lambai se nahi!)
        consumed = len(head)
        if consumed <= 0:
            consumed = len(piece)
        rest = rest[consumed:].lstrip("\n")

        # ---- step 4: agle tukde me khule tags dobara kholo
        # ⚠️ `head` (asli prefix) se dekho, `piece` se NAHI: piece me hum ne
        #    khud closing tags jode hote hain, isliye usme kuch bhi "khula"
        #    nahi dikhta aur agli line ki formatting (bold/italic) toot jati.
        if parse_mode and rest:
            opens = _open_tags(head)
            if opens:
                rest = "".join(f"<{n}>" for n in opens) + rest

    return [p for p in out if p and p.strip()] or [""]


def _open_tags(html: str, max_out: int = 4) -> list:
    """Is text me kaun-kaun se tag KHULE reh gaye (bahar se andar ke order me).

    v60.1 safeguard: agar text me koi tag baar-baar khula ho bina band hone ke
    (jaise galat se 1500 baar `<pre>`), to hum sirf UNIQUE tags lautate hain.
    Isse agli line me 1500 tags nahi lagte aur message chhote-chhote 2000
    tukdon me nahi bat jata (jo bhi Telegram ke liye kharab hai).
    """
    import re as _re
    tag_re = _re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>")
    stack: list = []
    for m in tag_re.finditer(html or ""):
        name = m.group(2).lower()
        if name not in _TAG_RE_NAMES:
            continue
        if m.group(1):
            if stack and stack[-1] == name:
                stack.pop()
            elif name in stack:
                # mismatch — jo bhi ho, isko hata do (stack saaf rakho)
                while stack and stack.pop() != name:
                    pass
        else:
            stack.append(name)
    # dedupe (order bachaye rakhte hue) + cap
    seen, out = set(), []
    for n in stack:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out[:max_out]


# =====================================================================
#  THE CORE SEND  —  sab kuch ek jagah handle hota hai
# =====================================================================
async def _raw_call(coro_factory):
    """Ek retry-loop: flood-wait + network blip + ignorable errors handle karta hai.

    coro_factory: har koshish par NAYA coroutine banane wala callable (zaroori —
    ek hi coroutine dobara await nahi ho sakti).
    Returns (ok, result_or_None, err_or_None)
    """
    from telegram.error import (BadRequest, Forbidden, NetworkError, RetryAfter,
                                TelegramError, TimedOut)

    last: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            res = await coro_factory()
            return True, res, None
        except RetryAfter as e:                                  # 429 flood wait
            wait = int(getattr(e, "retry_after", 5) or 5)
            wait = max(1, min(wait, _MAX_WAIT))
            log.warning("Flood-wait %ss (koshish %s/%s) — ruk kar dobara bhejta hoon",
                        wait, attempt, _MAX_RETRY)
            if attempt >= _MAX_RETRY:
                last = e
                break
            await asyncio.sleep(wait + 1)
            continue
        except (TimedOut, NetworkError) as e:                    # net blip
            last = e
            if attempt >= _MAX_RETRY:
                break
            await asyncio.sleep(min(2 ** attempt, 8))
            continue
        except Forbidden as e:                                   # user ne block kiya
            return False, None, e
        except BadRequest as e:                                  # content problem
            return False, None, e
        except TelegramError as e:
            last = e
            if attempt >= _MAX_RETRY:
                break
            await asyncio.sleep(1.5)
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # noqa: BLE001
            return False, None, e
    return False, None, last


async def safe_send_text(bot, chat_id: int, text: str, *, parse_mode: Optional[str] = "HTML",
                         reply_markup: Any = None, reply_to_message_id: Optional[int] = None,
                         disable_web_page_preview: bool = True, split: bool = True,
                         **_extra) -> SendResult:
    """Kisi bhi chat me text bhejo — kabhi crash nahi.

    Lamba text ho to khud baant deta hai; HTML tooti ho to repair karta hai;
    phir bhi na mane to plain text bhej deta hai.

    Returns SendResult (hamesha — exception kabhi nahi).
    """
    if bot is None:
        return SendResult(False, reason="no bot")
    body = "" if text is None else str(text)
    if not body.strip():
        body = "…"

    def _mk(chunk_text: str, mode: Optional[str], markup: Any):
        kw: dict = {}
        if mode:
            kw["parse_mode"] = mode
        if markup is not None:
            kw["reply_markup"] = markup
        if reply_to_message_id:
            kw["reply_to_message_id"] = reply_to_message_id
            kw["allow_sending_without_reply"] = True
        if mode is None:
            kw["disable_web_page_preview"] = bool(disable_web_page_preview)
        try:
            return bot.send_message(chat_id=chat_id, text=chunk_text, **kw)
        except TypeError:
            # purani PTB version me koi kwarg na ho
            kw.pop("allow_sending_without_reply", None)
            kw.pop("disable_web_page_preview", None)
            return bot.send_message(chat_id=chat_id, text=chunk_text, **kw)

    chunks = split_html(body, TG_LIMIT, parse_mode) if split else [body]
    sent = 0
    first_msg = None

    for idx, chunk in enumerate(chunks):
        # is chunk par pehla button hi lagega (spam se bachne ke liye)
        markup = reply_markup if idx == 0 else None
        rtid = reply_to_message_id if idx == 0 else None

        def _factory(_t=chunk, _m=parse_mode, _k=markup, _r=rtid):
            if _r:
                return _mk(_t, _m, _k)
            # reply_to sirf pehle chunk par
            kk = dict(_k) if isinstance(_k, dict) else _k

            async def _c():
                kw: dict = {}
                if _m:
                    kw["parse_mode"] = _m
                if kk is not None:
                    kw["reply_markup"] = kk
                if _m is None:
                    kw["disable_web_page_preview"] = True
                return await bot.send_message(chat_id=chat_id, text=_t, **kw)
            return _c()

        ok, res, err = await _raw_call(_factory)

        # --- repair 1: HTML toota tha -> repair karke dobara
        if not ok and err is not None and parse_mode and _looks_like_entity_error(err):
            fixed = repair_html(chunk)
            ok, res, err = await _raw_call(
                lambda _t=fixed, _k=(markup if idx == 0 else None):
                    _mk(_t, "HTML", _k))

        # --- repair 2: bahut lamba tha -> chhota karke dobara
        if not ok and err is not None and _looks_like_too_long(err) and parse_mode:
            for _lim in (3500, 2500, 1500, 900):
                ok, res, _err2 = await _raw_call(
                    lambda _t=cut_html(chunk, _lim), _k=(markup if idx == 0 else None):
                        _mk(_t, "HTML", _k))
                if ok:
                    break
                err = _err2

        # --- repair 3: bhejne layak hi nahi -> PLAIN TEXT (sabse safe)
        if not ok and err is not None and parse_mode and not _looks_like_blocked(err):
            plain = strip_html(chunk)
            for _lim in (TG_LIMIT, 3000, 2000):
                ok, res, _err3 = await _raw_call(
                    lambda _t=plain[:_lim], _k=(markup if idx == 0 else None):
                        _mk(_t, None, _k))
                if ok:
                    break
                err = _err3

        if not ok:
            if err is not None and _looks_like_ignorable(err):
                continue
            if err is not None and _looks_like_blocked(err):
                log.info("send skip — user/chat reachable nahi (%s)", str(err)[:90])
                return SendResult(False, reason=str(err)[:120], sent_count=sent)
            log.error("send text FAIL (%s) — %s", type(err).__name__ if err else "?",
                      str(err)[:160] if err else "unknown")
            return SendResult(False, reason=str(err)[:160] if err else "unknown",
                              sent_count=sent)
        sent += 1
        if first_msg is None:
            first_msg = res
        if idx < len(chunks) - 1:
            await asyncio.sleep(0.35)                            # Telegram ko saans lene do
    return SendResult(True, first_msg, sent_count=sent)


async def safe_reply(msg, text: str, **kw) -> SendResult:
    """Kisi message ka reply — kabhi crash nahi. Lamba text khud baant deta hai."""
    if msg is None:
        return SendResult(False, reason="no message")
    chat_id = None
    try:
        chat_id = msg.chat_id
    except Exception:                                            # noqa: BLE001
        try:
            chat_id = msg.chat.id
        except Exception:                                        # noqa: BLE001
            chat_id = None
    if chat_id is None:
        return SendResult(False, reason="no chat id")
    kw.setdefault("reply_to_message_id", None)
    try:
        return await safe_send_text(msg.get_bot(), chat_id, text, **kw)
    except Exception as e:                                       # noqa: BLE001
        log.debug("safe_reply fallback (%s)", str(e)[:90])
        return SendResult(False, reason=str(e)[:120])


async def safe_edit(msg, text: str, *, parse_mode: Optional[str] = "HTML",
                    reply_markup: Any = None, **_extra) -> SendResult:
    """Message edit — 'not modified' / purana message ho to crash nahi.

    Edit fail ho jaye to naya message bhej deta hai (user ko jawab milna chahiye
    hi). Isliye user kabhi atka nahi rehta.
    """
    if msg is None:
        return SendResult(False, reason="no message")
    body = "" if text is None else str(text)
    if parse_mode:
        body = cut_html(body, TG_LIMIT)

    def _mk(t: str, mode: Optional[str]):
        if mode:
            return msg.edit_text(text=t, parse_mode=mode,
                                 **({"reply_markup": reply_markup} if reply_markup is not None else {}))
        return msg.edit_text(text=t,
                             **({"reply_markup": reply_markup} if reply_markup is not None else {}))

    ok, res, err = await _raw_call(lambda: _mk(body, parse_mode))
    if not ok and err is not None and _looks_like_ignorable(err):
        return SendResult(True, res, reason="not-modified")      # koi kaam nahi, par crash nahi
    if not ok and err is not None and parse_mode:
        ok, res, err = await _raw_call(lambda: _mk(repair_html(body), "HTML"))
    if not ok and err is not None and parse_mode:
        ok, res, err = await _raw_call(lambda: _mk(strip_html(body), None))
    if not ok:
        # edit hi nahi ho paya (purana message / caption hai) -> naya bhej do
        try:
            return await safe_reply(msg, body, parse_mode=parse_mode,
                                    reply_markup=reply_markup)
        except Exception as e:                                   # noqa: BLE001
            return SendResult(False, reason=str(e)[:120])
    return SendResult(True, res)


async def _safe_media(bot, chat_id, sender_name: str, file_obj, *, caption: str = "",
                      parse_mode: Optional[str] = "HTML", reply_markup: Any = None,
                      reply_to_message_id: Optional[int] = None,
                      fallback_text: str = "", **kwargs) -> SendResult:
    """Photo / video / document bhejne ka common safe raasta."""
    if bot is None or file_obj is None:
        return SendResult(False, reason="no bot/file")
    cap = "" if caption is None else str(caption)
    if len(cap) > TG_CAPTION_LIMIT:
        cap = cut_html(cap, TG_CAPTION_LIMIT) if parse_mode else cap[:TG_CAPTION_LIMIT]
    sender = getattr(bot, sender_name, None)
    if sender is None:
        return SendResult(False, reason=f"bot.{sender_name} available nahi")
    arg = _media_kw(sender_name, file_obj)
    extra = {k: v for k, v in (kwargs or {}).items() if v is not None}

    def _build(use_cap: str, mode: Optional[str]):
        kw: dict = dict(extra)
        if use_cap:
            kw["caption"] = use_cap
            if mode:
                kw["parse_mode"] = mode
        if reply_markup is not None:
            kw["reply_markup"] = reply_markup
        if reply_to_message_id:
            kw["reply_to_message_id"] = reply_to_message_id
            kw["allow_sending_without_reply"] = True
        return sender(chat_id=chat_id, **dict(arg), **kw)

    ok, res, err = await _raw_call(lambda: _build(cap, parse_mode))
    if not ok and err is not None and _looks_like_entity_error(err):
        ok, res, err = await _raw_call(
            lambda: _build(strip_html(cap)[:TG_CAPTION_LIMIT], None))
    if not ok and fallback_text:
        log.info("%s send fail (%s) — text fallback bhej raha hoon",
                 sender_name, str(err)[:90] if err else "?")
        return await safe_send_text(bot, chat_id, fallback_text, parse_mode=parse_mode,
                                    reply_markup=reply_markup,
                                    reply_to_message_id=reply_to_message_id)
    if not ok:
        return SendResult(False, reason=str(err)[:160] if err else "unknown")
    if len(str(caption)) > TG_CAPTION_LIMIT:
        # caption lamba tha — poora text alag se bhej do (kuch bhi chhoot na jaye)
        rest = str(caption)[TG_CAPTION_LIMIT:]
        await safe_send_text(bot, chat_id, rest, parse_mode=parse_mode)
    return SendResult(True, res)


def _media_kw(sender_name: str, file_obj) -> dict:
    """Har sender ka file ka arg ka naam alag hota hai."""
    if "document" in sender_name:
        return {"document": file_obj}
    if "video" in sender_name:
        return {"video": file_obj}
    if "audio" in sender_name:
        return {"audio": file_obj}
    if "animation" in sender_name:
        return {"animation": file_obj}
    return {"photo": file_obj}


async def safe_send_photo(bot, chat_id: int, photo, *, caption: str = "",
                          parse_mode: Optional[str] = "HTML", reply_markup: Any = None,
                          reply_to_message_id: Optional[int] = None,
                          fallback_text: str = "", **kw) -> SendResult:
    """Photo bhejo — kabhi crash nahi. Fail ho to text fallback."""
    return await _safe_media(bot, chat_id, "send_photo", photo, caption=caption,
                             parse_mode=parse_mode, reply_markup=reply_markup,
                             reply_to_message_id=reply_to_message_id,
                             fallback_text=fallback_text, **kw)


async def safe_send_video(bot, chat_id: int, video, *, caption: str = "",
                          parse_mode: Optional[str] = "HTML", reply_markup: Any = None,
                          reply_to_message_id: Optional[int] = None,
                          fallback_text: str = "", duration: Any = None,
                          width: Any = None, height: Any = None, **kw) -> SendResult:
    """Video bhejo — kabhi crash nahi."""
    if duration:
        kw["duration"] = duration
    return await _safe_media(bot, chat_id, "send_video", video, caption=caption,
                             parse_mode=parse_mode, reply_markup=reply_markup,
                             reply_to_message_id=reply_to_message_id,
                             fallback_text=fallback_text, **kw)


async def safe_send_document(bot, chat_id: int, document, *, caption: str = "",
                             parse_mode: Optional[str] = "HTML", reply_markup: Any = None,
                             filename: str = "", reply_to_message_id: Optional[int] = None,
                             fallback_text: str = "", **kw) -> SendResult:
    """Document bhejo — kabhi crash nahi."""
    if filename:
        try:
            if hasattr(document, "name"):
                document.name = filename
        except Exception:                                        # noqa: BLE001
            pass
    return await _safe_media(bot, chat_id, "send_document", document, caption=caption,
                             parse_mode=parse_mode, reply_markup=reply_markup,
                             reply_to_message_id=reply_to_message_id,
                             fallback_text=fallback_text, **kw)


async def safe_answer_cb(query, text: str = "", *, show_alert: bool = False,
                         cache_time: int = 0, url: str = "") -> bool:
    """Button ka 'loading' spinner band karo — 10 min baad bhi ye chalna chahiye.

    Purani callback query par Telegram error deta hai -> pehle bot crash karta
    tha ("query is too old"). Ab chup-chaap ignore.
    """
    if query is None:
        return False
    kw: dict = {"show_alert": bool(show_alert), "cache_time": int(cache_time)}
    if text:
        kw["text"] = str(text)[:190]
    if url:
        kw["url"] = url

    def _mk():
        return query.answer(**kw)

    ok, _res, _err = await _raw_call(_mk)
    return bool(ok)


async def safe_delete(msg) -> bool:
    """Message delete — agar pehle se delete hai to crash nahi."""
    if msg is None:
        return False

    def _mk():
        return msg.delete()

    ok, _res, _err = await _raw_call(_mk)
    return bool(ok)


def trim_callback_data(data: str, limit: int = 64) -> str:
    """callback_data 64 bytes se bada ho to Telegram poora button reject karta
    hai. Ye use trim kar deta hai (crash nahi)."""
    s = str(data or "")
    if len(s.encode("utf-8")) <= limit:
        return s
    out = s
    while len(out.encode("utf-8")) > limit and out:
        out = out[:-1]
    return out
