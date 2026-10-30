# -*- coding: utf-8 -*-
"""HTML-safe message cutting (v54.3).

PROBLEM (live crash, Render log 2026-10-05 08:33 UTC):
    "Exception handling update: Can't parse entities: can't find end tag
     corresponding to start tag 'i'"

Wajah: hamare cards lambi HTML strings hain aur hum unhe limit par kaat dete
hain (`txt[:4000]`). Agar cut kisi line ke BEECH me gire jahan `<i>` khula ho
aur `</i>` aage ho, to message me **unclosed tag** reh jaata hai → Telegram
Bot API pura message reject kar deta hai → exception → user ko "Chhota sa
ghatna ho gaya" dikhta tha (aur credit bhi kat chuka hota tha).

FIX: `cut_html(text, limit)` —
  1. limit par kaato,
  2. cut ko peeche ki taraf **newline tak** le aao (hamare cards me har tag
     ek hi line me khulta aur band hota hai, isliye line-safe cut = tag-safe),
  3. koi adhoora `<` fragment ho to hatao,
  4. phir bhi koi tag khula reh gaya ho to **stack se band karo**.
"""
from __future__ import annotations

import re

__all__ = ["cut_html", "strip_html", "html_balanced"]

# Telegram HTML me allowed tags (bold/italic/etc). `<a href>` attributes ke
# saath bhi match hota hai.
_TAG_RE = re.compile(r"<(/?)(b|i|u|s|code|pre|tg-spoiler|a)(?:\s[^>]*)?>", re.I)


def cut_html(text, limit: int) -> str:
    """HTML ko limit tak kaato, bina tags tode."""
    if text is None:
        return ""
    t = str(text)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    # adhoora tag fragment (cut ke baad '<' aa gaya, '>' nahi) hatao
    lt = cut.rfind("<")
    if lt != -1 and ">" not in cut[lt:]:
        cut = cut[:lt]
    # khule tags band karo
    stack = []
    for m in _TAG_RE.finditer(cut):
        name = m.group(2).lower()
        if m.group(1):                      # closing tag
            if stack and stack[-1] == name:
                stack.pop()
        else:                               # opening tag
            stack.append(name)
    if stack:
        cut = cut + "".join(f"</{n}>" for n in reversed(stack))
    return cut


def strip_html(text) -> str:
    """Saare HTML tags hata do (plain-text fallback ke liye)."""
    if text is None:
        return ""
    return re.sub(r"</?[^>]+>", "", str(text))


def html_balanced(text) -> bool:
    """True agar saare tags balanced hain (tests ke liye)."""
    stack = []
    for m in _TAG_RE.finditer(str(text or "")):
        name = m.group(2).lower()
        if m.group(1):
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack
