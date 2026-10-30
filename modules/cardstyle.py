# -*- coding: utf-8 -*-
"""
🎴 CARD STYLE (owner signature design v3)
============================================
Boss ka order (23 Oct): sab OUTPUTS ek hi design mein —

    ╔══════════════════════════════════════╗
    ║  🚗 𝐑𝐂 + 𝐂𝐇𝐀𝐋𝐋𝐀𝐍 𝐑𝐄𝐏𝐎𝐑𝐓              ║
    ╚══════════════════════════════════════╝

    🔹 <b>Label:</b> value        ← har field ke beech MEIN SPACE

    ━━━━━━━━━━━━━━━━━━━━━━━━━━

    ⚡ <b>Response:</b> 123ms
    🔥 <b>Powered by @ANONYMOUS_XH</b>

Title = mathematical bold ('𝐃𝐀𝐑𝐊' jaisa) + box fixed-width.
Sirf stdlib — zero dependency, zero RAM.
"""
from __future__ import annotations

import html as _html

BOX_W = 36


def ubold(text: str) -> str:
    """Plain text → mathematical bold (𝐀-𝐙, 𝐚-𝐳, 𝟎-𝟗) — '𝐃𝐀𝐑𝐊' style."""
    out = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(0x1D400 + o - 65))
        elif 97 <= o <= 122:
            out.append(chr(0x1D41A + o - 97))
        elif 48 <= o <= 57:
            out.append(chr(0x1D7CE + o - 48))
        else:
            out.append(ch)
    return "".join(out)


def _vis(s: str) -> int:
    """Approximate display width (emoji=2, sab kuch 1)."""
    w = 0
    for ch in s:
        o = ord(ch)
        if o >= 0x1F000 or o in (0x2705, 0x274C, 0x2B50):
            w += 2
        else:
            w += 1
    return w


def box(icon: str, title: str) -> str:
    """Box header — icon + math-bold title, fixed 36-wide walls."""
    inner = f"  {icon} {ubold(title)}"
    pad = BOX_W - _vis(inner)
    if pad < 1:
        pad = 1
    bar = "═" * BOX_W
    return f"╔{bar}╗\n║{inner}{' ' * pad}║\n╚{bar}╝\n\n"


def field(icon: str, label: "str", value) -> str:
    """🔹 Field line + neeche SPACE (empty/na/null skip)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("na", "n/a", "none", "null", "-"):
        return ""
    esc = _html.escape(s[:400])
    return f"{icon} <b>{label}:</b> {esc}\n\n"


def subhead(text: str) -> str:
    """Section heading inside card."""
    return f"<b>{ubold(text)}</b>\n\n"


def sep() -> str:
    return "━" * 26 + "\n\n"


def footer(ms, extra: str = "") -> str:
    """⚡ Response + 🔥 brand (credits line bot side se judta hai)."""
    try:
        ms = int(ms or 0)
    except Exception:
        ms = 0
    return f"⚡ <b>Response:</b> {ms}ms{extra}\n🔥 <b>Powered by @ANONYMOUS_XH</b>"


def _build_from_bold():
    """ubold ka ULTA — mathematical bold -> ASCII (button routing ke liye)."""
    m = {}
    for i in range(26):
        m[0x1D400 + i] = ord("A") + i      # 𝐀-𝐙 -> A-Z
        m[0x1D41A + i] = ord("a") + i      # 𝐚-𝐳 -> a-z
    for i in range(10):
        m[0x1D7CE + i] = ord("0") + i      # 𝟎-𝟗 -> 0-9
    return str.maketrans(m)


FROM_BOLD = _build_from_bold()


def from_bold(s: str) -> str:
    """Bold label text -> plain ('𝐍𝐔𝐌𝐁𝐄𝐑 𝐈𝐍𝐅𝐎' -> 'NUMBER INFO')."""
    return (s or "").translate(FROM_BOLD)
