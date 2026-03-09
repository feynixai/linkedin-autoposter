#!/usr/bin/env python3
"""
Telegram bot for LinkedIn autoposter — powered by GPT-5.4 agent with tools.
Run: python bot.py

The agent can:
- Search the web for trending topics
- Fetch tweets, articles, and any URL
- Generate posts with AI images
- Edit text, regenerate images
- Remember your preferences
- Schedule and post to LinkedIn
"""

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta

import openai
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

load_dotenv()

from db import (
    get_draft, get_latest_draft, mark_posted, mark_skipped,
    set_scheduled_time, get_scheduled_posts, save_draft, get_recent_topics,
    create_conversation, save_conversation, get_conversation, get_conversations,
)
from linkedin import create_post_with_image
from pipeline import run_pipeline, OPENAI_API_KEY
from agent import run_agent_sync

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AUTHORIZED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

scheduler = None

# Tool name → friendly emoji for status updates
TOOL_EMOJI = {
    "web_search_preview": "🔍 Searching the web...",
    "fetch_url": "🌐 Fetching URL...",
    "fetch_rss_trends": "📰 Fetching tech news...",
    "generate_post_image": "🎨 Generating image...",
    "download_web_image": "📥 Downloading image...",
    "download_web_video": "📥 Downloading video...",
    "save_post_draft": "💾 Saving draft...",
    "update_draft_text": "✏️ Updating text...",
    "update_draft_image": "🖼️ Updating image...",
    "get_current_draft": "📋 Loading draft...",
    "get_recent_posted_topics": "📊 Checking recent posts...",
    "remember_preference": "🧠 Remembering...",
    "forget_preference": "🧠 Forgetting...",
    "get_memories": "🧠 Loading preferences...",
    "post_to_linkedin": "🚀 Posting to LinkedIn...",
    "schedule_linkedin_post": "📅 Scheduling on LinkedIn...",
    "skip_draft": "⏭️ Skipping draft...",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"LinkedIn Autoposter Agent ready!\n\n"
        f"Chat ID: `{chat_id}`\n\n"
        f"Just talk to me naturally. I can:\n\n"
        f"*Create posts:*\n"
        f"- \"Generate a post about today's AI news\"\n"
        f"- \"Use this tweet: https://x.com/...\"\n"
        f"- \"Search for the latest on AI agents and write a post\"\n"
        f"- Send me multiple links and I'll combine them\n\n"
        f"*Edit:*\n"
        f"- \"Make it shorter\" / \"More casual\"\n"
        f"- \"Change the image\" / \"Make the image about...\"\n"
        f"- \"Add a mention of [topic]\"\n\n"
        f"*Manage:*\n"
        f"- \"Post it\" / \"Skip\" / \"Schedule for 3pm\"\n"
        f"- \"Remember: never use dashes\"\n"
        f"- \"What do you remember?\"\n\n"
        f"*Commands:*\n"
        f"/new - Start a fresh conversation\n"
        f"/history - View conversation history\n"
        f"/generate - Quick generate from trending news\n"
        f"/preview - Show current draft\n"
        f"/status - Bot status\n"
        f"/scheduled - View scheduled posts",
        parse_mode="Markdown",
    )


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a fresh conversation, saving the current one."""
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    # Save current conversation if it has messages
    old_conv_id = context.chat_data.get("conv_id")
    old_history = context.chat_data.get("history", [])
    if old_conv_id and old_history:
        # Auto-title from first user message
        first_msg = next((m["content"][:50] for m in old_history if m["role"] == "user"), "Untitled")
        save_conversation(old_conv_id, old_history, title=first_msg)

    # Create new conversation
    conv_id = create_conversation(chat_id)
    context.chat_data["conv_id"] = conv_id
    context.chat_data["history"] = []
    await update.message.reply_text("Fresh conversation started. Use /history to see past conversations.")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show past conversations with buttons to resume."""
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    convos = get_conversations(chat_id, limit=10)
    if not convos:
        await update.message.reply_text("No conversation history yet. Start chatting!")
        return

    buttons = []
    for c in convos:
        title = c["title"] or "Untitled"
        if len(title) > 40:
            title = title[:40] + "..."
        label = f"{title} ({c['updated_at'][:10]})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"conv:{c['id']}")])

    await update.message.reply_text(
        "Past conversations — tap to resume:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick generate using RSS trends pipeline."""
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    msg = await update.message.reply_text("Running pipeline...")
    try:
        draft_id = await asyncio.to_thread(run_pipeline)
        if draft_id:
            await msg.delete()
            await send_preview(context.bot, chat_id, draft_id)
        else:
            await msg.edit_text("Failed to generate. Check logs.")
    except Exception as e:
        await msg.edit_text(f"Error: {e}")


async def preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return
    draft = get_latest_draft()
    if not draft:
        await update.message.reply_text("No drafts. Send me a message to create one!")
        return
    await send_preview(context.bot, chat_id, draft["id"])


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = get_latest_draft()
    scheduled = get_scheduled_posts()
    status = f"Pending draft: #{draft['id']}" if draft else "No pending drafts"
    sched_text = f"\nScheduled: {len(scheduled)} posts" if scheduled else ""
    await update.message.reply_text(f"Bot running\n{status}{sched_text}\nDaily auto-generate: 8:00 AM IST")


async def scheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    scheduled = get_scheduled_posts()
    if not scheduled:
        await update.message.reply_text("No scheduled posts.")
        return

    await update.message.reply_text(f"📅 *{len(scheduled)} scheduled post(s):*", parse_mode="Markdown")
    for p in scheduled:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Post Now", callback_data=f"post:{p['id']}"),
                InlineKeyboardButton("Cancel", callback_data=f"skip:{p['id']}"),
            ],
        ])
        caption = f"*Scheduled #{p['id']}*\n⏰ {p['posted_at']}\n\n{p['content']}"
        if len(caption) > 1024:
            await context.bot.send_message(chat_id, caption, parse_mode="Markdown")
            if p.get("image_path") and os.path.exists(p["image_path"]):
                with open(p["image_path"], "rb") as img:
                    await context.bot.send_photo(chat_id, img, reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id, "(No image)", reply_markup=keyboard)
        else:
            if p.get("image_path") and os.path.exists(p["image_path"]):
                with open(p["image_path"], "rb") as img:
                    await context.bot.send_photo(chat_id, img, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id, caption, parse_mode="Markdown", reply_markup=keyboard)


async def send_preview(bot, chat_id, draft_id):
    """Send draft preview with image and action buttons."""
    draft = get_draft(draft_id)
    if not draft:
        await bot.send_message(chat_id, "Draft not found.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Post Now", callback_data=f"post:{draft_id}"),
            InlineKeyboardButton("Skip", callback_data=f"skip:{draft_id}"),
        ],
        [
            InlineKeyboardButton("Regenerate", callback_data=f"regen:{draft_id}"),
        ],
    ])

    caption = f"*Draft #{draft_id}*\n\n{draft['content']}"
    if len(caption) > 1024:
        await bot.send_message(chat_id, f"*Draft #{draft_id}*\n\n{draft['content']}", parse_mode="Markdown")
        if draft.get("image_path") and os.path.exists(draft["image_path"]):
            with open(draft["image_path"], "rb") as img:
                await bot.send_photo(chat_id, img, reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, "(No image)", reply_markup=keyboard)
    else:
        if draft.get("image_path") and os.path.exists(draft["image_path"]):
            with open(draft["image_path"], "rb") as img:
                await bot.send_photo(chat_id, img, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, caption, parse_mode="Markdown", reply_markup=keyboard)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat_id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    action, value_str = query.data.split(":", 1)

    # Handle conversation switching
    if action == "conv":
        conv_id = int(value_str)
        # Save current conversation first
        old_conv_id = context.chat_data.get("conv_id")
        old_history = context.chat_data.get("history", [])
        if old_conv_id and old_history:
            first_msg = next((m["content"][:50] for m in old_history if m["role"] == "user"), "Untitled")
            save_conversation(old_conv_id, old_history, title=first_msg)

        # Load selected conversation
        conv = get_conversation(conv_id)
        if conv:
            context.chat_data["conv_id"] = conv_id
            context.chat_data["history"] = conv["messages"]
            msg_count = len(conv["messages"])
            await query.edit_message_text(f"Resumed: *{conv['title']}* ({msg_count} messages)", parse_mode="Markdown")
        else:
            await query.edit_message_text("Conversation not found.")
        return

    draft_id = int(value_str)
    draft = get_draft(draft_id)

    if not draft:
        await query.edit_message_caption("Draft not found.")
        return

    if action == "post":
        await query.edit_message_caption("Posting to LinkedIn...")
        try:
            post_urn = await asyncio.to_thread(create_post_with_image, draft["content"], draft["image_path"])
            mark_posted(draft_id, post_urn)
            await context.bot.send_message(chat_id, f"Posted to LinkedIn!\nPost ID: {post_urn}")
        except Exception as e:
            await context.bot.send_message(chat_id, f"Failed: {e}")

    elif action == "skip":
        mark_skipped(draft_id)
        await query.edit_message_caption("Skipped.")

    elif action == "regen":
        await context.bot.send_message(chat_id, "Regenerating...")
        mark_skipped(draft_id)
        try:
            new_draft_id = await asyncio.to_thread(run_pipeline)
            if new_draft_id:
                await send_preview(context.bot, chat_id, new_draft_id)
        except Exception as e:
            await context.bot.send_message(chat_id, f"Error: {e}")


MAX_HISTORY = 30  # Keep last 30 messages for context

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any text message — run through the agent."""
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    user_msg = update.message.text
    msg = await update.message.reply_text("Thinking...")

    # Auto-create conversation if none exists
    if "conv_id" not in context.chat_data:
        context.chat_data["conv_id"] = create_conversation(chat_id)
        context.chat_data["history"] = []
    history = context.chat_data["history"]

    try:
        result = await asyncio.to_thread(run_agent_sync, user_msg, history)

        # Show tool usage log
        tool_log = result.get("tool_log", [])
        if tool_log:
            tools_used = []
            for t in tool_log:
                emoji_msg = TOOL_EMOJI.get(t["tool"], f"🔧 {t['tool']}")
                tools_used.append(emoji_msg)
            seen = set()
            unique_tools = []
            for t in tools_used:
                if t not in seen:
                    seen.add(t)
                    unique_tools.append(t)
            await msg.edit_text("\n".join(unique_tools))

        # Get the final result
        final = result.get("result", {})
        agent_text = final.get("text", "")
        draft = final.get("draft")

        # Save to conversation history
        history.append({"role": "user", "content": user_msg})
        if agent_text:
            history.append({"role": "assistant", "content": agent_text})
        # Trim history to last N messages
        if len(history) > MAX_HISTORY:
            context.chat_data["history"] = history[-MAX_HISTORY:]
            history = context.chat_data["history"]

        # Persist to DB
        conv_id = context.chat_data["conv_id"]
        first_user = next((m["content"][:50] for m in history if m["role"] == "user"), None)
        save_conversation(conv_id, history, title=first_user)

        # Send agent's response text
        if agent_text:
            try:
                await msg.edit_text(agent_text, parse_mode="Markdown")
            except Exception:
                await msg.edit_text(agent_text)

        # Send preview if there's a draft
        if draft and draft.get("status") == "draft":
            await send_preview(context.bot, chat_id, draft["id"])

    except Exception as e:
        await msg.edit_text(f"Error: {e}")
        logger.error(f"Agent error: {e}", exc_info=True)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages — transcribe with GPT-5.4 then run agent."""
    chat_id = str(update.effective_chat.id)
    if AUTHORIZED_CHAT_ID and chat_id != AUTHORIZED_CHAT_ID:
        return

    msg = await update.message.reply_text("Listening...")

    voice = update.message.voice or update.message.audio
    voice_file = await voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await voice_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        # Transcribe with GPT-5.4 audio input
        with open(tmp_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-5.4",
            max_completion_tokens=256,
            messages=[
                {"role": "system", "content": "Transcribe this voice message exactly. Return only the transcription."},
                {"role": "user", "content": [
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "ogg"}}
                ]},
            ],
        )
        transcription = response.choices[0].message.content
        await msg.edit_text(f"You said: \"{transcription}\"\n\nProcessing...")

        # Run through agent
        result = await asyncio.to_thread(run_agent_sync, transcription)

        tool_log = result.get("tool_log", [])
        final = result.get("result", {})
        agent_text = final.get("text", "")
        draft = final.get("draft")

        if agent_text:
            try:
                await msg.edit_text(f"You said: \"{transcription}\"\n\n{agent_text}", parse_mode="Markdown")
            except Exception:
                await msg.edit_text(f"You said: \"{transcription}\"\n\n{agent_text}")

        if draft and draft.get("status") == "draft":
            await send_preview(context.bot, chat_id, draft["id"])

    except Exception as e:
        await msg.edit_text(f"Voice error: {e}")
        logger.error(f"Voice error: {e}", exc_info=True)
    finally:
        os.unlink(tmp_path)


async def check_scheduled_posts(bot):
    """Check for posts due to be published and post them."""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now = dt.now(ist)

    scheduled = get_scheduled_posts()
    for post in scheduled:
        try:
            scheduled_time = dt.fromisoformat(post["posted_at"]).replace(tzinfo=ist)
            if now >= scheduled_time:
                logger.info(f"Publishing scheduled post #{post['id']}")
                media_path = post.get("image_path", "")
                if media_path and media_path.endswith(".mp4"):
                    from linkedin import create_post_with_video
                    post_urn = await asyncio.to_thread(create_post_with_video, post["content"], media_path)
                else:
                    post_urn = await asyncio.to_thread(create_post_with_image, post["content"], media_path)
                mark_posted(post["id"], post_urn)
                if AUTHORIZED_CHAT_ID:
                    await bot.send_message(AUTHORIZED_CHAT_ID, f"Scheduled post #{post['id']} published!\nPost ID: {post_urn}")
        except Exception as e:
            logger.error(f"Failed to publish scheduled post #{post['id']}: {e}")
            if AUTHORIZED_CHAT_ID:
                await bot.send_message(AUTHORIZED_CHAT_ID, f"Failed to publish scheduled post #{post['id']}: {e}")


async def daily_generate(bot):
    """Called by scheduler at 8:00 AM IST daily."""
    if not AUTHORIZED_CHAT_ID:
        return
    logger.info("Running daily pipeline...")
    try:
        result = await asyncio.to_thread(
            run_agent_sync,
            "Search for the most interesting AI and tech news today. Create a LinkedIn post about the most compelling trend. Make sure to include source links."
        )
        final = result.get("result", {})
        draft = final.get("draft")
        if draft:
            await send_preview(bot, AUTHORIZED_CHAT_ID, draft["id"])
        else:
            await bot.send_message(AUTHORIZED_CHAT_ID, "Daily generation failed. Check logs.")
    except Exception as e:
        await bot.send_message(AUTHORIZED_CHAT_ID, f"Daily error: {e}")
        logger.error(f"Daily error: {e}", exc_info=True)


def main():
    global scheduler

    if not TELEGRAM_TOKEN:
        print("Error: Set TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("generate", generate_command))
    app.add_handler(CommandHandler("preview", preview_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("scheduled", scheduled_command))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Voice messages
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    # Text messages (catch-all) — runs through agent
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler
    async def post_init(application):
        global scheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            daily_generate,
            CronTrigger(hour=2, minute=30),  # 2:30 AM UTC = 8:00 AM IST
            args=[application.bot],
            id="daily_generate",
            replace_existing=True,
        )
        scheduler.add_job(
            check_scheduled_posts,
            CronTrigger(minute="*"),  # Every minute
            args=[application.bot],
            id="check_scheduled",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started — daily generation at 8:00 AM IST, scheduled posts checked every minute")

    app.post_init = post_init

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
