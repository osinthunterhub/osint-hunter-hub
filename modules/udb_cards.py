# -*- coding: utf-8 -*-
"""
modules.udb_cards — v2.6: @utility_duniya_bot (bot-1) se port kiye gaye
tool cards + prompts (RC/CHALLAN, VEHICLE TO NUMBER, AADHAAR TO NUMBER,
BSEB RESULT CHECK). Bot-1 jaisa EXACT look.
"""
from html import escape as hesc
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from modules import userapi as uapi
from modules import bseb_result as bseb

from modules.cardstyle import box, field, sep, footer, subhead, ubold
from modules.gw_tools import echallan_section

PCARD_MID = "━━━━━━━━━━━━━━━━━━━━━━"
BRAND_LINK = '🔥 Powered by <a href="https://t.me/ANONYMOUS_XH">@ANONYMOUS_XH</a>'


def to_bold(text: str) -> str:
    return ubold(text)


PROMPT_DATA = {
    "numinfo": {
        "head": "📱 NUMBER INFO V2 ENGINE",
        "ask": "10 Digit Number bhejein:",
        "ex": [('7857843092', '10 digit ka mobile number')],
        "tip": '+91 ya 0 pehle lagane ki zaroorat nahi — seedha 10 digit bhejo',
        "foot": 'Circle · operator · owner card',
    },
    "familyinfo": {
        "head": "👪 FAMILY INFO",
        "ask": "12-digit Aadhaar number bhejein:",
        "ex": [('401635555849', '12-digit Aadhaar number')],
        "tip": 'Aadhaar hamesha masked rehta hai (XXXX-XXXX-1234) — poora kabhi nahi dikhta',
        "foot": 'Ration card · family members · eKYC',
    },
    "rc": {
        "head": "🚘 VEHICLE & CHALLAN",
        "ask": "Gaadi ka number bhejein (RC + Challan):",
        "ex": [('BR30AR0802', 'bina space ke gaadi number (jaise BR30AR0802)')],
        "tip": 'Space ya dash lagao to bhi chalega — naya engine RC + Challan dono laata hai',
        "foot": 'Owner · model · RTO · insurance · PUC · fitness · challan list',
    },
    "vehicletomobile": {
        "head": "📲 VEHICLE TO NUMBER",
        "ask": "Gaadi ka number bhejein (RC → Mobile):",
        "ex": [('KL41V3504', 'gaadi number — RC se mobile number')],
        "tip": 'RC number se owner ka mobile number milega',
        "foot": 'Mobile number from RC',
    },
    "aadhaar2pan": {
        "head": "🪪 AADHAAR TO PAN",
        "ask": "12-digit Aadhaar reference bhejo",
        "ex": [("293649586679",)],
    },
    "pantogst": {
        "head": "🏢 PAN TO GST",
        "ask": "PAN number bhejo (10 char)",
        "ex": [("AAAPL1234C",)],
    },
    "gst": {
        "head": "🧾 GST INFO",
        "ask": "GSTIN bhejo (15 char)",
        "ex": [("27AAPFU0939F1ZV",)],
    },
    "dl": {
        "head": "🪪 DRIVING LICENCE",
        "ask": "DL number + DOB bhejo (DLNUMBER DD-MM-YYYY)",
        "ex": [("UP25 20210027727 01-01-2001",)],
    },
    "fastag": {
        "head": "🏷️ FASTAG INFO",
        "ask": "Vehicle RC number bhejo",
        "ex": [("RJ14CV0002",)],
    },
    "imei": {
        "head": "📱 IMEI CHECK",
        "ask": "15-digit IMEI bhejo",
        "ex": [("490154203237518",)],
    },
    "aadhaar2num": {
        "head": "🪪 AADHAAR TO NUMBER",
        "ask": "12-digit Aadhaar number bhejein (Aadhaar → Mobile list):",
        "ex": [('401635555849', 'Aadhaar se mobile numbers')],
        "tip": 'Aadhaar se jude mobile numbers ki list',
        "foot": 'Mobile numbers linked to Aadhaar',
    },
    "result": {
        "head": "📋 RESULT CHECK — BSEB",
        "ask": "Pehle exam chuno (neeche button se):",
        "ex": [('22050 2600046', 'roll code + roll number')],
        "tip": 'Sirf Bihar Board (BSEB). Result season ke bahar board data hata deta hai',
        "foot": 'Naam · pita · school · subject-wise marks · division',
    },
}


def tool_prompt(action: str) -> str:
    """Tool ka prompt (bot-1 v102 plain format) — head + divider + ask + example."""
    d = PROMPT_DATA.get(action)
    if not d:
        return ""
    head = str(d.get("head") or "")
    if " " in head:
        _icon, _rest = head.split(" ", 1)
        L = [f"{_icon} <b>{_rest}</b>", PCARD_MID]
    else:
        L = [f"<b>{head}</b>", PCARD_MID]
    L.append("")
    L.append(f"🔗 <b>{hesc(str(d.get('ask') or ''))}</b>")
    ex = d.get("ex") or []
    if ex:
        L.append("")
        L.append(f"     <code>{hesc(str(ex[0][0]))}</code>")
    return "\n".join(L)


def pcard_sep() -> str:
    """Separator — pehle aur baad me khali line (card saaf-suthra dikhe)."""
    return "\n" + PCARD_MID + "\n"


def vehicle_rc_card(res: dict) -> str:
    """🚗 RC + CHALLAN report — owner signature design + E-CHALLAN (Parivahan) section."""
    res = res if isinstance(res, dict) else {}
    data = res.get("data") or {}
    if not data:
        data = res
    if not data.get("owner_name") and not data.get("registration_number") and res.get("fields"):
        L = [box("🚗", "RC + CHALLAN"), ""]
        for lbl, val in (res.get("fields") or []):
            L.append(field("🔹", str(lbl).lstrip("🔹 ").strip(), val))
        L.append(sep())
        L.append(footer(res.get("ms") or 0))
        return "".join([x for x in L if x])

    def _g(k, d=""):
        v = data.get(k)
        if v is None or v == "" or str(v).upper() == "NA":
            return d
        return str(v).strip()

    def _h(txt):
        return hesc(txt)

    reg = _g("registration_number") or _g("reg_no") or _g("term") or res.get("term") or ""
    owner = _g("owner_name") or _g("owner") or ""
    owner_father = _g("owner_father_name") or _g("father_name") or ""
    model = _g("model") or _g("maker_model") or _g("vehicle") or ""
    maker = _g("manufacturer") or _g("maker") or ""
    vclass = _g("vehicle_class") or ""
    fuel = _g("fuel_type") or ""
    city = _g("city") or ""
    rto = _g("rto") or _g("rto_code") or ""
    addr = _g("address") or ""
    phone = _g("mobile_number") or _g("mobile") or _g("phone") or ""
    reg_date = _g("registration_date") or _g("reg_date") or ""
    fitness = _g("fitness_upto") or ""
    ins_upto = _g("insurance_upto") or ""
    ins_comp = _g("insurance_company") or ""
    ins_policy = _g("insurance_policy_number") or _g("insurance_policy") or ""
    puc_upto = _g("pucc_upto") or _g("puc_number") or ""
    puc_no = _g("pucc_number") or ""
    engine = _g("engine_number") or _g("engine") or _g("engine_cc") or ""
    chassis = _g("chassis_number") or _g("chassis") or ""
    financer = _g("financer") or _g("financer_name") or ""
    rc_status = _g("rc_status") or "ACTIVE"
    tax = _g("tax_upto") or ""
    age = _g("vehicle_age") or ""
    seat = _g("seat_capacity") or ""
    variant = _g("variant") or ""
    color = _g("color") or ""
    cubic = _g("cubic_capacity") or ""
    norms = _g("norms_type") or ""
    manu_month = _g("manufacturing_month_year") or ""
    challans = data.get("challan_details") or []
    summary = data.get("challan_summary") or {}

    L = [box("🚗", "RC + CHALLAN REPORT"), ""]
    L.append(f"🔢 <b>Number:</b> <code>{_h(reg)}</code>\n\n")
    L.append(subhead("VEHICLE INFORMATION"))
    L.append(field("👤", "Owner", owner))
    L.append(field("👨", "Father", owner_father))
    if model or maker:
        L.append(field("🚘", "Model", f"{maker} {model}".strip()))
    L.append(field("🏷️", "Variant", variant))
    L.append(field("🏍️", "Class", vclass))
    L.append(field("⛽", "Fuel", fuel))
    L.append(field("🏙️", "City", city))
    L.append(field("📞", "Phone", phone))
    L.append(field("📍", "RTO", rto))
    L.append(field("🏠", "Address", str(addr)[:160]))
    L.append(sep())
    L.append(subhead("TECHNICAL & RC"))
    L.append(field("🆔", "RC Status", ("✅ " + rc_status) if "ACTIVE" in rc_status.upper() else rc_status))
    L.append(field("🎨", "Color", color))
    L.append(field("🔧", "Chassis", chassis))
    L.append(field("⚙️", "Engine", engine))
    L.append(field("🔩", "CC", cubic))
    L.append(field("💺", "Seats", seat))
    L.append(field("📏", "Norms", norms))
    L.append(field("📅", "Mfg", manu_month))
    L.append(field("📅", "Reg Date", reg_date))
    _ins = " ".join([x for x in [ins_comp, ins_policy, ins_upto] if x])[:180]
    L.append(field("🛡️", "Insurance", _ins))
    L.append(field("🌫️", "PUC", " ".join([x for x in [puc_no, puc_upto] if x])[:120]))
    L.append(field("🏦", "Finance", financer))
    L.append(field("💰", "Tax Upto", tax))
    L.append(field("📋", "Fitness Upto", fitness))
    L.append(field("⏳", "Age", age))
    L.append(sep())
    L.append(subhead("CHALLAN SUMMARY"))
    if summary:
        total = summary.get("total_challans") or (summary.get("challan_summary") or {}).get("total_challans") or ""
        amount = summary.get("total_amount") or (summary.get("challan_summary") or {}).get("total_amount") or ""
        if total:
            L.append(f"📊 <b>Total Challans:</b> {_h(str(total))}\n\n")
        if amount:
            L.append(f"💰 <b>Total Amount:</b> ₹{_h(str(amount))}\n\n")
        if not total and not amount:
            L.append("⚠️ Challan details temporarily live track status failed.\n\n")
    else:
        L.append("⚠️ Challan details temporarily live track status failed.\n\n")
    if challans:
        for ch in challans[:5]:
            cn = ch.get("challan_number") or ch.get("challan_no") or ""
            if not cn:
                continue
            L.append(f"🔹 <b>Challan:</b> <code>{_h(cn)}</code>\n")
            acc = ch.get("accused_name") or ""
            if acc:
                L.append(f"   👤 {_h(acc)}\n")
            amt = ch.get("amount") or ch.get("challan_amount") or ""
            if amt:
                try:
                    amt = f"{int(str(amt).replace(',', '')):,}"
                except Exception:
                    amt = str(amt)
                L.append(f"   💰 ₹{_h(str(amt))}\n")
            date = ch.get("challan_date") or ch.get("date") or ""
            if date:
                L.append(f"   📅 {_h(str(date))}\n")
            status = ch.get("challan_status") or ch.get("status") or ""
            if status:
                L.append(f"   ❌ {_h(str(status).upper())}\n")
            L.append("\n")
    # 📑 E-CHALLAN (Parivahan) — owner order: RC ke andar hi challan details
    L.append(echallan_section(res.get("e_challan") or {}))
    L.append(sep())
    ms = int(res.get("ms") or res.get("response_time") or 0)
    L.append(footer(ms, " · cached" if res.get("cached") else ""))
    return "".join([x for x in L if x])


def vehicletomobile_card(res: dict) -> str:
    """📲 VEHICLE TO NUMBER — owner signature design."""
    res = res if isinstance(res, dict) else {}
    data = res.get("data") or res
    reg = str(data.get("registration_number") or data.get("reg_no") or res.get("term") or "").upper()
    mob = str(data.get("mobile_number") or data.get("mobile") or data.get("phone") or "").strip()
    owner = str(data.get("owner_name") or data.get("owner") or "").strip()
    model = str(data.get("model") or data.get("vehicle") or "").strip()
    L = [box("📲", "VEHICLE TO NUMBER"), ""]
    L.append(f"🔢 <b>RC:</b> <code>{hesc(reg)}</code>\n\n")
    if mob:
        L.append(f"📱 <b>Mobile:</b> <code>{hesc(mob)}</code>\n\n")
    else:
        L.append("❌ <b>Mobile DB me nahi hai</b> (sarkari record me mobile nahi)\n\n")
    L.append(field("👤", "Owner", owner))
    L.append(field("🚘", "Model", model))
    L.append(field("🏢", "RTO", data.get("rto")))
    L.append(sep())
    L.append(footer(res.get("ms") or 0))
    return "".join([x for x in L if x])


def aadhaar2num_card(res: dict) -> str:
    """🪪 AADHAAR TO NUMBER — owner signature design; Aadhaar hamesha masked."""
    import re
    def _mask_aadhaar(value):
        digits = re.sub(r"\D", "", str(value or ""))
        return ("XXXX-XXXX-" + digits[-4:]) if len(digits) >= 4 else "XXXX-XXXX-XXXX"
    res = res if isinstance(res, dict) else {}
    data = res.get("data") or {}
    term = _mask_aadhaar(res.get("term") or data.get("aadhaar") or data.get("aadharNumber"))
    records = data.get("data") or data.get("records") or []
    if isinstance(data, list):
        records = data
    count = data.get("count") or (len(records) if isinstance(records, list) else 0)
    L = [box("🪪", "AADHAAR TO NUMBER"), ""]
    L.append(f"🔒 <b>Aadhaar:</b> <code>{hesc(term)}</code>\n\n")
    L.append(f"📊 <b>Total Records:</b> {str(count)}\n\n")
    for i, rec in enumerate(records[:10], 1):
        if not isinstance(rec, dict):
            continue
        name = rec.get("name") or rec.get("fullName") or "—"
        L.append(f"👤 <b>{i}. {hesc(str(name))}</b>\n\n")
        father = rec.get("fathersName") or rec.get("father") or ""
        if father:
            L.append(f"   👨 Father: {hesc(str(father))}\n")
        phone = rec.get("phoneNumber") or rec.get("mobile") or rec.get("phone") or ""
        if phone:
            L.append(f"   📱 Phone: <code>{hesc(str(phone))}</code>\n")
        other = rec.get("otherNumber") or rec.get("alt") or ""
        if other:
            L.append(f"   📱 Alt: <code>{hesc(str(other))}</code>\n")
        aad = _mask_aadhaar(rec.get("aadharNumber") or rec.get("aadhaar") or term)
        L.append(f"   🆔 Aadhaar: <code>{hesc(str(aad))}</code>\n")
        addr = rec.get("address") or rec.get("addr") or ""
        if addr:
            L.append(f"   🏠 {hesc(str(addr)[:160])}\n")
        L.append("\n")
    if isinstance(records, list) and count and int(count) > len(records):
        L.append(f"<i>…aur {int(count) - len(records)} aur</i>\n\n")
    L.append(sep())
    L.append(footer(res.get("ms") or 0))
    return "".join([x for x in L if x])


def userapi_card(res: dict, kind: str) -> str:
    """Tool card dispatcher (rc/v2m/a2n ke dedicated cards + generic fallback)."""
    res = res if isinstance(res, dict) else {}
    if kind == "rc":
        try:
            return vehicle_rc_card(res)
        except Exception:
            pass
    if kind == "vehicletomobile":
        try:
            return vehicletomobile_card(res)
        except Exception:
            pass
    if kind == "aadhaar2num":
        try:
            return aadhaar2num_card(res)
        except Exception:
            pass
    cfg = uapi.TOOLS.get(kind, {"icon": "🔎", "label": "INFO"})
    L = [box(cfg.get("icon", "🔎"), cfg.get("label", "INFO")), ""]
    L.append(f"🔍 <b>Query:</b> <code>{hesc(str(res.get('term') or ''))}</code>\n\n")
    for lbl, val in (res.get("fields") or []):
        L.append(f"🔹 {hesc(str(lbl))} <b>:</b> {hesc(str(val))}\n\n")
    extra = res.get("extra") or {}
    if extra:
        L.append(subhead("BAAKI DETAILS"))
        for k, v in extra.items():
            L.append(f"   • <b>{hesc(str(k))}:</b> {hesc(str(v))}\n")
        L.append("\n")
    _rawt = str(res.get("raw_text") or "").strip()
    if _rawt:
        L.append(f"<pre>{hesc(_rawt[:1500])}</pre>\n\n")
    L.append(sep())
    L.append(footer(res.get("ms") or 0, "  ·  <i>cached</i>" if res.get("cached") else ""))
    return "".join([x for x in L if x is not None])


def result_card(st: dict, exam_label: str, year) -> str:
    """📋 RESULT CHECK — BSEB marksheet card (owner signature design)."""
    st = st if isinstance(st, dict) else {}
    L = [box("📋", f"RESULT CHECK — {str(exam_label)}"), ""]
    L.append(f"🎓 <b>Year:</b> {hesc(str(year))}\n\n")
    for icon, lbl, key in (("👤", "Naam", "name"), ("👨", "Pita", "father"),
                           ("👩", "Mata", "mother"), ("🔢", "Roll Code", "roll_code"),
                           ("🎫", "Roll No", "roll_no"), ("🪪", "Reg. No", "reg_no"),
                           ("🏫", "School", "school"), ("📅", "DOB", "dob"),
                           ("⚧", "Gender", "gender"), ("📚", "Faculty", "faculty")):
        L.append(field(icon, lbl, st.get(key)))

    subs = st.get("subjects") or []
    if subs:
        L.append(subhead("SUBJECT-WISE MARKS"))
        for s_ in subs[:15]:
            try:
                L.append(bseb._sub_line(s_) + "\n\n")
            except Exception:                                # noqa: BLE001
                nm = hesc(str(s_.get("name") or s_.get("subject") or "—"))
                L.append(f"   • {nm}: {hesc(str(s_.get('total') or ''))}\n\n")

    def _n(v):
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except Exception:                                    # noqa: BLE001
            return str(v)

    tot = st.get("total")
    if tot not in (None, ""):
        full = st.get("full_marks") or st.get("out_of")
        L.append(f"🏆 <b>Total:</b> {_n(tot)}" + (f" / {_n(full)}" if full else "") + "\n\n")
    for icon, lbl, key in (("📊", "Percent", "percent"), ("🥇", "Division", "division"),
                           ("✅", "Result", "result_status")):
        L.append(field(icon, lbl, st.get(key)))
    if st.get("is_topper"):
        L.append("🌟 <b>TOPPER!</b> 🎉\n\n")

    try:
        L.append(str(bseb.MEDIA_NOTE) + "\n\n")
    except Exception:                                        # noqa: BLE001
        pass
    L.append(sep())
    L.append(footer(0))
    return "".join([x for x in L if x])


def result_pick_text() -> str:
    """📋 RESULT CHECK ka step 1 — kaunsa exam?"""
    return ("📋 <b>RESULT CHECK — BIHAR BOARD</b>\n"
            f"{PCARD_MID}\n\n"
            "Pehle batao <b>kaunsa exam</b> hai 👇\n\n"
            f"<i>Saal: {bseb.CURRENT_YEAR}</i>")


def result_pick_kb():
    """Exam chunne ke inline buttons (bseb.EXAMS se khud bante hain)."""
    rows = []
    for key, ex in bseb.EXAMS.items():
        rows.append([InlineKeyboardButton(str(ex.get("btn") or ex.get("short") or key),
                                          callback_data=f"rs:{key}")])
    return InlineKeyboardMarkup(rows)


def result_ask_roll_text(exam_key: str) -> str:
    """Step 2 — roll code + roll number maango."""
    ex = bseb.EXAMS.get(exam_key, {})
    return (f"📋 <b>{hesc(str(ex.get('label') or 'BSEB'))} — {bseb.CURRENT_YEAR}</b>\n"
            f"{PCARD_MID}\n\n"
            "Ab bhejo: <b>Roll Code + Roll Number</b>\n\n"
            "📌 <b>Jaise:</b> <code>22050 2600046</code>\n"
            "<i>(pehle roll code, space, phir roll number)</i>")


_TYPING_STOP: dict = {}
