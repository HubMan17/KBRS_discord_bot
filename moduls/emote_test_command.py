# moduls/emote_test_command.py
import random as _random
import discord
from discord.ext import commands

# ================================================================
# ПРОСТАЯ РАНДОМНАЯ РЕАКЦИЯ
# ================================================================

# вероятность поставить реакцию (0.02 = 2%)
AUTO_REACT_PROB = 0.02  

# кастомные эмодзи (подставь реальные ID)
FRIERUH_ID = 1349779091036831874   # <:frieruh:...>
NOTED_ID   = 1395905755345060001   # <:noted:...>


def _resolve_emoji_anywhere(bot: commands.Bot, guild: discord.Guild | None, name: str | None = None, eid: int | None = None):
    """Пытается найти эмодзи по ID или имени в текущей гильдии или среди всех доступных."""
    em = None
    if guild:
        if eid:
            em = discord.utils.get(guild.emojis, id=int(eid))
        if not em and name:
            em = discord.utils.get(guild.emojis, name=name)
    if not em and eid:
        em = discord.utils.get(bot.emojis, id=int(eid))
    if not em and name:
        em = discord.utils.get(bot.emojis, name=name)
    return em


def _build_react_pool(bot: commands.Bot, guild: discord.Guild | None):
    """Создаёт список возможных реакций (👀 + кастомные эмодзи)."""
    pool = ["👀"]

    fri = _resolve_emoji_anywhere(bot, guild, name="frieruh", eid=FRIERUH_ID)
    if fri:
        pool.append(fri)

    noted = _resolve_emoji_anywhere(bot, guild, name="noted", eid=NOTED_ID)
    if noted:
        pool.append(noted)

    return pool


async def maybe_auto_react(bot: commands.Bot, message: discord.Message):
    """С небольшой вероятностью ставит одну случайную реакцию."""
    try:
        # пропускаем сообщения от ботов
        if message.author.bot:
            return

        # вероятность (2% по умолчанию)
        if _random.random() >= AUTO_REACT_PROB:
            return

        # собираем пул реакций
        pool = _build_react_pool(bot, message.guild)
        if not pool:
            return

        choice = _random.choice(pool)
        perms = message.channel.permissions_for(message.guild.me)
        if perms.add_reactions and perms.read_message_history:
            await message.add_reaction(choice)

    except Exception:
        pass
