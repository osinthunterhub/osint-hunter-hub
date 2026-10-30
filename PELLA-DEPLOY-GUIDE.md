# Free Hosting Guide - No Card Needed (Pella.app + Others)

Aapke paas UPI/Card nahi hai, isliye ye best free options hain:

## 1. Pella.app - BEST FOR YOU (No Card, 1-Click)

**Steps:**
1. https://pella.app pe jao, GitHub se login karo
2. New Project → Import from GitHub → `savecontentrestricted-pixel/osint` select karo
3. Environment Variables daalo:
   ```
   BOT_TOKEN=your_bot_token
   API_ID=your_api_id
   API_HASH=your_api_hash
   SESSION_STRING=your_session_string
   ADMIN_ID=your_id
   ```
4. Deploy → 1 min me live!

**Free Limits:** 0.1 CPU, 100MB RAM, 5GB Disk, Unlimited Bandwidth - aapke 6 tools ke liye kaafi hai (bot ~80MB RAM use karta hai)

**Pros:** No credit card, no sleep, 99.9% uptime, Python support

---

## 2. Koyeb.com - Second Best (No Card for free tier)

1. koyeb.com pe GitHub se login
2. Create App → GitHub repo select → `osint`
3. Build command: `pip install -r requirements.txt`
4. Run command: `python bot.py`
5. Env vars daalo, deploy

Free: 512MB RAM, sleeps after 15 min (Pella se thoda kam accha)

---

## 3. Replit.com - For Testing (No Card)

1. replit.com pe import GitHub repo
2. Run button dabao
3. Always On ke liye Hacker plan chahiye ($7/month), free me sleep hota hai

---

## 4. Oracle Cloud - Most Powerful BUT Needs Card (Future ke liye)

Jab card mil jaye to ye best hai:
- 2 ARM cores + 12GB RAM + 200GB storage FOREVER FREE
- Kabhi sleep nahi hota, 10 bots ek saath chala sakte ho
- Setup: Ubuntu VM banao, `git clone`, `python bot.py` with PM2

Guide: https://klymentiev.com/blog/free-vps

---

## Recommendation for You:

**Abhi ke liye: Pella.app use karo** - No card, 1-click, aapke 6 tools ke liye perfect.

Jab thoda paisa aaye ya card mile to Oracle pe shift kar dena.

Main aapke liye Pella ke liye `pella.json` config bhi bana diya hai repo me.
