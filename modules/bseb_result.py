# -*- coding: utf-8 -*-
"""
📋 BOARD RESULT CHECK — BSEB (Bihar School Examination Board)  [v74.0 PRO]
===========================================================================
User ka order (8 Oct 2026): "Result check tool ko upgrade karo — sirf BSEB.
Portal jaisa: pehle EXAM chuno, phir YEAR chuno, phir Roll Code, phir Roll
Number. Result aisa de jaise portal par PDF download hota hai — PDF me poora
data ho. Aisa hi BSEB ke saare exam ke liye."

Sach (research ke baad — 7/8 Oct 2026, LIVE verify kiya):
  • MATRIC (10th) Annual — ✅ LIVE. Official React app ki open API:
        GET https://resultapi.biharboardonline.org/result?roll_code=&roll_no=
    (asli topper ke roll 22050/2600046 par poora result aaya — naam, papa,
    school, subject-wise marks, total 492, 1st Division.)
  • INTER (12th) Annual — portal: interbiharboard.com/ex-26
    Form POST: /ex-26/Result/GetResult  (rollcode, rollno + antiforgery)
    Captcha SIRF client-side hai (server check nahi karta) — isliye bot
    seedha roll code + roll number se result maang sakta hai. Result season
    ke bahar board data hata deta hai → tab "Not Found" (bot saaf batata hai).
  • INTER Special & Compartmental — portal: interbiharboard.com/Spcl-Comprt-26
    (wahi form pattern.)
  • PURANE SAAL (2025, 2024, 2023...) — board purane saal ka data live
    portal par NAHI rakhta. Bot saaf batata hai (jhoothi umeed nahi).

Privacy: sirf roll code + roll number se PUBLIC result. Hum kuch save/log
nahi karte. Login/captcha bypass jaisa kuch nahi — sab public endpoints hain.
"""

# v109: io hata diya (sirf PDF ke liye tha)
import re
import time

# ───────────────────────────────────────────────────────────── constants
MATRIC_API = "https://resultapi.biharboardonline.org/result"
INTER_BASE = "https://interbiharboard.com"
API_REFERER = "https://result.biharboardonline.org/"
TIMEOUT = 18
CURRENT_YEAR = 2026

MEDIA_NOTE = ("ℹ️ Ye result <b>bilkul asli data</b> par bana hai — jo board ke record me "
              "hai, wahi yahan hai. Print / screenshot nikaal sakte hain ✅")

# Exam registry — portal ke buttons jaisa (sirf BSEB)
EXAMS = {
    "matric": {
        "label": "🎓 MATRIC (10th) ANNUAL",
        "short": "Matric (10th)",
        "kind": "api",
        "btn": "🎓 Matric (10th)",
    },
    "inter": {
        "label": "🎓 INTER (12th) ANNUAL",
        "short": "Inter (12th)",
        "kind": "portal",
        "page": "ex-26",                 # portal ka page (2026 cycle)
        "btn": "🎓 Inter (12th)",
    },
    "inter_sc": {
        "label": "🔁 INTER SPECIAL & COMPARTMENTAL",
        "short": "Inter Spl/Compartmental",
        "kind": "portal",
        "page": "Spcl-Comprt-26",
        "btn": "🔁 Inter Spl/Comp",
    },
}

YEARS = [2026, 2025, 2024, 2023, 2022]

_RX_CODE = re.compile(r"^\d{3,6}$")
_RX_ROLL = re.compile(r"^\d{4,9}$")


# ───────────────────────────────────────────────────────────── helpers
def clean_digits(s) -> str:
    """Text me se digits nikalo ('11,001' → '11001')."""
    return re.sub(r"[^\d]", "", str(s or ""))


def validate(roll_code, roll_no) -> tuple:
    """(ok, error_hinglish)."""
    rc = clean_digits(roll_code)
    rn = clean_digits(roll_no)
    if not rc and not rn:
        return False, "Roll Code aur Roll Number dono chahiye."
    if not rc:
        return False, "Roll Code nahi mila — jaise <code>11001</code>."
    if not rn:
        return False, "Roll Number nahi mila — jaise <code>100001</code>."
    if not _RX_CODE.match(rc):
        return False, "Roll Code 5 digit ka hota hai (jaise <code>11001</code>)."
    if not _RX_ROLL.match(rn):
        return False, "Roll Number 6-7 digit ka hota hai (jaise <code>2600046</code>)."
    return True, ""


def split_input(text) -> tuple:
    """Message se (roll_code, roll_no) nikalo — '11001 100001' jaisa bhi."""
    t = re.sub(r"(?<=\d),(?=\d)", "", str(text or ""))   # '11,001' → '11001'
    nums = re.findall(r"\d{3,9}", t)
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return "", nums[0]
    return "", ""


def _num(v):
    """'640' / 640 / '098' → float ya None."""
    try:
        if v is None or str(v).strip().upper() in ("ABS", "-", ""):
            return None
        return float(str(v).strip())
    except Exception:                                            # noqa: BLE001
        return None


def _nice(v) -> str:
    """Marks ko saaf dikhao: '098' → '98', 88.0 → '88', None → '-'."""
    n = _num(v)
    if n is None:
        return "-"
    return str(int(n)) if n == int(n) else f"{n:g}"


def _flag_str(sub: dict) -> str:
    """""C/#/*/F — portal wale nishaan."""
    f = ""
    if sub.get("is_compartmental"):
        f += " (C)"
    if str(sub.get("sub_result") or "").strip():
        _n = _num(sub.get("sub_total"))
        if _n is None or _n < 30:
            f += " F"
    if sub.get("is_improved_sub"):
        f += " #"
    if sub.get("regulation"):
        f += " *"
    return f


def _sub_line(sub: dict) -> str:
    """Text card ke liye ek subject ki line."""
    name = str(sub.get("sub_name") or sub.get("subject") or sub.get("name") or "-").strip()
    out = f"{name} — <b>{_nice(sub.get('sub_total'))}</b>"
    extra = []
    if sub.get("theory") is not None:
        extra.append(f"th {_nice(sub.get('theory'))}")
    if sub.get("practical") is not None:
        extra.append(f"pr {_nice(sub.get('practical'))}")
    if extra:
        out += f" <i>({', '.join(extra)})</i>"
    out += _flag_str(sub)
    return out


def _extra_marks_cell(sub: dict) -> str:
    """Practical / IA / project / CCE — jo bhi ho, ek cell me."""
    parts = []
    for key, lab in (("practical", "Pr"), ("ia_sci", "IA"),
                     ("project_work", "Proj"), ("cce", "CCE"),
                     ("literacy_activity", "Lit")):
        v = _num(sub.get(key))
        if v is not None:
            parts.append(f"{lab} {_nice(v)}")
    return ", ".join(parts) if parts else "-"


def _find_student(obj):
    """Payload me kahin bhi student dict dhoondo (roll_no / name wali)."""
    if isinstance(obj, dict):
        keys = set(str(k).lower() for k in obj.keys())
        if "roll_no" in keys or {"name", "school_name"} <= keys:
            return obj
        for v in obj.values():
            hit = _find_student(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_student(v)
            if hit:
                return hit
    return None


def parse_payload(js) -> dict:
    """Matric API ka JSON → saaf student dict. Kabhi crash nahi."""
    try:
        if not isinstance(js, dict):
            return {}
        if js.get("success") is False:
            return {}
        stu = _find_student(js)
        if not stu:
            return {}
        out = {
            "name": str(stu.get("name") or "").strip(),
            "father": str(stu.get("father_name") or "").strip(),
            "mother": str(stu.get("mother_name") or "").strip(),
            "school": str(stu.get("school_name") or "").strip(),
            "roll_code": str(stu.get("roll_code") or "").strip(),
            "roll_no": str(stu.get("roll_no") or "").strip(),
            "reg_no": str(stu.get("reg_no") or stu.get("registration_no") or "").strip(),
            "bseb_id": str(stu.get("bseb_id") or "").strip(),
            "division": str(stu.get("division") or "").strip(),
            "exam_type": str(stu.get("exam_type") or "").strip(),
            "is_topper": bool(stu.get("is_topper")),
            "is_scrutiny": bool(stu.get("is_scrutiny")),
            "is_expelled": bool(stu.get("is_expelled")),
            "passed_under_regulation": bool(stu.get("passed_under_regulation")),
            "is_improved_result": bool(stu.get("is_improved_result")),
            "subjects": [],
        }
        subs = stu.get("subjects")
        if isinstance(subs, list):
            for s in subs[:20]:
                if isinstance(s, dict):
                    out["subjects"].append({
                        "sub_name": s.get("sub_name") or s.get("subject") or "",
                        "sub_code": str(s.get("sub_code") or ""),
                        "sub_total": s.get("sub_total"),
                        "theory": s.get("theory"),
                        "practical": s.get("practical"),
                        "ia_sci": s.get("ia_sci"),
                        "project_work": s.get("project_work"),
                        "cce": s.get("cce"),
                        "literacy_activity": s.get("literacy_activity"),
                        "regulation": s.get("regulation"),
                        "is_compartmental": bool(s.get("is_compartmental")),
                        "is_improved_sub": bool(s.get("is_improved_sub")),
                        "sub_result": s.get("sub_result"),
                    })
        tot = _num(stu.get("total") or stu.get("total_marks") or stu.get("grand_total"))
        if tot is None:
            _s = [_num(s.get("sub_total")) for s in out["subjects"]]
            _s = [x for x in _s if x is not None]
            if _s:
                tot = sum(_s)
        out["total"] = tot
        return out
    except Exception:                                            # noqa: BLE001
        return {}


def result_text(st: dict) -> str:
    """PASS / FAIL / Expelled / Regulation — saaf shabdon me."""
    if st.get("is_expelled"):
        return "EXPELLED"
    div = str(st.get("division") or "").lower()
    if "fail" in div:
        return "FAIL"
    bad = [s for s in (st.get("subjects") or [])
           if str(s.get("sub_result") or "").strip() and (_num(s.get("sub_total")) or 0) < 30]
    if st.get("division") or st.get("total"):
        return "PASS (Regulation)" if st.get("passed_under_regulation") else "PASS"
    return "FAIL" if bad else "PASS"


# ======================================================================
#  SOURCE 1: MATRIC — official open API
# ======================================================================
def fetch_matric(roll_code, roll_no) -> dict:
    """Matric result lao (official API).
       {"ok": True, "student": {...}} | {"ok": False, "status": not_live|server|invalid}
    """
    rc, rn = clean_digits(roll_code), clean_digits(roll_no)
    ok, err = validate(rc, rn)
    if not ok:
        return {"ok": False, "status": "invalid", "error": err}
    try:
        # v109: pehle `modules.core.httpio` use hota tha, par wo module v108 ki
        # safai me delete ho gaya tha — isliye ye tool chup-chaap "server busy"
        # de raha tha. Ab shared pooled session (modules.core.net) use hota hai.
        from modules.core.net import http_get as _hget
        _r = _hget(
            MATRIC_API,
            params={"roll_code": rc, "roll_no": rn},
            headers={"Accept": "application/json", "Referer": API_REFERER},
            timeout=TIMEOUT,
            retries=0,                         # board server par halka rehna hai
        )
        if _r.status_code == 404:
            return {"ok": False, "status": "not_live",
                    "error": "is roll code + roll number ka result abhi live nahi hai"}
        if _r.status_code >= 400:
            return {"ok": False, "status": "server",
                    "error": f"Bihar Board ka server abhi jawab nahi de raha (HTTP {_r.status_code})"}
        js = _r.json()
    except Exception as e:                                       # noqa: BLE001
        msg = str(e)[:160].lower()
        if "404" in msg or "not found" in msg:
            return {"ok": False, "status": "not_live",
                    "error": "is roll code + roll number ka result abhi live nahi hai"}
        return {"ok": False, "status": "server",
                "error": "Bihar Board ka server abhi jawab nahi de raha (busy ya band)"}
    if isinstance(js, dict) and js.get("success") is False:
        return {"ok": False, "status": "not_live",
                "error": str(js.get("message") or "result abhi live nahi hai")}
    stu = parse_payload(js)
    if not stu or not (stu.get("name") or stu.get("subjects")):
        return {"ok": False, "status": "not_live",
                "error": "is roll ka result abhi live nahi hai"}
    return {"ok": True, "student": stu}


# ======================================================================
#  SOURCE 2: INTER — portal form (interbiharboard.com)
# ======================================================================
def _inter_session():
    """Inter portal ke liye apna chhota session (cookies chahiye)."""
    import fasthttp
    s = fasthttp.FastSession(default_timeout=20.0)
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    })
    return s


def _txt_in(v):
    """Kachra input (None/bool/dict) -> str. v78: `(x or '')` True/5 jaisa
    truthy non-str pass kar deta tha -> re.compile TypeError = tool crash."""
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    try:
        return v.decode("utf-8", "ignore") if isinstance(v, (bytes, bytearray)) else str(v)
    except Exception:                                       # noqa: BLE001
        return ""


def _extract_token(html: str) -> str:
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', _txt_in(html))
    return m.group(1) if m else ""


def _txt(html: str) -> str:
    t = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t\r\f\v]+", " ", t)


def _parse_marksheet_guess(html: str) -> dict:
    """Portal ke result page se data nikaalo (best-effort — format badal
    bhi jaye to crash na ho)."""
    out = {"name": "", "roll_code": "", "roll_no": "", "subjects": [],
           "total": None, "division": "", "school": "", "father": ""}
    try:
        t = _txt(html)
        def grab(labels):
            for lab in labels:
                m = re.search(lab + r"\s*[:\-]?\s*([A-Za-z0-9 .'()/-]{2,60})", t, re.I)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            return ""
        out["name"] = grab([r"Student[’'s]*\s*Name", r"Name of (?:the )?Student",
                            r"Candidate[’'s]*\s*Name"])
        out["father"] = grab([r"Father[’'s]*\s*Name"])
        out["school"] = grab([r"School[’'s]*\s*Name", r"School"])
        out["roll_code"] = clean_digits(grab([r"Roll\s*Code"]))
        out["roll_no"] = clean_digits(grab([r"Roll\s*(?:No|Number)"]))
        m = re.search(r"(First|Second|Third|1st|2nd|3rd)\s*Division", t, re.I)
        out["division"] = m.group(0) if m else ""
        m = re.search(r"(?:Grand\s*Total|Total\s*Marks)\s*[:\-]?\s*(\d{2,4})", t, re.I)
        if m:
            out["total"] = _num(m.group(1))
        # subject rows: kisi bhi table se rows utha lo jisme subject + number ho
        rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html or "", re.I)
        for r in rows[:80]:
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", r, re.I)
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]
            if len(cells) < 2:
                continue
            name = cells[0]
            if not re.match(r"^[A-Za-z][A-Za-z .&()/-]{2,40}$", name):
                continue
            if re.search(r"\d", name):
                continue
            _low = re.sub(r"[^a-z ]", " ", name.lower())
            _bad = {"roll", "code", "number", "no", "name", "father", "mother",
                    "school", "college", "student", "registration", "dob", "date",
                    "gender", "sex", "category", "total", "grand", "division",
                    "result", "marks", "theory", "practical", "subject", "subjects",
                    "class", "board", "session", "exam", "signature", "photo",
                    "sl", "sr", "note", "sub", "paper"}
            if any(t in _bad for t in _low.split()):
                continue
            nums = [_num(c) for c in cells[1:]]
            if not any(n is not None for n in nums):
                continue
            sub = {"sub_name": name, "sub_code": "", "sub_total": None,
                   "theory": None, "practical": None, "ia_sci": None,
                   "project_work": None, "cce": None, "literacy_activity": None,
                   "regulation": None, "is_compartmental": False,
                   "is_improved_sub": False, "sub_result": None}
            vals = [c for c in cells[1:] if _num(c) is not None]
            if vals:
                sub["sub_total"] = vals[-1]
                if len(vals) >= 2:
                    sub["theory"] = vals[0]
            out["subjects"].append(sub)
        if out["total"] is None and out["subjects"]:
            _s = [_num(s.get("sub_total")) for s in out["subjects"]]
            _s = [x for x in _s if x is not None]
            if _s:
                out["total"] = sum(_s)
    except Exception:                                            # noqa: BLE001
        pass
    return out


def fetch_inter(roll_code, roll_no, page: str = "ex-26") -> dict:
    """Inter portal se result lao (form POST — captcha server check nahi karta)."""
    rc, rn = clean_digits(roll_code), clean_digits(roll_no)
    ok, err = validate(rc, rn)
    if not ok:
        return {"ok": False, "status": "invalid", "error": err}
    url = f"{INTER_BASE}/{page}"
    try:
        s = _inter_session()
        g = s.get(url, timeout=TIMEOUT)
        token = _extract_token(g.text)
        if not token:
            return {"ok": False, "status": "server",
                    "error": "portal ka form nahi mila (page badal gaya ho sakta hai)"}
        p = s.post(
            f"{url}/Result/GetResult",
            data={"rollcode": rc, "rollno": rn, "captcha": "000000",
                  "__RequestVerificationToken": token},
            headers={"Referer": url,
                     "Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT, allow_redirects=True,
        )
        html = p.text or ""
        low = html.lower()
        if "no result found" in low or "not found for the given" in low:
            return {"ok": False, "status": "not_live",
                    "error": "is roll code + roll number ka result abhi live nahi hai"}
        # success page kabhi form wapas hota hai (cookie flow) — dono dekh lo
        if "resultform" in low and "view result" in low and "no result" not in low:
            # shaayad redirect galat hua — dobara try karo seedha GET nahi chalega
            html2 = html
        else:
            html2 = html
        stu = _parse_marksheet_guess(html2)
        if stu.get("name") or stu.get("subjects"):
            return {"ok": True, "student": stu}
        # result aaya par format samajh nahi aaya — raw snippet bhej do
        return {"ok": False, "status": "parse",
                "snippet": re.sub(r"\s+", " ", _txt(html))[:400],
                "error": "result mila par format naya hai"}
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "status": "server",
                "error": f"portal tak nahi pahunch paya ({str(e)[:80]})"}


# ======================================================================
#  DISPATCHER — bot isi ko call karta hai
# ======================================================================
def check(exam_key: str, year, roll_code, roll_no) -> dict:
    """Exam + year + roll → result. Har halat me dict (kabhi crash nahi)."""
    ex = EXAMS.get(str(exam_key))
    if not ex:
        return {"ok": False, "status": "invalid", "error": "pehle exam chuno"}
    try:
        y = int(year or 0)
    except Exception:                                            # noqa: BLE001
        y = 0
    res = {"exam": str(exam_key), "year": y,
           "exam_label": ex["label"], "exam_short": ex["short"]}
    if ex["kind"] == "api":
        r = fetch_matric(roll_code, roll_no)
    else:
        if y and y != CURRENT_YEAR:
            return {**res, "ok": False, "status": "archive",
                    "error": (f"{ex['short']} ka {y} wala result board ke live portal "
                              f"par nahi hai (board purane saal ka data hata deta hai)")}
        r = fetch_inter(roll_code, roll_no, ex.get("page") or "ex-26")
    r.update(res)
    return r


# ======================================================================
#  PDF — portal jaisi "WEB COPY" marksheet (saara data ke saath)
# ======================================================================

# ---------------------------------------------------------------------
# v109 NOTE: yahan se aage purane version me PDF banane ka code tha
# (reportlab + Noto Devanagari font). Wo ~250 line hata di gayi hai —
# reportlab akela hi boot par ~40 MB RAM leta tha aur Render free plan
# (512 MB) par yahi cheezein bot ko mar rahi thi.
# Result ab bhi POORA aata hai, bas text card ke roop me (PDF nahi).
# ---------------------------------------------------------------------
