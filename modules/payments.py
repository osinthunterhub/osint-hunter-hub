"""
Payments + Premium Plans - Same as utility-duniya-bot
UPI: kumaranil98787@axl
"""

import os
import urllib.parse
import html

UPI_ID = os.getenv("UPI_ID", "kumaranil98787@axl").strip()
UPI_NAME = os.getenv("UPI_NAME", "OSINT Hunter Hub").strip()
FREE_CREDITS = int(os.getenv("FREE_CREDITS", "25"))

# Premium plans - same as utility bot: 30d 49, 60d 89, 90d 120, 365d 299
PLANS = {
    "30d": {"key": "30d", "name": "30 Days Premium", "days": 30, "amount": 49, "credits": 0, "desc": "1 Month Unlimited"},
    "60d": {"key": "60d", "name": "60 Days Premium", "days": 60, "amount": 89, "credits": 0, "desc": "2 Months Unlimited - Popular"},
    "90d": {"key": "90d", "name": "90 Days Premium", "days": 90, "amount": 120, "credits": 0, "desc": "3 Months Unlimited - Best Value"},
    "180d": {"key": "180d", "name": "180 Days Premium", "days": 180, "amount": 199, "credits": 0, "desc": "6 Months Unlimited"},
    "365d": {"key": "365d", "name": "365 Days Premium", "days": 365, "amount": 299, "credits": 0, "desc": "1 Year Unlimited - Lifetime Deal"},
    # Credits packs
    "c50": {"key": "c50", "name": "50 Credits Pack", "days": 0, "amount": 29, "credits": 50, "desc": "50 Extra Credits"},
    "c100": {"key": "c100", "name": "100 Credits Pack", "days": 0, "amount": 49, "credits": 100, "desc": "100 Extra Credits"},
    "c250": {"key": "c250", "name": "250 Credits Pack", "days": 0, "amount": 99, "credits": 250, "desc": "250 Extra Credits"},
}

def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe='')

def get_upi_link(amount: int, note: str = "OSINT Premium") -> str:
    if not UPI_ID:
        return ""
    return f"upi://pay?pa={_q(UPI_ID)}&pn={_q(UPI_NAME)}&am={amount}&cu=INR&tn={_q(note)}"

def get_upi_link_escaped(amount: int, note: str = "OSINT Premium") -> str:
    """Escaped for HTML"""
    link = get_upi_link(amount, note)
    return html.escape(link)

def format_plans() -> str:
    upi_id_esc = html.escape(UPI_ID)
    upi_name_esc = html.escape(UPI_NAME)
    txt = f"""
╔══════════════════════════════════════╗
║  👑 <b>PREMIUM PLANS - OSINT Hunter Hub</b>  ║
╚══════════════════════════════════════╝

🎁 <b>Free:</b> Har naye user ko <b>{FREE_CREDITS} credits</b> milte hain!
   1 credit = 1 lookup (Number, TG, IP, Website, etc)

🔒 Credits khatam? Premium lo - Unlimited!

<b>━━━━━━━━ Premium Plans ━━━━━━━━</b>

💎 <b>30 Days</b> - ₹49
   ├ 30 din unlimited
   ├ Saare 8 tools unlimited
   └ /buy 30d

🔥 <b>60 Days - Popular</b> - ₹89 (₹44.5/month)
   ├ 60 din unlimited
   └ /buy 60d

⭐ <b>90 Days - Best Value</b> - ₹120 (₹40/month)
   ├ 90 din unlimited
   └ /buy 90d

🚀 <b>180 Days</b> - ₹199
   └ /buy 180d

👑 <b>365 Days</b> - ₹299 (₹25/month)
   └ /buy 365d

<b>━━━━━━━━ Credits Pack ━━━━━━━━</b>

📦 50 Credits - ₹29 → /buy c50
📦 100 Credits - ₹49 → /buy c100
📦 250 Credits - ₹99 → /buy c250

<b>━━━━━━━━ Payment ━━━━━━━━</b>
💳 <b>UPI ID:</b> <code>{upi_id_esc}</code>
👤 <b>Name:</b> {upi_name_esc}

<b>Kaise kharidein:</b>
1️⃣ /buy &lt;plan&gt; - jaise /buy 30d
2️⃣ UPI se payment karo
3️⃣ Screenshot + UTR bhejo
4️⃣ Owner 5-10 min me approve karega
5️⃣ Premium chalu!

📢 @osint_xpert | 👑 @ANONYMOUS_XH
"""
    return txt

def format_plan_detail(plan_key: str) -> str:
    plan = PLANS.get(plan_key)
    if not plan:
        return "❌ Invalid plan. /premium se plans dekho"
    
    upi_link = get_upi_link(plan['amount'], f"OSINT {plan['name']}")
    upi_link_esc = html.escape(upi_link)
    upi_id_esc = html.escape(UPI_ID)
    upi_name_esc = html.escape(UPI_NAME)
    
    if plan['days'] > 0:
        txt = f"""
╔══════════════════════════════════════╗
║  💳 <b>{html.escape(plan['name'])}</b>  ║
╚══════════════════════════════════════╝

📦 <b>Plan:</b> {html.escape(plan['desc'])}
💰 <b>Amount:</b> ₹{plan['amount']}
⏰ <b>Duration:</b> {plan['days']} days unlimited

💳 <b>UPI ID:</b> <code>{upi_id_esc}</code>
👤 <b>Name:</b> {upi_name_esc}

🔗 <b>UPI Link:</b>
<code>{upi_link_esc}</code>

<b>━━━━━━━━ Steps ━━━━━━━━</b>
1️⃣ UPI ID pe ₹{plan['amount']} bhejo
   <code>{upi_id_esc}</code>

2️⃣ Payment ka screenshot lo

3️⃣ Screenshot yahan bhejo + UTR (12 digit)

4️⃣ Example:
   <code>/pay {html.escape(plan_key)} UTR1234567890</code>
   Phir screenshot bhejo

⏳ Owner 5-10 min me verify karke premium dega!

📢 @osint_xpert
"""
    else:
        txt = f"""
╔══════════════════════════════════════╗
║  📦 <b>{html.escape(plan['name'])}</b>  ║
╚══════════════════════════════════════╝

📦 <b>Pack:</b> {html.escape(plan['desc'])}
💰 <b>Amount:</b> ₹{plan['amount']}
🎁 <b>Credits:</b> {plan['credits']} credits

💳 <b>UPI ID:</b> <code>{upi_id_esc}</code>

🔗 <b>UPI Link:</b>
<code>{upi_link_esc}</code>

1️⃣ UPI pe ₹{plan['amount']} bhejo
2️⃣ Screenshot + UTR bhejo
3️⃣ /pay {html.escape(plan_key)} UTR

📢 @osint_xpert
"""
    return txt

def format_myplan(user: dict, credits: int) -> str:
    from modules.database import is_premium, premium_expiry
    is_prem = is_premium(user)
    expiry = premium_expiry(user)
    
    if is_prem:
        txt = f"""
╔══════════════════════════════════════╗
║  👑 <b>MY PREMIUM STATUS</b>  ║
╚══════════════════════════════════════╝

✅ <b>Premium Active!</b>
⏰ <b>Expiry:</b> {expiry}
🎁 <b>Credits:</b> {credits} (Unlimited - no deduction)
♾️ <b>Status:</b> Unlimited access to all 8 tools

🚀 Aap saare tools bina limit ke use kar sakte ho!

📢 @osint_xpert
"""
    else:
        txt = f"""
╔══════════════════════════════════════╗
║  📊 <b>MY PLAN - Free User</b>  ║
╚══════════════════════════════════════╝

🎁 <b>Free Credits:</b> {credits} / {FREE_CREDITS}
"""
        if credits <= 0:
            txt += f"""
🔒 <b>Credits khatam!</b>
   Premium lo unlimited ke liye

💎 /premium se plans dekho
"""
        else:
            bar = "█" * max(0, min(10, round(credits * 10 / max(1, FREE_CREDITS)))) + "░" * (10 - max(0, min(10, round(credits * 10 / max(1, FREE_CREDITS)))))
            txt += f"""
📊 [{bar}] {credits}/{FREE_CREDITS}

⚠️ 1 credit = 1 lookup
   Free tools: IP, Website basic still limited

👑 Unlimited ke liye /premium
"""
        txt += "\n📢 @osint_xpert"
    return txt

def format_credits_over(tool_name: str = "") -> str:
    upi_id_esc = html.escape(UPI_ID)
    return f"""
🔒 <b>Aapke {FREE_CREDITS} free credits khatam ho gaye</b>

Har naye user ko <b>{FREE_CREDITS} free credits</b> milte hain
Aapne saare use kar liye!

✅ <b>Solution:</b>
👑 Premium lo - Unlimited!
📦 Credits pack lo

💎 /premium se plans dekho
   30 din sirf ₹49 se shuru!

💳 UPI: <code>{upi_id_esc}</code>

{html.escape(tool_name)} premium me bina rok-tok chalega!
"""
