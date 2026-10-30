# -*- coding: utf-8 -*-
"""
v93 — 🧯 HTML SAFETY NET (bot ko "crash" dikhne wali sabse badi wajah band)
============================================================================
PROBLEM (jaanch kar confirm)
---------------------------
bot.py me ~500 jagah `parse_mode=HTML` ke saath direct `reply_text` /
`edit_text` / `send_message` / `reply_photo` / `reply_document` hota hai.
Agar us text me HTML ki zara si bhi gadbad ho — engine ke title me `<` aa
gaya, koi tag adhoora reh gaya, user ke naam me `&` tha — to Telegram
poora message REJECT kar deta hai:

    telegram.error.BadRequest: Can't parse entities: can't find end tag 'b'

Natija user ko aisa dikhta hai:  "⚠️ Chhota sa ghatna ho gaya!"
Yaani **kaam ho gaya tha, jawab taiyaar tha, par bheja nahi gaya** —
user ke liye wahi "bot crash ho gaya" hai.

ILAAJ
-----
Bot class ke send/edit methods par ek patla wrapper: agar Telegram
"parse entities" se mana kare to wahi message
  1) pehle `repair_html()` se theek karke,
  2) phir bhi na mane to saare tags hata kar (plain text)
dobara bhej do. Message kabhi gayab nahi hota; bas formatting chali jaati hai.

Ye ek jagah se lagta hai aur poore bot ke ~500 send sites ko cover karta hai —
isliye har call-site par alag try/except lagane ki zaroorat nahi.

Suraksha: wrapper IDEMPOTENT hai (dobara lagane par dobara wrap nahi hota),
aur sirf "parse entities" wale error par kaam karta hai — baaki error pehle
jaise aage badhte hain (Conflict / RetryAfter / Forbidden ka apna handling hai).
"""
import functools
import inspect
import logging
import re

log = logging.getLogger("ud.htmlnet")

_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>", re.S)

# Telegram ka error text alag-alag version me thoda alag aata hai — sab pakdo
_PARSE_ERR_MARKERS = (
    "can't parse entities",
    "cant parse entities",
    "parse entities",
    "unsupported parse_mode",
    "can't find end tag",
    "can't find start tag",
    "expected ending",
)

# Jin methods par net lagta hai: har ek me text ya caption kwargs me jaata hai
_TEXT_METHODS = (
    "send_message", "edit_message_text",
    "send_photo", "send_video", "send_document", "send_audio",
    "send_voice", "send_animation", "send_sticker",
    "edit_message_caption",
)

# Message ke reply_* helpers — ye Bot methods hi call karte hain, isliye
# upar wale net se cover ho jaate hain. Ye list sirf documentation ke liye hai:
#   reply_text, reply_photo, reply_video, reply_document, reply_audio,
#   reply_voice, reply_animation, edit_text

_PATCHED_FLAG = "__ud_html_net__"


def _is_parse_error(err) -> bool:
    """Ye error Telegram ki HTML-parsing wajah se hai?"""
    s = str(err or "").lower()
    return any(m in s for m in _PARSE_ERR_MARKERS)


def strip_tags(text) -> str:
    """Saare HTML tags hatao + entities ko padhne-layak banao.

    Formatting chali jaati hai, par TEXT poora bachta hai — user ko jawab milta hai.
    Kabhi exception nahi deta.
    """
    try:
        t = "" if text is None else str(text)
        t = _TAG_RE.sub("", t)
        for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                        ("&apos;", "'"), ("&#39;", "'"), ("&nbsp;", " ")):
            t = t.replace(ent, ch)
        # '&amp;' sabse last me — warna '&amp;lt;' double-decode ho jaata
        t = t.replace("&amp;", "&")
        return t.strip() or "(empty)"
    except Exception:                                            # noqa: BLE001
        return "(empty)"


def _try_repair(text: str) -> str:
    """safesend.repair_html — available ho to. Na ho to seedha strip."""
    try:
        from modules.core.safesend import repair_html
        fixed = repair_html(text)
        return fixed if (fixed and str(fixed).strip()) else strip_tags(text)
    except Exception:                                            # noqa: BLE001
        return strip_tags(text)


def _wrap_method(orig, name: str):
    """Ek Bot method par 'parse entities → dobara bhejo' net lagao."""
    # PTB ke send/edit methods ke signature se pata lagao ki text/caption
    # kaun-se positional index par aata hai — andaza nahi, asli signature.
    # Dhyaan do: signature me `self` bhi hota hai, par jab method ko
    # `orig(self, *args)` ki tarah call karte hain to `args` me self NAHI hota.
    # Isliye positional index = signature index - 1. (Yahi off-by-one pehle bug tha.)
    try:
        _params = list(inspect.signature(orig).parameters)
    except Exception:                                            # noqa: BLE001
        _params = []
    if _params and _params[0] in ("self", "cls"):
        _params = _params[1:]

    def _pos_of(fld):
        return _params.index(fld) if fld in _params else None

    _text_idx = _pos_of("text")
    _cap_idx = _pos_of("caption")

    def _locate(args, kwargs):
        """(field_name, value, positional?) — text/caption kwargs me ho ya positional me."""
        if kwargs.get("caption") is not None:
            return "caption", kwargs["caption"], False
        if kwargs.get("text") is not None:
            return "text", kwargs["text"], False
        if _cap_idx is not None and len(args) > _cap_idx and args[_cap_idx] is not None:
            return "caption", args[_cap_idx], True
        if _text_idx is not None and len(args) > _text_idx and args[_text_idx] is not None:
            return "text", args[_text_idx], True
        return None, None, False

    @functools.wraps(orig)
    async def _guarded(self, *args, **kwargs):
        # parse_mode na ho to koi HTML risk nahi — seedha aage
        pm = kwargs.get("parse_mode")
        if not pm or str(pm).upper() == "NONE":
            return await orig(self, *args, **kwargs)
        try:
            return await orig(self, *args, **kwargs)
        except Exception as e:                                   # noqa: BLE001
            if not _is_parse_error(e):
                raise
            _field, _body, _positional = _locate(args, kwargs)
            if _field is None:
                # text/caption dhoondh nahi paaye — chhedna risky, jaisa tha waisa
                raise

            def _call(new_body, drop_parse=False):
                _kw = dict(kwargs)
                if _positional:
                    _a = list(args)
                    _a[_cap_idx if _field == "caption" else _text_idx] = new_body
                    if drop_parse and "parse_mode" in _kw:
                        _kw["parse_mode"] = None
                    return orig(self, *_a, **_kw)
                _kw[_field] = new_body
                if drop_parse:
                    _kw["parse_mode"] = None
                return orig(self, *args, **_kw)

            # 1) HTML theek karke dobara (formatting bach jaati hai)
            try:
                _fixed = _try_repair(str(_body))
                if _fixed and _fixed != str(_body):
                    log.info("htmlnet: %s repair karke dobara bheja (%s)",
                             name, type(e).__name__)
                    return await _call(_fixed)
            except Exception as e2:                              # noqa: BLE001
                # repair wala text bhi reject hua — koi baat nahi, neeche plain jaayega
                log.debug("htmlnet: %s repair bhi reject (%s)", name, str(e2)[:80])
            # 2) ab tags hata kar plain — message kabhi gayab na ho
            log.warning("htmlnet: %s plain-text fallback (%s)", name, str(e)[:90])
            return await _call(strip_tags(str(_body)), drop_parse=True)

    _guarded.__ud_wrapped__ = True
    return _guarded


def patch_bot_html_safety(cls=None):
    """Bot class (ya koi bhi class) par HTML safety net lagao. Idempotent."""
    if cls is None:
        try:
            from telegram import Bot as cls
        except Exception:                                        # noqa: BLE001
            log.warning("htmlnet: telegram.Bot import nahi hua — net skip")
            return False
    if getattr(cls, _PATCHED_FLAG, False):
        return True
    n = 0
    for name in _TEXT_METHODS:
        orig = cls.__dict__.get(name)
        if orig is None or not callable(orig):
            continue
        if getattr(orig, "__ud_wrapped__", False):
            continue
        setattr(cls, name, _wrap_method(orig, name))
        n += 1
    setattr(cls, _PATCHED_FLAG, True)
    log.info("🧯 HTML safety net ON — %s send/edit methods guard hue", n)
    return n > 0
