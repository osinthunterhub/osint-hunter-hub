"""
Generate Telethon String Session
Ek baar chalao, phone number se login karo, SESSION_STRING milega
Usse .env me daal do
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0") or input("API_ID (my.telegram.org se): ").strip())
API_HASH = os.getenv("API_HASH", "").strip() or input("API_HASH: ").strip()

async def main():
    print("=== Telethon Session Generator ===")
    print(f"API_ID: {API_ID}")
    print(f"API_HASH: {API_HASH[:5]}...{API_HASH[-5:] if len(API_HASH)>10 else ''}")
    print("\nAb phone number dalo (with country code, jaise +91xxxxxxxxxx):")
    
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start()
    
    print("\n✅ Login successful!")
    session_string = client.session.save()
    print("\n🔑 Tumhara SESSION_STRING ye hai (ise .env me daalo):")
    print("="*80)
    print(session_string)
    print("="*80)
    print("\nIse kisi ko share mat karo! Ye tumhare Telegram account ka access hai.")
    
    # Save to file also
    with open("session_string.txt", "w") as f:
        f.write(session_string)
    print("\n✅ session_string.txt me bhi save kar diya hai.")
    
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
