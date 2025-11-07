import os
import re
import time
import json
import random
import asyncio
import logging
import tempfile
import subprocess
import threading
from pathlib import Path
from importlib import import_module
from datetime import datetime, timezone as tz

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from jinja2 import Template
from playwright.async_api import async_playwright

from api_client import api
from messages import M
from messages import (
    format_multilang_levelup, format_multilang_welcome,
    ROLE_LANG_MAP, LABELS,
    birthday_instr_multilang, birthday_saved_multilang, birthday_invalid_multilang
)
from utils.role_id_land import ROLE_ID_LANG_MAP
from utils.helpers_events import extract_emoji_usage, message_basic_counters

# --- KBRS bridge plugins ---
from kbrs_bridge import setup_bridge
from kbrs_bridge.discord_relay import setup_discord_relay

# ─────────────────────────────────────────────────────────────────────────────
# env & logging
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kbrs.bot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("❌ DISCORD_TOKEN is empty. Set it in .env")

ENABLE_TG_BRIDGE = os.getenv("ENABLE_TG_BRIDGE", "1").strip().lower() in {"1", "true", "yes"}
ENABLE_DISCORD_RELAY = os.getenv("ENABLE_DISCORD_RELAY", "1").strip().lower() in {"1", "true", "yes"}

LEVEL_UP_LOG_CHANNEL_ID = int(os.getenv("LEVEL_UP_LOG_CHANNEL_ID", "0"))
LEVEL_UP_PUBLIC_IN_SAME_CHANNEL = os.getenv("LEVEL_UP_PUBLIC_IN_SAME_CHANNEL", "false").lower() == "true"

WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))

# --- local XP knobs (клиентские эвристики) ---
XP_PER_MESSAGE = (8, 15)
MSG_COOLDOWN = 45

ENABLE_REACTION_XP = True
XP_PER_REACTION = (3, 6)
REACT_COOLDOWN = 45
DISALLOW_SELF_REACT_FARM = True

BONUS_STICKER_XP = 5
BONUS_ATTACHMENT_XP = 8
BONUS_REPLY_XP = 5

# ─────────────────────────────────────────────────────────────────────────────
# intents & bot
# ─────────────────────────────────────────────────────────────────────────────
class GuardedTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Разрешаем в ЛС
        if interaction.guild is None:
            return True

        # Проверяем канал (учитываем треды)
        if _is_channel_allowed(interaction.channel):
            return True

        # Эфемерная подсказка и блок
        hint = "Slash commands are allowed only in: " + (
            await _allowed_mentions(interaction.guild) or "configured channels"
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(hint, ephemeral=True)
            else:
                await interaction.followup.send(hint, ephemeral=True)
        except Exception:
            pass
        return False

intents = discord.Intents.all()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GuardedTree)
tree = bot.tree

# ─────────────────────────────────────────────────────────────────────────────
# plugins (мосты)
# ─────────────────────────────────────────────────────────────────────────────
if ENABLE_TG_BRIDGE:
    bridge = setup_bridge(bot)  # запускает TG polling + планировщик
    log.info("[init] TG bridge enabled")
else:
    log.info("[init] TG bridge DISABLED via env")

if ENABLE_DISCORD_RELAY:
    relay = setup_discord_relay(bot)  # ретрансляция внутри Discord
    log.info("[init] Discord relay enabled")
else:
    log.info("[init] Discord relay DISABLED via env")

# ─────────────────────────────────────────────────────────────────────────────
# slash guild scope
# ─────────────────────────────────────────────────────────────────────────────
GUILD_IDS = [int(x) for x in os.getenv("SLASH_GUILDS", "").split(",") if x.strip().isdigit()]
guild_objs = [discord.Object(id=gid) for gid in GUILD_IDS]
guilds_deco = app_commands.guilds(*guild_objs)

# ─────────────────────────────────────────────────────────────────────────────
# command channel guard
# ─────────────────────────────────────────────────────────────────────────────
ALLOWED_COMMAND_CHANNELS = {
    int(os.getenv("ALLOWED_COMMAND_CHANNELS", 0)),
}
ALLOWED_COMMAND_CHANNELS: set[int] = {c for c in ALLOWED_COMMAND_CHANNELS if c}

def _is_channel_allowed(ch: discord.abc.GuildChannel | None) -> bool:
    if not ALLOWED_COMMAND_CHANNELS:
        return True
    if ch is None:
        return False
    if getattr(ch, "id", None) in ALLOWED_COMMAND_CHANNELS:
        return True
    parent = getattr(ch, "parent", None)
    return bool(parent and getattr(parent, "id", None) in ALLOWED_COMMAND_CHANNELS)

async def _allowed_mentions(guild: discord.Guild | None) -> str:
    if not guild or not ALLOWED_COMMAND_CHANNELS:
        return ""
    mentions = []
    for cid in ALLOWED_COMMAND_CHANNELS:
        ch = guild.get_channel(cid)
        if isinstance(ch, (discord.TextChannel, discord.Thread, discord.ForumChannel)):
            mentions.append(ch.mention)
    return ", ".join(mentions)

@bot.check
async def only_allowed_channels(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return True
    if _is_channel_allowed(ctx.channel):
        return True
    try:
        await ctx.message.add_reaction("🚫")
    except Exception:
        pass
    hint = "Commands are allowed only in: " + (await _allowed_mentions(ctx.guild) or "configured channels")
    try:
        await ctx.reply(hint, delete_after=6, mention_author=False)
    except Exception:
        pass
    return False

async def slash_channel_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return True
    if _is_channel_allowed(interaction.channel):
        return True
    hint = "Slash commands are allowed only in: " + (await _allowed_mentions(interaction.guild) or "configured channels")
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(hint, ephemeral=True)
        else:
            await interaction.followup.send(hint, ephemeral=True)
    except Exception:
        pass
    raise app_commands.CheckFailure("Slash command used in a disallowed channel")

# ─────────────────────────────────────────────────────────────────────────────
# periodic bulk flush
# ─────────────────────────────────────────────────────────────────────────────
_buffer_messages: list[dict] = []
_buffer_reactions: list[dict] = []
_buffer_emoji: list[dict] = []

BUFFER_SAFE_MAX = 2000
FLUSH_PERIOD_SEC = 5 * 60
_periodic_flush_task: asyncio.Task | None = None

def _utc(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.now(tz.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.utc)
    return dt.astimezone(tz.utc).isoformat()

async def _flush_buffers():
    try:
        if _buffer_messages:
            batch = _buffer_messages[:]
            _buffer_messages.clear()
            api.bulk_messages(batch)

        if _buffer_reactions:
            batch = _buffer_reactions[:]
            _buffer_reactions.clear()
            api.bulk_reactions(batch)

        if _buffer_emoji:
            batch = _buffer_emoji[:]
            _buffer_emoji.clear()
            api.bulk_emoji(batch)
    except Exception as e:
        print("[bulk flush] error:", e)

async def _periodic_flush_loop():
    while True:
        await asyncio.sleep(FLUSH_PERIOD_SEC)
        await _flush_buffers()

def _safety_maybe_flush_now():
    total = len(_buffer_messages) + len(_buffer_reactions) + len(_buffer_emoji)
    if total >= BUFFER_SAFE_MAX:
        asyncio.create_task(_flush_buffers())

# ─────────────────────────────────────────────────────────────────────────────
# Playwright & rank rendering
# ─────────────────────────────────────────────────────────────────────────────
CARD_SCRIPT = Path(__file__).parent / "render_card.py"
CARD_TEMPLATE = Path(__file__).parent / "card_dynamic.html"

PLAY = None
BROWSER = None
RANK_TEMPLATE = None

def now() -> float:
    return time.time()

def _fmt_hhmm_ddmmyyyy(dt: datetime | None) -> str:
    if not dt:
        return "—"
    local = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=tz.utc).astimezone()
    return local.strftime("%H:%M • %d.%m.%Y")

def _initials_from_name(name: str) -> str:
    parts = [p for p in name.split() if p.strip()]
    if not parts:
        s = (name or "U")[:2]
        return s.upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()

async def _render_rank_card_async(username: str, level: int, value: int, maxv: int,
                                  rank: int, top: int, avatar_url: str | None) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="rankcard_"))
    out_png = tmpdir / "card.png"
    data_json = tmpdir / "data.json"

    payload = {
        "username": username,
        "initials": _initials_from_name(username),
        "avatar": avatar_url or None,
        "level": int(level),
        "value": int(value),
        "max": int(maxv),
        "rank": int(rank),
        "top": int(top),
    }
    data_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    import sys
    cmd = [
        sys.executable, str(CARD_SCRIPT),
        "--template", str(CARD_TEMPLATE),
        "--out", str(out_png),
        "--data", str(data_json),
    ]

    def _run():
        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"render_card.py failed: {completed.returncode}\n"
                f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        return out_png

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run)

# ─────────────────────────────────────────────────────────────────────────────
# events
# ─────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    global PLAY, BROWSER, RANK_TEMPLATE
    print(f"✅ Logged in as {bot.user} (prefix '!')")

    # шаблон
    try:
        RANK_TEMPLATE = Template(Path(CARD_TEMPLATE).read_text(encoding="utf-8"))
        print("✅ Rank card template loaded")
    except Exception as e:
        print("❌ Failed to load rank template:", e)

    # браузер
    try:
        if PLAY is None:
            PLAY = await async_playwright().start()
        if BROWSER is None:
            BROWSER = await PLAY.chromium.launch()  # headless по умолчанию
        print("✅ Chromium ready (persistent)")
    except Exception as e:
        print("❌ Chromium start failed:", e)

    # команды
    await import_module("moduls.command_top").setup_top_commands(bot, tree)
    await import_module("moduls.rank_commands").setup_rank_commands(bot, tree, PLAY, BROWSER, RANK_TEMPLATE)
    await import_module("moduls.bday_commands").setup_bday_commands(bot, tree)
    await import_module("moduls.stats_command").setup_stats_commands(bot, tree)
    await import_module("moduls.fact_commands").setup_fact_commands(bot, tree)
    await import_module("moduls.activity_commands").setup_activity_commands(bot, tree)
    await import_module("moduls.help_command").setup_help_commands(bot, tree)
    await import_module("moduls.userinfo_command").setup_userinfo_commands(bot, tree)
    await import_module("moduls.achievements_command").setup_achievements_commands(bot, tree)

    # sync slash (опционально)
    if os.getenv("SYNC_SLASH", "false").lower() == "true":
        try:
            for gid in GUILD_IDS:
                await bot.tree.sync(guild=discord.Object(id=gid))
            await bot.tree.sync()
        except Exception as e:
            print("❌ Slash sync error:", e)

    # API авторизация
    try:
        api.ensure_token()
        print("✅ API token acquired")
    except Exception as e:
        print("❌ API auth failed:", e)

    # periodic flush
    global _periodic_flush_task
    if _periodic_flush_task is None or _periodic_flush_task.done():
        _periodic_flush_task = asyncio.create_task(_periodic_flush_loop())
        print("⏱️ started periodic 5-min flush")

    # единый гард
    try:
        if not getattr(tree, "_channel_guard_added", False):
            setattr(tree, "_channel_guard_added", True)
            print("✅ Unified slash channel guard enabled")
    except Exception as e:
        print("⚠️ add_check failed:", e)

    # лог-канал доступность
    try:
        ch = bot.get_channel(LEVEL_UP_LOG_CHANNEL_ID) or await bot.fetch_channel(LEVEL_UP_LOG_CHANNEL_ID)
        if not ch:
            print("[level-up] logs channel not resolved at startup")
    except Exception as e:
        print("[level-up] fetch logs channel failed:", e)

# ─────────────────────────────────────────────────────────────────────────────
# message XP
# ─────────────────────────────────────────────────────────────────────────────
msg_cooldowns: dict[int, float] = {}
react_cooldowns: dict[int, float] = {}
reacted_awarded: set[tuple[int, int]] = set()

async def send_levelup_to_logs(guild: discord.Guild, content: str) -> bool:
    cid = LEVEL_UP_LOG_CHANNEL_ID
    if not cid:
        print("[level-up] no LEVEL_UP_LOG_CHANNEL_ID set")
        return False

    ch = guild.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception as e:
            print(f"[level-up] fetch_channel({cid}) failed:", e)
            return False

    try:
        perms = ch.permissions_for(ch.guild.me)
        if not perms.view_channel or not perms.send_messages:
            print(f"[level-up] insufficient perms in logs channel {cid}: "
                  f"view={perms.view_channel}, send={perms.send_messages}")
            return False
        await ch.send(content)
        return True
    except discord.Forbidden:
        print(f"[level-up] Forbidden: no permission to send in channel {cid}")
    except discord.HTTPException as e:
        print(f"[level-up] HTTPException while sending to {cid}: {e}")
    except Exception as e:
        print(f"[level-up] unexpected error while sending to {cid}: {e}")
    return False

@bot.event
async def on_message(message: discord.Message):
    # игнор ботов
    if message.author.bot:
        return

    # DM
    if message.guild is None:
        await bot.process_commands(message)
        return

    # сырые метрики
    c = message_basic_counters(message)
    _buffer_messages.append({
        "guild_id": message.guild.id,
        "channel_id": message.channel.id,
        "message_id": message.id,
        "author_id": message.author.id,
        "words": c["words"],
        "chars": c["chars"],
        "has_attach": c["has_attach"],
        "has_sticker": c["has_sticker"],
        "is_reply": c["is_reply"],
        "emoji_count": c["emoji_count"],
        "link_count": c["link_count"],
        "mention_cnt": c["mention_cnt"],
        "created_at": _utc(message.created_at),
    })

    # эмодзи/стикеры из текста
    usage = extract_emoji_usage(message.content or "")
    for key, count in usage.items():
        _buffer_emoji.append({
            "guild_id": message.guild.id,
            "message_id": message.id,
            "sender_id": message.author.id,
            "key": key,
            "count": int(count),
            "created_at": _utc(message.created_at),
        })

    # стикеры как события
    for st in message.stickers:
        _buffer_emoji.append({
            "guild_id": message.guild.id,
            "message_id": message.id,
            "sender_id": message.author.id,
            "key": f"sticker:{st.id}",
            "count": 1,
            "created_at": _utc(message.created_at),
        })

    # начисление XP
    uid = message.author.id
    tnow = time.time()
    if msg_cooldowns.get(uid, 0) + MSG_COOLDOWN <= tnow:
        msg_cooldowns[uid] = tnow

        base = random.randint(*XP_PER_MESSAGE)
        bonus = 0
        if message.stickers:
            bonus += BONUS_STICKER_XP
        if message.attachments:
            bonus += BONUS_ATTACHMENT_XP
        if message.reference:
            bonus += BONUS_REPLY_XP

        amount = base + bonus
        try:
            data = api.add_xp(
                discord_id=uid,
                username=message.author.display_name,
                amount=amount,
                source="message",
                guild_id=message.guild.id,
            )
            if data.get("leveled_up"):
                msg_text = format_multilang_levelup(
                    mention=message.author.mention,
                    level=data.get("level", 0)
                )
                ok = await send_levelup_to_logs(message.guild, msg_text)
                if not ok and LEVEL_UP_PUBLIC_IN_SAME_CHANNEL:
                    try:
                        await message.channel.send(
                            f"🎉 {message.author.mention} reached **level {data.get('level', 0)}!**"
                        )
                    except discord.Forbidden:
                        print("[level-up] No permission to send in current channel")
                    except Exception as e:
                        print("[level-up] Fallback send failed:", e)

            # Check achievements after XP award
            try:
                api.check_achievements(uid, trigger_event="message_sent")
            except Exception as e:
                print(f"[achievements] check_achievements(message) error: {e}")
        except Exception as e:
            print("add_xp(message) error:", e)

    _safety_maybe_flush_now()
    await bot.process_commands(message)

# ─────────────────────────────────────────────────────────────────────────────
# reaction XP
# ─────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    if payload.emoji.is_unicode_emoji():
        emoji_key = payload.emoji.name
    else:
        emoji_key = f"{'a:' if payload.emoji.animated else ''}{payload.emoji.name}:{payload.emoji.id}"

    author_id = None
    ch = bot.get_channel(payload.channel_id)
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        try:
            msg = await ch.fetch_message(payload.message_id)
            author_id = msg.author.id if msg and msg.author else None
        except Exception:
            pass

    _buffer_reactions.append({
        "guild_id": payload.guild_id,
        "channel_id": payload.channel_id,
        "message_id": payload.message_id,
        "reactor_id": payload.user_id,
        "author_id": author_id,
        "emoji_key": emoji_key,
        "created_at": _utc(),
    })

    key = (payload.message_id, member.id)
    if key in reacted_awarded:
        return
    tnow = time.time()
    if react_cooldowns.get(member.id, 0) + REACT_COOLDOWN > tnow:
        return

    if DISALLOW_SELF_REACT_FARM and author_id == member.id:
        return

    react_cooldowns[member.id] = tnow
    reacted_awarded.add(key)

    amount = random.randint(*XP_PER_REACTION)
    try:
        data = api.add_xp(
            discord_id=member.id,
            username=member.display_name,
            amount=amount,
            source="reaction",
            guild_id=member.guild.id,
        )
        if data.get("leveled_up"):
            msg = format_multilang_levelup(
                mention=member.mention,
                level=data.get("level", 0)
            )
            ok = await send_levelup_to_logs(member.guild, msg)
            if not ok and LEVEL_UP_PUBLIC_IN_SAME_CHANNEL:
                ch = bot.get_channel(payload.channel_id)
                if isinstance(ch, (discord.TextChannel, discord.Thread)):
                    try:
                        await ch.send(f"🎉 {member.mention} reached **level {data.get('level', 0)}!**")
                    except Exception as e:
                        print("[level-up] fallback public send failed:", e)

        # Check achievements after XP award
        try:
            api.check_achievements(member.id, trigger_event="reaction_added")
        except Exception as e:
            print(f"[achievements] check_achievements(reaction) error: {e}")
    except Exception as e:
        print("add_xp(reaction) error:", e)

    _safety_maybe_flush_now()

# ─────────────────────────────────────────────────────────────────────────────
# welcome
# ─────────────────────────────────────────────────────────────────────────────
async def get_welcome_channel(guild: discord.Guild) -> discord.abc.GuildChannel | None:
    cid = WELCOME_CHANNEL_ID
    if not cid:
        print("[welcome] WELCOME_CHANNEL_ID is not set")
        return None
    ch = guild.get_channel(cid) or bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception as e:
            print(f"[welcome] fetch_channel({cid}) failed:", e)
    return ch

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot or not member.guild:
        return

    try:
        api.sync_members([{"discord_id": member.id, "username": member.display_name}])
    except Exception as e:
        print("[welcome] sync_members error:", e)

    # Check join_server achievement
    try:
        api.check_achievements(member.id, trigger_event="member_join")
    except Exception as e:
        print(f"[achievements] check_achievements(join) error: {e}")

    try:
        ch = await get_welcome_channel(member.guild)
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            msg = format_multilang_welcome(member.mention)
            await ch.send(msg)
        else:
            print(f"[welcome] channel {WELCOME_CHANNEL_ID} not found or wrong type")
    except discord.Forbidden:
        print(f"[welcome] no SEND permission in channel {WELCOME_CHANNEL_ID}")
    except Exception as e:
        print("[welcome] send error:", e)

# ─────────────────────────────────────────────────────────────────────────────
# error handler
# ─────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if not isinstance(error, commands.CheckFailure):
        return
    if ctx.guild is None:
        return
    try:
        await ctx.message.add_reaction("🚫")
    except Exception:
        pass
    if ALLOWED_COMMAND_CHANNELS:
        mentions = []
        for cid in ALLOWED_COMMAND_CHANNELS:
            ch = ctx.guild.get_channel(cid) or bot.get_channel(cid)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                mentions.append(ch.mention)
        if mentions:
            try:
                await ctx.reply(
                    f"Commands are allowed only in: {', '.join(mentions)}",
                    delete_after=6,
                    mention_author=False,
                )
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# admin utils
# ─────────────────────────────────────────────────────────────────────────────
@bot.command(name="sync_members", help="Add all non-bot members to the XP system (admin only)")
@commands.has_permissions(administrator=True)
async def sync_members(ctx: commands.Context):
    payload = [{"discord_id": m.id, "username": m.display_name} for m in ctx.guild.members if not m.bot]
    try:
        res = api.sync_members(payload)
        await ctx.send(M["sync_done"].format(n=res.get("added", 0)))
    except Exception as e:
        print("sync_members error:", e)
        await ctx.send("Service unavailable.")

# ─────────────────────────────────────────────────────────────────────────────
# entrypoint
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
