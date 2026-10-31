import os
from dotenv import load_dotenv

load_dotenv()

# Bot Token - Provided by user: @osint_hunter_hub_bot
# v2.0 SECURITY: token ab sirf Render env se (pehle public repo me hardcoded tha!)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Telegram API - my.telegram.org
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

# Owner - Provided
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)   # private; Render env only

ADMINS = [ADMIN_ID] if ADMIN_ID else []
_admins_raw = os.getenv("ADMINS", "").strip()
if _admins_raw:
    for x in _admins_raw.split(","):
        try:
            adm = int(x.strip())
            if adm not in ADMINS:
                ADMINS.append(adm)
        except:
            pass

# Force Join - Provided
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "").strip()  # v2.24.8: force-join DEFAULT OFF (boss order)
if FORCE_CHANNEL.lower() in ("0", "off", "false", "none", "no"):
    FORCE_CHANNEL = ""
FORCE_CHANNEL_LINK = os.getenv("FORCE_CHANNEL_LINK", "").strip()
FORCE_CHANNEL_USERNAME = os.getenv("FORCE_CHANNEL_USERNAME", "").strip()

OWNER_USERNAME = os.getenv("OWNER_USERNAME", "@ANONYMOUS_XH").strip()   # v2.6: naya admin
BRAND_TAG = os.getenv("BRAND_TAG", "@ANONYMOUS_XH").strip()

# APIs
MYNUM_API_URL = os.getenv("MYNUM_API_URL", "https://utility-api-we9m.onrender.com/api").strip()
# v2.0 SECURITY: API key sirf env se (public repo me hardcoded nahi)
MYNUM_API_KEY = os.getenv("MYNUM_API_KEY", "").strip()

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def validate():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    # Telegram scraper needs these, but other tools work without
    if not API_ID:
        missing.append("API_ID (for Telegram scraper)")
    if not API_HASH:
        missing.append("API_HASH (for Telegram scraper)")
    if not SESSION_STRING:
        missing.append("SESSION_STRING (for Telegram scraper - generate_session.py)")
    return missing
