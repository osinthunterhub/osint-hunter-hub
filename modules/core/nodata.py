# -*- coding: utf-8 -*-
"""
modules.core.nodata — SAB TOOLS KE LIYE EK HI "NO DATA FOUND" MESSAGE (v124)

Owner ne kaha:
    "jab kbhi bhi database mein data n ho koi bhi tools ka tum usme
     saaf saaf likh dena NO DATA FOUND, bs yhi msg aisa hi"

Pehle har tool apna alag message bolta tha —
    "❌ Is number ka record number API me nahi mila."
    "❌ Is Aadhaar par family record nahi mila."
    "<code>X</code> ka koi record nahi mila."
— isse user confuse hota tha ki kahin bot hi to kharab nahi hai.

Ab SAB jagah bilkul ek jaisa message jata hai.

⚠️ ZAROORI FARQ (ye do cheezein alag hain, inhe mat milao):

  1. NO DATA FOUND  → input SAHI tha, par database me record nahi hai.
                      Yahi file wahi message deti hai.

  2. FORMAT GALAT   → input hi galat tha (jaise 11-digit Aadhaar, ya
                      galat checksum). Uska message ALAG rehna chahiye,
                      warna user ko lagega "database me nahi hai",
                      jabki asli baat ye hai ki usne number galat likha.
                      Uske liye neeche bad_input() hai.
"""

from __future__ import annotations

__all__ = ["NO_DATA_TITLE", "no_data", "bad_input"]

NO_DATA_TITLE = "❌ <b>Data not found in any database.</b>"


def _esc(v) -> str:
    s = str(v or "").strip()
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def no_data(term="", what="") -> str:
    """
    Database me record nahi mila — har tool ka EK HI message.

    term = user ne jo bheja tha (dikhane ke liye, optional)
    what = "number" / "Aadhaar" ... (optional)
    """
    # v127.13 (USER ORDER 18 Oct): fail par SIRF ye ek line — user ka
    # bheja hua data (term) bhi echo nahi karna.
    return "❌ <b>Data not found in any database.</b>"


def bad_input(msg: str) -> str:
    """
    Input hi galat tha (format/checksum) — ye NO DATA FOUND se ALAG hai.
    User ko saaf pata chale ki galti kahan hui.
    """
    return "⚠️ <b>GALAT INPUT</b>\n\n" + str(msg or "").strip()
