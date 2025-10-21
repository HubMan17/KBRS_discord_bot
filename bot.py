import os, random, time
import re
import random

from importlib import import_module

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

import asyncio, json, tempfile, subprocess
from pathlib import Path

from playwright.async_api import async_playwright
from jinja2 import Template

from api_client import api
from messages import M

""""""
from moduls.command_top import setup_top_commands
from moduls.rank_commands import setup_rank_commands
from moduls.stats_command import setup_stats_commands
from moduls.fact_commands import setup_fact_commands
from moduls.activity_commands import setup_activity_commands
""""""

from messages import (
    format_multilang_levelup, format_multilang_welcome,
    ROLE_LANG_MAP, LABELS,
    birthday_instr_multilang, birthday_saved_multilang, birthday_invalid_multilang
)
from datetime import datetime, timezone as tz
from utils.role_id_land import ROLE_ID_LANG_MAP
from utils.helpers_events import extract_emoji_usage, message_basic_counters

# from utils.guardedtree import GuardedTree

# ── config ───────────────────────────────────────────────────────────────────
load_dotenv()

LEVEL_UP_LOG_CHANNEL_ID = int(os.getenv("LEVEL_UP_LOG_CHANNEL_ID", "0"))
LEVEL_UP_PUBLIC_IN_SAME_CHANNEL = os.getenv("LEVEL_UP_PUBLIC_IN_SAME_CHANNEL", "false").lower() == "true"

MENTION_REPLY_COOLDOWN = 10  # сек между ответами одному юзеру
MENTION_EMOJI_ID = int(os.getenv("MENTION_EMOJI_ID", "0"))
MENTION_STICKER_ID = int(os.getenv("MENTION_STICKER_ID", "0"))

WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))

mention_cd: dict[int, float] = {}

# local anti-spam knobs (бот считает amount, но «истина» — на бэке)
XP_PER_MESSAGE = (5, 15)
MSG_COOLDOWN = 60

ENABLE_REACTION_XP = True
XP_PER_REACTION = (2, 6)
REACT_COOLDOWN = 20
DISALLOW_SELF_REACT_FARM = True

BONUS_STICKER_XP = 5
BONUS_ATTACHMENT_XP = 3
BONUS_REPLY_XP = 2

# ── env / bot ────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")

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
        return False  # отменить выполнение команды


intents = discord.Intents.default()
intents = discord.Intents.all()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GuardedTree)
tree = bot.tree

GUILD_IDS = [int(x) for x in os.getenv("SLASH_GUILDS", "").split(",") if x.strip()]

# создаем дерево для слэш-команд
tree = bot.tree

# Буферы сырых событий
_buffer_messages: list[dict] = []
_buffer_reactions: list[dict] = []
_buffer_emoji: list[dict] = []

# Безопасный предел на случай бурного чата
BUFFER_SAFE_MAX = 2000
FLUSH_PERIOD_SEC = 5 * 60  # каждые 5 минут
_periodic_flush_task: asyncio.Task | None = None

# --- only allow commands in specific channels ---
ALLOWED_COMMAND_CHANNELS = {
    int(os.getenv("ALLOWED_COMMAND_CHANNELS", 0)),
    # 1429513548899422338
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
    if ctx.guild is None:  # DM разрешаем
        return True
    if _is_channel_allowed(ctx.channel):
        return True
    # в «не том» канале — поставим реакцию и мягкую подсказку
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

# --- сам чек (как раньше)
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

# local cooldowns (UI snappiness; защита от спама API)
msg_cooldowns: dict[int, float] = {}
react_cooldowns: dict[int, float] = {}
reacted_awarded: set[tuple[int, int]] = set()

# generate rang
CARD_SCRIPT = Path(__file__).parent / "render_card.py"
CARD_TEMPLATE = Path(__file__).parent / "card_dynamic.html"

PLAY = None
BROWSER = None
RANK_TEMPLATE = None

DATE_RE_YMD = re.compile(r"^\d{2}-\d{2}-\d{4}$")
DATE_RE_MD  = re.compile(r"^\d{2}-\d{2}$")
DATE_RE = re.compile(r"^(\d{2})[-.](\d{2})(?:[-.](\d{4}))?$")  # DD-MM, DD-MM-YYYY, DD.MM, DD.MM.YYYY

guild_objs = [discord.Object(id=gid) for gid in GUILD_IDS]
guilds_deco = app_commands.guilds(*guild_objs)

def now() -> float:
    return time.time()

def _parse_iso_z(s: str | None) -> datetime | None:
    """'2025-10-18T21:27:06.383000Z' -> aware datetime (UTC)."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _fmt_hhmm_ddmmyyyy(dt: datetime | None) -> str:
    if not dt:
        return "—"
    # показываем во временной зоне машины, чтобы «человечнее»
    local = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=tz.utc).astimezone()
    return local.strftime("%H:%M • %d.%m.%Y")

@bot.event
async def on_ready():
    global PLAY, BROWSER, RANK_TEMPLATE
    print(f"✅ Logged in as {bot.user} (prefix '!')")

    # Загружаем HTML-шаблон один раз
    try:
        RANK_TEMPLATE = Template(Path(CARD_TEMPLATE).read_text(encoding="utf-8"))
        print("✅ Rank card template loaded")
    except Exception as e:
        print("❌ Failed to load rank template:", e)

    # Поднимаем Playwright браузер один раз
    try:
        if PLAY is None:
            PLAY = await async_playwright().start()
        if BROWSER is None:
            BROWSER = await PLAY.chromium.launch()  # можно add headless=False для отладки
        print("✅ Chromium ready (persistent)")
    except Exception as e:
        print("❌ Chromium start failed:", e)

    """"""
    await setup_top_commands(bot, tree)
    await setup_rank_commands(bot, tree, PLAY, BROWSER, RANK_TEMPLATE)
    
    bmod = import_module("moduls.bday_commands")
    await bmod.setup_bday_commands(bot, tree)
    
    await setup_stats_commands(bot, tree)
    await setup_fact_commands(bot, tree)
    await setup_activity_commands(bot, tree)
    """"""  
        
    DO_SYNC = os.getenv("SYNC_SLASH", "false").lower() == "true"
    if DO_SYNC:
        try:
            # 1) сначала синк по гильдиям (быстро появляется)
            for gid in GUILD_IDS:
                cmds = await bot.tree.sync(guild=discord.Object(id=gid))
                print(f"🔁 Guild {gid} slash synced: {[c.name for c in cmds]}")

            # 2) потом глобальный — чтобы через время подтянулся везде
            gcmds = await bot.tree.sync()
            print(f"🌐 Global slash synced: {[c.name for c in gcmds]}")
        except Exception as e:
            print("❌ Slash sync error:", e)

    try:
        g = await bot.tree.fetch_commands()
        print("Global slash now:", [(c.name, c.id) for c in g])
    except Exception as e:
        print("fetch global failed:", e)

    try:
        cmds = await bot.tree.fetch_commands()
        print("Slash now registered:", [(c.name, c.id) for c in cmds])
    except Exception as e:
        print("fetch_commands failed:", e)

    # API авторизация
    try:
        api.ensure_token()
        print("✅ API token acquired")
    except Exception as e:
        print("❌ API auth failed:", e)

    global _periodic_flush_task
    if _periodic_flush_task is None or _periodic_flush_task.done():
        _periodic_flush_task = asyncio.create_task(_periodic_flush_loop())
        print("⏱️ started periodic 5-min flush")

    try:
        if not getattr(tree, "_channel_guard_added", False):
            # tree.add_check(slash_channel_check)
            setattr(tree, "_channel_guard_added", True)
            print("✅ Unified slash channel guard enabled")
    except Exception as e:
        print("⚠️ add_check failed:", e)

    try:
        ch = bot.get_channel(LEVEL_UP_LOG_CHANNEL_ID) or await bot.fetch_channel(LEVEL_UP_LOG_CHANNEL_ID)
        if ch:
            perms = ch.permissions_for(ch.guild.me)
        else:
            print("[level-up] logs channel not resolved at startup")
    except Exception as e:
        print("[level-up] fetch logs channel failed:", e)

def _utc(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.now(tz.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.utc)
    return dt.astimezone(tz.utc).isoformat()

async def _flush_buffers():
    """Отправить все накопленные буферы батчами (если они не пустые)."""
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
        # В случае ошибки не теряем данные: вернём их в буфер
        print("[bulk flush] error:", e)
        # (на случай частичного отправления, здесь простое возвращение — ок)
        # Лучше иметь очередь-диск на будущее.
        # Ничего не возвращаем, чтобы таймер продолжал работать.

async def _periodic_flush_loop():
    """Фоновая задача: каждые 5 минут флашит буферы."""
    while True:
        await asyncio.sleep(FLUSH_PERIOD_SEC)
        await _flush_buffers()

def _safety_maybe_flush_now():
    """Подстраховка: при лавине сообщений — флашим сразу, чтобы не раздуло память."""
    total = len(_buffer_messages) + len(_buffer_reactions) + len(_buffer_emoji)
    if total >= BUFFER_SAFE_MAX:
        # Запускаем быстрый флаш, но не await — чтобы не блокировать текущий обработчик
        asyncio.create_task(_flush_buffers())

async def send_levelup_to_logs(guild: discord.Guild, content: str) -> bool:
    """Пытается отправить `content` в канал LEVEL_UP_LOG_CHANNEL_ID. Возвращает True при успехе."""
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

    # не фильтруем тип — пробуем отправить и печатаем понятную ошибку
    try:
        perms = ch.permissions_for(guild.me)
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

# ——— Message XP ——————————————————————————————————————————————————————————————
async def get_log_channel(guild: discord.Guild) -> discord.abc.GuildChannel | None:
    cid = LEVEL_UP_LOG_CHANNEL_ID
    if not cid:
        print("[level-up] LEVEL_UP_LOG_CHANNEL_ID is not set")
        return None
    ch = guild.get_channel(cid) or bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception as e:
            print(f"[level-up] fetch_channel({cid}) failed:", e)
    return ch

@bot.event
async def on_message(message: discord.Message):
    # 0) никогда не реагируем на ботов
    if message.author.bot:
        return

    # 1) ЛС (DM): ничего не логируем, просто пропускаем в обработчик команд
    if message.guild is None:
        await bot.process_commands(message)
        return

    # ===== ниже — только серверные сообщения =====

    # 2) Сырые метрики сообщения
    c = message_basic_counters(message)  # words/chars/emoji_count/link_count/mention_cnt/flags
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

    # 3) Детализация по эмодзи/стикерам в тексте
    usage = extract_emoji_usage(message.content or "")
    for key, count in usage.items():
        _buffer_emoji.append({
            "guild_id": message.guild.id,
            "message_id": message.id,
            "sender_id": message.author.id,
            "key": key,               # юникод-эмодзи либо "name:id" / "a:name:id"
            "count": int(count),
            "created_at": _utc(message.created_at),
        })

    # 4) Стикеры (кастомные/стандартные)
    for st in message.stickers:  # StickerItem
        _buffer_emoji.append({
            "guild_id": message.guild.id,
            "message_id": message.id,
            "sender_id": message.author.id,
            "key": f"sticker:{st.id}",
            "count": 1,
            "created_at": _utc(message.created_at),
        })

    # 5) Антиспам-кулдаун по сообщениям + начисление XP через бэк
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
        except Exception as e:
            print("add_xp(message) error:", e)

    # 6) Подстраховочный быстрый флаш, если буферы раздуло
    _safety_maybe_flush_now()

    # 7) Всегда пропускаем команды для серверных сообщений
    await bot.process_commands(message)

# ——— Reaction XP ————————————————————————————————————————————————————————————
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

    # emoji_key: unicode или name:id / a:name:id
    if payload.emoji.is_unicode_emoji():
        emoji_key = payload.emoji.name  # сам юникод-символ
    else:
        emoji_key = f"{'a:' if payload.emoji.animated else ''}{payload.emoji.name}:{payload.emoji.id}"

    # автор сообщения (стараемся получить)
    author_id = None
    ch = bot.get_channel(payload.channel_id)
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        try:
            msg = await ch.fetch_message(payload.message_id)
            author_id = msg.author.id if msg and msg.author else None
        except Exception:
            pass

    # 1) лог реакции
    _buffer_reactions.append({
        "guild_id": payload.guild_id,
        "channel_id": payload.channel_id,
        "message_id": payload.message_id,
        "reactor_id": payload.user_id,
        "author_id": author_id,
        "emoji_key": emoji_key,
        "created_at": _utc(),  # сейчас
    })

    # 2) Анти-фарм и кулдаун по реакциям
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

    # 3) Начисление XP за реакцию через бэк
    amount = random.randint(*XP_PER_REACTION)
    try:
        data = api.add_xp(
            discord_id=member.id,
            username=member.display_name,
            amount=amount,
            source="reaction",
            guild_id=member.guild.id,         # <-- добавили
        )
        if data.get("leveled_up"):
            msg = format_multilang_levelup(
                mention=member.mention,
                level=data.get("level", 0)
            )
            ok = await send_levelup_to_logs(member.guild, msg)
            if not ok:
                # fallback — дублируем в том же канале, где стояла реакция
                if LEVEL_UP_PUBLIC_IN_SAME_CHANNEL:
                    ch = bot.get_channel(payload.channel_id)
                    if isinstance(ch, (discord.TextChannel, discord.Thread)):
                        try:
                            await ch.send(f"🎉 {member.mention} reached **level {data.get('level', 0)}!**")
                        except Exception as e:
                            print("[level-up] fallback public send failed:", e)
    except Exception as e:
        print("add_xp(reaction) error:", e)

    # 4) Подстраховочный флаш при распухших буферах
    _safety_maybe_flush_now()


def _render_emoji(emoji_key: str) -> str:
    """Преобразует stored key в вид, который Discord правильно покажет."""
    if not emoji_key:
        return "—"
    # custom:  name:id  or  a:name:id
    parts = emoji_key.split(":")
    if len(parts) == 2:            # name:id
        name, eid = parts
        return f"<:{name}:{eid}>"
    if len(parts) == 3 and parts[0] == "a":  # animated
        _, name, eid = parts
        return f"<a:{name}:{eid}>"
    # unicode
    return emoji_key

async def _mention(guild: discord.Guild, uid: int) -> str:
    """Безопасно получить упоминание участника по ID."""
    if not guild or not uid:
        return f"<@{uid}>"
    m = guild.get_member(uid)
    if m:
        return m.mention
    try:
        user = await guild.fetch_member(uid)
        return user.mention
    except Exception:
        return f"<@{uid}>"


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
    # игнор ботов
    if member.bot or not member.guild:
        return

    # 1) добавить/обновить профиль на бэке
    try:
        # переиспользуем уже существующий эндпоинт батч-синхронизации, но с одним участником
        api.sync_members([{"discord_id": member.id, "username": member.display_name}])
    except Exception as e:
        print("[welcome] sync_members error:", e)

    # 2) отправить приветствие в welcome-канал
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

# ——— Commands ——————————————————————————————————————————————————————————————
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
    """Запускает render_card.py в отдельном процессе, чтобы не блокировать event loop."""
    tmpdir = Path(tempfile.mkdtemp(prefix="rankcard_"))
    out_png = tmpdir / "card.png"
    data_json = tmpdir / "data.json"

    payload = {
        "username": username,
        "initials": _initials_from_name(username),
        "avatar": avatar_url or None,   # можно передавать прямиком URL аватарки
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

# ——— Error handling ————————————————————————————————————————————————
# --- обработчик ошибок: красиво гасим CheckFailure
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    # игнор других ошибок (пусть логируются стандартно)
    if not isinstance(error, commands.CheckFailure):
        return

    # если это DM — не напоминаем (в DM всё разрешено)
    if ctx.guild is None:
        return

    # ещё раз попробуем поставить реакцию на всякий случай
    try:
        await ctx.message.add_reaction("🚫")
    except Exception:
        pass

    # опционально — подсказка, куда можно писать команды
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

@bot.command(name="sync_members", help="Add all non-bot members to the XP system (admin only)")
@commands.has_permissions(administrator=True)
async def sync_members(ctx: commands.Context):
    payload = []
    for m in ctx.guild.members:
        if m.bot:
            continue
        payload.append({"discord_id": m.id, "username": m.display_name})
    try:
        res = api.sync_members(payload)
        await ctx.send(M["sync_done"].format(n=res.get("added", 0)))
    except Exception as e:
        print("sync_members error:", e)
        await ctx.send("Service unavailable.")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN is empty. Set it in .env")
    
    bot.run(TOKEN)
