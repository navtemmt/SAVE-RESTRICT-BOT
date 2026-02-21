import os
import re
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, ADMINS
from database.db import db

# Active batch sessions: uid -> {step, link, count, cancel}
BATCH_STATE = {}
CANCEL_FLAG = {}


def parse_link(link):
    """Parse t.me link and return (chat_id, msg_id, link_type)."""
    link = link.strip()
    # Private: t.me/c/CHATID/MSGID
    m = re.match(r'https?://t\.me/c/(\d+)/(\d+)', link)
    if m:
        return f"-100{m.group(1)}", int(m.group(2)), 'private'
    # Public: t.me/USERNAME/MSGID
    m = re.match(r'https?://t\.me/([^/]+)/(\d+)', link)
    if m:
        return m.group(1), int(m.group(2)), 'public'
    return None, None, None


async def get_user_client(uid):
    """Return a Pyrogram Client from the user's saved session string."""
    session_string = await db.get_session(uid)
    if not session_string:
        return None
    try:
        client = Client(
            name=f"usersession_{uid}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
        )
        await client.start()
        return client
    except Exception as e:
        print(f'User client error for {uid}: {e}')
        return None


async def forward_message(bot, user_client, chat_id, msg_id, dest_id, link_type):
    """Fetch one message and forward/send it to dest_id."""
    try:
        if link_type == 'private':
            if not user_client:
                return False, 'No session - use /login first'
            msg = await user_client.get_messages(int(chat_id), msg_id)
        else:
            try:
                msg = await bot.get_messages(chat_id, msg_id)
            except Exception:
                if user_client:
                    msg = await user_client.get_messages(chat_id, msg_id)
                else:
                    return False, 'Cannot fetch public message'

        if not msg or getattr(msg, 'empty', False):
            return False, 'Empty message'

        await bot.copy_message(
            chat_id=int(dest_id),
            from_chat_id=msg.chat.id,
            message_id=msg.id,
        )
        return True, 'OK'
    except Exception as e:
        return False, str(e)[:60]


# ============================================================
# /batch command - admin only
# ============================================================
@Client.on_message(filters.private & filters.command('batch') & filters.user(ADMINS))
async def batch_cmd(client: Client, message: Message):
    uid = message.from_user.id
    BATCH_STATE[uid] = {'step': 'WAITING_LINK'}
    CANCEL_FLAG.pop(uid, None)
    await message.reply(
        '**Batch Mode**\n\n'
        'Send the **starting link** (first message).\n'
        'Example: `https://t.me/c/1234567890/1`\n\n'
        'Send /cancel to abort.'
    )


# /single command - admin only
@Client.on_message(filters.private & filters.command('single') & filters.user(ADMINS))
async def single_cmd(client: Client, message: Message):
    uid = message.from_user.id
    BATCH_STATE[uid] = {'step': 'WAITING_SINGLE_LINK'}
    CANCEL_FLAG.pop(uid, None)
    await message.reply(
        '**Single Download**\n\n'
        'Send the **link** of the message you want to save.\n'
        'Example: `https://t.me/c/1234567890/5`'
    )


# /cancel command
@Client.on_message(filters.private & filters.command('cancel') & filters.user(ADMINS))
async def cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if uid in BATCH_STATE:
        CANCEL_FLAG[uid] = True
        BATCH_STATE.pop(uid, None)
        await message.reply('Cancelled.')
    else:
        await message.reply('No active batch.')


# ============================================================
# Text handler - drives the conversation flow
# ============================================================
@Client.on_message(
    filters.private
    & filters.text
    & filters.user(ADMINS)
    & ~filters.command(['batch', 'single', 'cancel', 'start', 'help', 'login',
                        'logout', 'myplan', 'premium', 'setchat', 'set_thumb',
                        'view_thumb', 'del_thumb', 'set_caption', 'see_caption',
                        'del_caption', 'set_del_word', 'rem_del_word',
                        'set_repl_word', 'rem_repl_word', 'cmd'])
)
async def batch_text_handler(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in BATCH_STATE:
        return

    state = BATCH_STATE[uid]
    step = state.get('step')

    # ---- SINGLE: waiting for link ----
    if step == 'WAITING_SINGLE_LINK':
        link = message.text.strip()
        chat_id, msg_id, link_type = parse_link(link)
        if not chat_id:
            return await message.reply('Invalid link. Try again or /cancel.')

        BATCH_STATE.pop(uid, None)
        status = await message.reply('Processing...')

        if link_type == 'private':
            uc = await get_user_client(uid)
        else:
            uc = None

        ok, reason = await forward_message(client, uc, chat_id, msg_id, message.chat.id, link_type)
        if uc:
            try:
                await uc.stop()
            except Exception:
                pass

        if ok:
            await status.edit('Done.')
        else:
            await status.edit(f'Failed: {reason}')
        return

    # ---- BATCH: waiting for start link ----
    if step == 'WAITING_LINK':
        link = message.text.strip()
        chat_id, msg_id, link_type = parse_link(link)
        if not chat_id:
            return await message.reply('Invalid link. Try again or /cancel.')

        state.update({'step': 'WAITING_COUNT', 'chat_id': chat_id,
                      'msg_id': msg_id, 'link_type': link_type})
        await message.reply('How many messages to download from this link?')
        return

    # ---- BATCH: waiting for count ----
    if step == 'WAITING_COUNT':
        if not message.text.strip().isdigit():
            return await message.reply('Send a valid number. Try again or /cancel.')

        count = int(message.text.strip())
        if count < 1 or count > 200:
            return await message.reply('Number must be between 1 and 200.')

        # Check daily limit for non-premium users
        blocked = await db.check_limit(uid)
        if blocked:
            BATCH_STATE.pop(uid, None)
            return await message.reply(
                'Daily limit reached (10 files / 24h). Upgrade to premium for unlimited access.'
            )

        chat_id = state['chat_id']
        start_id = state['msg_id']
        link_type = state['link_type']
        BATCH_STATE.pop(uid, None)

        status = await message.reply(f'Starting batch: 0/{count}')

        if link_type == 'private':
            uc = await get_user_client(uid)
            if not uc:
                return await status.edit('Login required for private links. Use /login first.')
        else:
            uc = None

        success = 0
        failed = 0
        CANCEL_FLAG.pop(uid, None)

        try:
            for i in range(count):
                if CANCEL_FLAG.get(uid):
                    await status.edit(f'Cancelled at {i}/{count}. Success: {success}')
                    break

                mid = start_id + i
                ok, reason = await forward_message(client, uc, chat_id, mid,
                                                   message.chat.id, link_type)
                if ok:
                    success += 1
                    await db.add_traffic(uid)
                else:
                    failed += 1

                if (i + 1) % 5 == 0 or (i + 1) == count:
                    try:
                        await status.edit(f'Progress: {i+1}/{count} | Success: {success} | Failed: {failed}')
                    except Exception:
                        pass

                await asyncio.sleep(2)
            else:
                await status.edit(f'Batch done. Success: {success}/{count} | Failed: {failed}')
        finally:
            if uc:
                try:
                    await uc.stop()
                except Exception:
                    pass
            CANCEL_FLAG.pop(uid, None)
