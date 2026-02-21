import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import ADMINS
from database.db import db

BATCH_STATE = {}

@Client.on_message(filters.private & filters.command("batch") & filters.user(ADMINS))
async def batch_start(client: Client, message: Message):
    user_id = message.from_user.id
    BATCH_STATE[user_id] = {"step": "WAITING_LINK"}
    await message.reply(
        "**Initiating Batch Mode...**

"
        "Please send the **starting link** of the restricted content.
"
        "Example: `https://t.me/c/123456789/1`"
    )

@Client.on_message(filters.private & filters.text & filters.user(ADMINS))
async def batch_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in BATCH_STATE:
        return
    
    state = BATCH_STATE[user_id]
    step = state["step"]
    
    if step == "WAITING_LINK":
        link = message.text
        if not ("t.me/" in link):
            return await message.reply("❌ Invalid link format. Please try again.")
            
        BATCH_STATE[user_id].update({"step": "WAITING_COUNT", "link": link})
        await message.reply("✅ Link received! Now send the **number of messages** to download.")
        
    elif step == "WAITING_COUNT":
        if not message.text.isdigit():
            return await message.reply("❌ Please send a valid number.")
            
        count = int(message.text)
        link = state["link"]
        del BATCH_STATE[user_id]
        
        await message.reply(f"🚀 Starting batch processing for {count} messages...")
        # Placeholder for actual processing logic
        # In a real implementation, you would call your download/forward logic here
