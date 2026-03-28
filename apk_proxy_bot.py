"""
APK Signing Proxy Bot
======================
Flow: User → Your Bot → Signer Bot → Your Bot → User

Requirements:
    pip install python-telegram-bot telethon

Fill in all the CONFIG values below before running.
"""

import asyncio
import os
import logging
from telethon import TelegramClient, events
from telegram import Update, Bot
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
#  CONFIG — Fill these in before running
# ─────────────────────────────────────────────
BOT_TOKEN        = "8298292446:AAFNUs_UOS_wSwCySMpWlDYmCJxv6Oq-Vuw"          # From @BotFather
API_ID           = 39913572                  # From my.telegram.org (integer)
API_HASH         = "f67b4916275fdc00f15bcc4ad60100af"      # From my.telegram.org (string)
PHONE_NUMBER     = "+919835687961"           # Your Telegram account number
SIGNER_BOT       = "@android_protect_bot"           # Username of the signer bot
SESSION_NAME     = "proxy_session"           # Name for saved session file
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Tracks which bot user is waiting for their signed APK
# { signer_chat_msg_id: (bot_user_id, original_filename) }
pending_requests: dict = {}

# The Telethon user client (acts as a real user to talk to signer bot)
user_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# The PTB bot application (user-facing bot)
ptb_app: Application = None


# ──────────────────────────────────────────────────────────
#  STEP 1 — User sends APK to YOUR bot
# ──────────────────────────────────────────────────────────
async def handle_user_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when a user sends a .apk file to your bot."""
    user_id   = update.effective_user.id
    document  = update.message.document

    # Check it is actually an APK
    if not document.file_name.lower().endswith(".apk"):
        await update.message.reply_text("❌ Please send a valid .apk file.")
        return

    await update.message.reply_text(
        f"📦 Received *{document.file_name}*\n"
        "⏳ Forwarding to signer bot... Please wait.",
        parse_mode="Markdown"
    )

    # Download APK from Telegram to local disk
    file = await context.bot.get_file(document.file_id)
    local_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(local_path)

    logger.info(f"Downloaded APK from user {user_id} → {local_path}")

    # Send APK to signer bot using Telethon user session
    await send_to_signer_bot(user_id, local_path, document.file_name)


# ──────────────────────────────────────────────────────────
#  STEP 2 — Send APK to the signer bot (as a real user)
# ──────────────────────────────────────────────────────────
async def send_to_signer_bot(user_id: int, file_path: str, filename: str):
    """Send the APK file to the signer bot via the Telethon user client."""
    try:
        msg = await user_client.send_file(
            SIGNER_BOT,
            file_path,
            caption=f"Please sign this APK: {filename}"
        )
        # Remember which Telegram user is waiting for this
        pending_requests[msg.id] = {
            "user_id":  user_id,
            "filename": filename,
        }
        logger.info(
            f"Sent APK to signer bot (msg_id={msg.id}) for user {user_id}"
        )
    except Exception as e:
        logger.error(f"Failed to send APK to signer bot: {e}")
        bot = Bot(BOT_TOKEN)
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ Failed to reach the signer bot.\nError: {e}"
        )
    finally:
        # Clean up local temp file
        if os.path.exists(file_path):
            os.remove(file_path)


# ──────────────────────────────────────────────────────────
#  STEP 3 — Receive signed APK from signer bot
# ──────────────────────────────────────────────────────────
@user_client.on(events.NewMessage(from_users=SIGNER_BOT))
async def handle_signer_reply(event):
    """
    Called when the signer bot sends a message back to our user account.
    We look for a document (the signed APK) and forward it to the original user.
    """
    bot = Bot(BOT_TOKEN)

    # ── Case 1: Signer bot sent a file (signed APK) ──
    if event.document:
        signed_path = f"/tmp/signed_{event.document.id}.apk"

        # Find which user was waiting — check recent pending request
        if pending_requests:
            # Get the most recent pending request (simple approach)
            # For production, match by reply_to or sequence logic
            latest_key = max(pending_requests.keys())
            request    = pending_requests.pop(latest_key)
            user_id    = request["user_id"]
            filename   = request.get("filename", "signed.apk")

            # Download signed APK from signer bot
            await event.download_media(signed_path)
            logger.info(f"Downloaded signed APK → {signed_path}")

            # Send signed APK to original user
            await bot.send_document(
                chat_id=user_id,
                document=open(signed_path, "rb"),
                filename=f"signed_{filename}",
                caption="✅ Your APK has been signed successfully!"
            )
            logger.info(f"Delivered signed APK to user {user_id}")

            # Clean up
            if os.path.exists(signed_path):
                os.remove(signed_path)
        else:
            logger.warning("Received signed APK but no pending requests found.")

    # ── Case 2: Signer bot sent a text message (error or status) ──
    elif event.text:
        text = event.text.strip()
        logger.info(f"Signer bot says: {text}")

        # If it looks like an error, forward it to the waiting user
        if pending_requests:
            latest_key = max(pending_requests.keys())
            request    = pending_requests.get(latest_key)
            if request:
                user_id = request["user_id"]
                await bot.send_message(
                    chat_id=user_id,
                    text=f"ℹ️ Signer bot reply:\n{text}"
                )


# ──────────────────────────────────────────────────────────
#  STARTUP — First-time session login
# ──────────────────────────────────────────────────────────
async def start_user_client():
    """
    Start the Telethon user client.
    On FIRST RUN: It will ask for your phone number OTP in the terminal.
    After that, session is saved to `proxy_session.session` file.
    """
    await user_client.start(phone=PHONE_NUMBER)
    logger.info("✅ Telethon user client logged in!")
    me = await user_client.get_me()
    logger.info(f"Logged in as: {me.first_name} (@{me.username})")


# ──────────────────────────────────────────────────────────
#  MAIN — Run both clients together
# ──────────────────────────────────────────────────────────
async def main():
    global ptb_app

    # Start the Telethon user session first
    await start_user_client()

    # Build the PTB bot application
    ptb_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Register handler: user sends a document (APK) to the bot
    ptb_app.add_handler(
        MessageHandler(filters.Document.ALL, handle_user_apk)
    )

    logger.info("🤖 Bot is running... Send an APK to get it signed!")

    # Run both event loops together
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling()

    # Keep the Telethon client alive alongside PTB
    await user_client.run_until_disconnected()

    # Cleanup on exit
    await ptb_app.updater.stop()
    await ptb_app.stop()
    await ptb_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
