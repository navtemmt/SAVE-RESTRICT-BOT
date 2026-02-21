import os
import re
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, ADMINS
from database.db import db

BATCH_STATE = {}
CANCEL_FLAG = {}


def parse_link(link):
    link = link.strip()
    m = re.match(r'https?://t\.me/c/(\d+)/(\d+)', link)
    if m:
        return f'-100{m.group(1)}', int(m.group(2)), 'private'
    m = re.match(r'https?://t\.me/([^/]+)/(\d+)', link)
    if m:
        return m.group(1), int(m.group(2)), 'public'
    return None, None, None


async def get_user_client(uid):
    session_string = await db.get_session(uid)
    if not session_string:
        return None
    try:
        uc = Client(
            name=f'usersession_{uid}',
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
        )
        await uc.start()
        return uc
    except Exception as e:
        print(f'User client error {uid}: {e}')
        return None


async def send_message_to_user(bot, dest_id, msg, caption=None):
    """
    Re-send a downloaded file/text to dest_id using the bot.
    msg is a Pyrogram Message object already fetched by user client.
    """
    try:
        if msg.text:
            await bot.send_message(dest_id, msg.text)
            return True, 'text'

        if msg.photo:
            path = await msg.download()
            await bot.send_photo(dest_id, path, caption=caption or msg.caption)
            os.remove(path)
            return True, 'photo'

        if msg.video:
            path = await msg.download()
            await bot.send_video(
                dest_id, path,
                caption=caption or msg.caption,
                duration=msg.video.duration,
                width=msg.video.width,
                height=msg.video.height,
            )
            os.remove(path)
            return True, 'video'

        if msg.document:
            path = await msg.download()
            await bot.send_document(
                dest_id, path,
                caption=caption or msg.caption,
                file_name=msg.document.file_name,
            )
            os.remove(path)
            return True, 'document'

        if msg.audio:
            path = await msg.download()
            await bot.send_audio(
                dest_id, path,
                caption=caption or msg.caption,
                duration=msg.audio.duration,
                title=msg.audio.title,
                performer=msg.audio.performer,
            )
            os.remove(path)
            return True, 'audio'

        if msg.voice:
            path = await msg.download()
            await bot.send_voice(dest_id, path)
            os.remove(path)
            return True, 'voice'

        if msg.video_note:
            path = await msg.download()
            await bot.send_video_note(dest_id, path)
            os.remove(path)
            return True, 'video_note'

        if msg.sticker:
            await bot.send_sticker(dest_id, msg.sticker.file_id)
            return True, 'sticker'

        if msg.animation:
            path = await msg.download()
            await bot.send_animation(
                dest_id, path,
                caption=caption or msg.caption,
            )
            os.remove(path)
            return True, 'animation'

        return False, 'unsupported media type'

    except Exception as e:
        return False, str(e)[:80]


async def process_one(bot, uc, chat_id, msg_id, dest_id, link_type):
    try:
        if link_type == 'private':
            if not uc:
                return False, 'no session'
            msg = await uc.get_messages(int(chat_id), msg_id)
        else:
            try:
                msg = await bot.get_messages(chat_id, msg_id)
            except Exception:
                if uc:
                    msg = await uc.get_messages(chat_id, msg_id)
                else:
                    return False, 'cannot fetch'

        if not msg or getattr(msg, 'empty', False):
            return False, 'empty'

        # Get user caption if set
        user_caption = await db.get_caption(dest_id)
        ok, reason = await send_message_to_user(bot, int(dest_id), msg, caption=user_caption)
        return ok, reason

    except Exception as e:
        return False, str(e)[:80]


# ── /batch ────────────────────────────────────────────────
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


# ── /single ───────────────────────────────────────────────
@Client.on_message(filters.private & filters.command('single') & filters.user(ADMINS))
async def single_cmd(client: Client, message: Message):
    uid = message.from_user.id
    BATCH_STATE[uid] = {'step': 'WAITING_SINGLE_LINK'}
    CANCEL_FLAG.pop(uid, None)
    await message.reply(
        '**Single Download**\n\n'
        'Send the **link** of the message to save.\n'
        'Example: `https://t.me/c/1234567890/5`'
    )


# ── /cancel ───────────────────────────────────────────────
@Client.on_message(filters.private & filters.command('cancel') & filters.user(ADMINS))
async def cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if uid in BATCH_STATE or uid in CANCEL_FLAG:
        CANCEL_FLAG[uid] = True
        BATCH_STATE.pop(uid, None)
        await message.reply('Cancellation requested.')
    else:
        await message.reply('No active batch.')


# ── Text handler ──────────────────────────────────────────
@Client.on_message(
    filters.private
    & filters.text
    & filters.user(ADMINS)
    & ~filters.command(['batch', 'single', 'cancel', 'start', 'help',
                        'login', 'logout', 'myplan', 'premium', 'setchat',
                        'set_thumb', 'view_thumb', 'del_thumb',
                        'set_caption', 'see_caption', 'del_caption',
                        'set_del_word', 'rem_del_word',
                        'set_repl_word', 'rem_repl_word', 'cmd'])
)
async def batch_text_handler(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in BATCH_STATE:
        return

    state = BATCH_STATE[uid]
    step = state.get('step')

    # ── SINGLE ─────────────────────────────────────────────
    if step == 'WAITING_SINGLE_LINK':
        link = message.text.strip()
        chat_id, msg_id, link_type = parse_link(link)
        if not chat_id:
            return await message.reply('Invalid link. Try again or /cancel.')
        BATCH_STATE.pop(uid, None)
        status = await message.reply('Fetching...')
        uc = await get_user_client(uid) if link_type == 'private' else None
        ok, reason = await process_one(client, uc, chat_id, msg_id, message.chat.id, link_type)
        if uc:
            try:
                await uc.stop()
            except Exception:
                pass
        await status.edit('Done.' if ok else f'Failed: {reason}')
        return

    # ── BATCH step 1: get link ─────────────────────────────
    if step == 'WAITING_LINK':
        link = message.text.strip()
        chat_id, msg_id, link_type = parse_link(link)
        if not chat_id:
            return await message.reply('Invalid link. Try again or /cancel.')
        state.update({'step': 'WAITING_COUNT', 'chat_id': chat_id,
                      'msg_id': msg_id, 'link_type': link_type})
        await message.reply('How many messages to download from this link? (max 200)')
        return

    # ── BATCH step 2: get count + run ──────────────────────
    if step == 'WAITING_COUNT':
        if not message.text.strip().isdigit():
            return await message.reply('Send a valid number. Try again or /cancel.')
        count = int(message.text.strip())
        if count < 1 or count > 200:
            return await message.reply('Number must be 1-200.')

        blocked = await db.check_limit(uid)
        if blocked:
            BATCH_STATE.pop(uid, None)
            return await message.reply('Daily limit reached (10 files/24h). Upgrade to premium.')

        chat_id = state['chat_id']
        start_id = state['msg_id']
        link_type = state['link_type']
        BATCH_STATE.pop(uid, None)

        status = await message.reply(f'Starting batch: 0/{count} | Success: 0 | Failed: 0')

        uc = None
        if link_type == 'private':
            uc = await get_user_client(uid)
            if not uc:
                return await status.edit('Login required for private links. Use /login first.')

        success = 0
        failed = 0
        CANCEL_FLAG.pop(uid, None)

        try:
            for i in range(count):
                if CANCEL_FLAG.get(uid):
                    await status.edit(f'Cancelled at {i}/{count} | Success: {success} | Failed: {failed}')
                    break
                mid = start_id + i
                ok, reason = await process_one(client, uc, chat_id, mid,
                                               message.chat.id, link_type)
                if ok:
                    success += 1
                    await db.add_traffic(uid)
                else:
                    failed += 1
                    print(f'[batch] msg {mid} failed: {reason}')

                if (i + 1) % 5 == 0 or (i + 1) == count:
                    try:
                        await status.edit(
                            f'Progress: {i+1}/{count} | Success: {success} | Failed: {failed}'
                        )
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
