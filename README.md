# 🕵️ OSINT Hunter Hub — @osint_hunter_hub_bot

**Owner:** HIMANSHU · Telegram: [@ANONYMOUS_XH](https://t.me/ANONYMOUS_XH)
**Force Channel:** @osint_xpert
**Stack:** python-telegram-bot + apna khud ka gateway (utility-api) · Render par live
**Repo:** private — code/keys ka leakage zero

> Har tool ka apna fallback chain hai: pehle sabse fast source, fail hone par doosra,
> sab fail to honest error (jhoota "data not found" nahi). Repeat lookup = cache se 0 ms.

---

## 🆕 v2.18 — kya naya hai

| Cheez | Detail |
|---|---|
| 📇 **TRUE CALLER tool** | `/tc 9492915392` — naam, carrier, circle, IMEI/IP/MAC, locations. **3-layer:** rich provider → offline (carrier/circle/tz, 0 ms) → apna num dataset. Provider down ho to bhi tool kaam karta hai |
| 🛡 **Website extractor SSRF FIX** | Pehle koi bhi `http://169.254.169.254` (cloud metadata) ya localhost scan kar sakta tha. Ab: scheme/IP/metadata-host/DNS-rebind check har redirect hop par + 2 MB stream cap + content-type gate |
| 🔐 Secrets code se nikaale | Saari keys sirf Render env me — repo me kuch hardcoded nahi |
| ♻️ **userapi cache (12h)** | `/rc`, `/v2n`, `/a2n` repeat par 0 ms (pehle har baar network) |
| 🚨 Vault backup alert | 3 lagataar backup fail = owner ko Telegram alert (chupchaap data udna band) |
| 💬 Friendly errors | `upstream`/`timeout` jaise technical words ki jagah saaf Hinglish message |

---

## 🔥 11 Advanced Tools

### Tool 7: Number Info (Same as utility-duniya-bot) ✅
- Same API: utility-api-vu83
- 10-digit number → Name, Father, Address, Circle, Aadhaar masked (XXXX-XXXX-1234), Alt phone, Response ms
- No data → Only `❌ No records found for this number in any database.`
- Commands: `/numinfo 9876543210`, `/number`, `/num`
- Advanced formatting with spaces, emojis, separators

### Tool 8: Telegram Tools ✅
- TG Number: `/tginfo @username` → Telegram username se info
- Pak Number: `/paknum 03001234567`
- Same gateway

### Tool 1: Telegram Channel Scraper (Most Advanced) ✅
- Channel info with members, description, verification
- Scrape 1-10000 messages with views, forwards, replies, media, links, hashtags, mentions, reactions
- Search, media only, stats, batch 5 channels
- Export JSON/CSV
- Commands: `/channel_info`, `/scrape`, `/scrape_search`, `/scrape_media`, `/stats`, `/batch`

### Tool 2: Instagram OSINT ✅
- Public profile: followers, bio, posts, pic
- Command: `/insta @username`

### Tool 6: Social Media Security Checker (Educational) ✅
- Email leak, phone leak, suspicious links, username risk, security score 0-100
- Commands: `/igsecure @username`, `/security`, `/sec`

### Tool 3: Email OSINT ✅
- Validation, disposable, Gravatar, breach check, MX
- Command: `/email test@gmail.com`

### Tool 4: IP & Domain Recon ✅
- IP: location, ISP, VPN/Proxy detection, Google Maps
- Domain: DNS + WHOIS + SSL + IP full recon
- Commands: `/ip 8.8.8.8`, `/domain google.com`, `/dns`, `/whois`, `/ssl`

### Tool 5: Image EXIF Forensics ✅
- GPS + Camera + Date + Editing detection
- Send photo as File → auto EXIF
- Commands: `/exif`

### Tool 9: OCR Extractor ✅
- Image to Text (Hindi+English) via EasyOCR + pytesseract
- Send photo as File with caption 'ocr'
- Commands: `/ocr`

### Tool 10: Website Extractor ✅
- Emails, Phones, Links, Meta tags (title, description, keywords, OG image)
- Commands: `/wextract https://site.com`

### Tool 11: Excel to PDF + File Converter Hub ✅
- Excel (xlsx, xls, csv) → PDF (landscape A4, styled table)
- CSV ↔ Excel, Image → PDF, PDF → Text
- Send file as Document with caption excel2pdf etc
- Commands: `/excel2pdf`, `/csv2excel`, `/excel2csv`, `/img2pdf`, `/pdf2text`

---

## 📜 All Commands

```
/start, /help

# Number Info (Same as utility bot)
/numinfo 9876543210
/number 9876543210

# TG Tools
/tginfo @username
/paknum 03001234567

# Telegram Scraper
/channel_info @channel
/scrape @channel 100
/scrape_search @channel keyword 100
/scrape_media @channel 50
/stats @channel
/batch @ch1 @ch2 30

# Instagram + Security
/insta @username
/igsecure @username
/security @username

# Email
/email test@gmail.com

# IP/Domain
/ip 8.8.8.8
/domain google.com
/dns google.com
/whois google.com
/ssl google.com

# EXIF
Send photo as File → auto
/exif

# OCR
Send photo as File + caption 'ocr'
/ocr

# Website Extractor
/wextract https://site.com

# File Converter
Send Excel/CSV/Image/PDF as Document + caption:
excel2pdf, csv2excel, excel2csv, img2pdf, pdf2text
```

---

## ⚙️ Setup & Hosting (Step by Step)

### ENV Variables (Required)
```
BOT_TOKEN=<Render Environment me set karo — code/repo me kabhi nahi>
ADMIN_ID=YOUR_NUMERIC_ADMIN_ID
FORCE_CHANNEL=-1004393596502
FORCE_CHANNEL_LINK=https://t.me/osint_xpert
MYNUM_API_URL=https://utility-api-vu83.onrender.com/api
MYNUM_API_KEY=<Render Environment me set karo>
API_ID= (from my.telegram.org - for Telegram scraper)
API_HASH= (from my.telegram.org)
SESSION_STRING= (from generate_session.py)
```

### Free Hosting - No Card Needed (Best for you)

#### Option 1: Pella.app (RECOMMENDED - No Card, 1-Click)
1. Go to https://pella.app → Login with GitHub
2. New Project → Import from GitHub → `savecontentrestricted-pixel/osint`
3. Add Environment Variables (above)
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `python bot.py`
6. Deploy → Live in 1 min!
7. Free: 0.1 CPU, 100MB RAM, 5GB Disk, No sleep

#### Option 2: Koyeb.com (No Card)
1. koyeb.com → Login with GitHub
2. Create App → Import `osint` repo
3. Build: `pip install -r requirements.txt`
4. Run: `python bot.py`
5. Add env vars, deploy

#### Option 3: Render.com (You already use)
1. render.com → New Web Service → Connect repo `osint`
2. Build: `pip install -r requirements.txt`
3. Start: `python bot.py`
4. Add env vars
5. Note: Sleeps after 15 min, needs keep-alive ping

#### Option 4: Oracle Cloud (Most Powerful, Needs Card for verification but free forever)
- 2 ARM cores + 12GB RAM + 200GB storage forever free
- Setup Ubuntu VM, git clone, pip install, PM2

See PELLA-DEPLOY-GUIDE.md for detailed steps.

---

## 🔥 Advanced Features in This Code

- Force Join Channel Check (-1004393596502 @osint_xpert)
- Beautiful formatting with spaces, emojis, separators (╔══╗ style)
- Max data extraction - more info than any other bot
- FloodWait handling + rate limiting
- Export JSON/CSV/PDF
- Admin bypass for force join
- 11 Tools in one bot - advanced high-level code

---

Legal: Public data only, educational OSINT.
