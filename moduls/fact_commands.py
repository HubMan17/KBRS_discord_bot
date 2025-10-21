# moduls/fact_commands.py
from __future__ import annotations
from datetime import datetime, timezone as tz
import os, random
import discord
from discord import app_commands
from discord.ext import commands
from api_client import api

# -------- helpers (локальные копии, чтобы модуль был самодостаточным)

def _render_emoji(emoji_key: str) -> str:
    """Преобразует stored key в вид, который Discord правильно покажет."""
    if not emoji_key:
        return "—"
    parts = emoji_key.split(":")
    if len(parts) == 2:
        name, eid = parts
        return f"<:{name}:{eid}>"
    if len(parts) == 3 and parts[0] == "a":
        _, name, eid = parts
        return f"<a:{name}:{eid}>"
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

# -------- ядро вывода

async def _pick_fact_text(guild: discord.Guild, period: str) -> str:
    try:
        data = api.server_highlights(guild.id, range=period)
    except Exception as e:
        print("fact error:", e)
        return "Service unavailable."

    facts: list[str] = []

    if data.get("top_senders"):
        top = data["top_senders"][0]
        facts.append(
            f"Most messages {period}: {(await _mention(guild, top['author_id']))} — **{top['count']}**."
        )

    if data.get("top_reactors"):
        tr = data["top_reactors"][0]
        facts.append(
            f"Top reactor {period}: {(await _mention(guild, tr['reactor_id']))} — **{tr['count']}** reactions."
        )

    if data.get("top_received"):
        rc = data["top_received"][0]
        facts.append(
            f"Most reacted-to {period}: {(await _mention(guild, rc['author_id']))} — **{rc['count']}** reactions received."
        )

    if data.get("top_emoji"):
        te = data["top_emoji"][0]
        facts.append(
            f"Most used emoji {period}: {_render_emoji(te['emoji_key'])} — **{te['count']}** times."
        )

    if data.get("xp_earners"):
        xe = data["xp_earners"][0]
        facts.append(
            f"Top XP earner {period}: {(await _mention(guild, xe['discord_id']))} — **+{xe['amount']} XP**."
        )

    if data.get("noisy_channel_id"):
        ch = guild.get_channel(data["noisy_channel_id"])
        if ch:
            facts.append(f"Noisiest channel {period}: {ch.mention}.")

    if not facts:
        facts = [f"No highlights for {period} yet — be the first to make some noise!"]

    return "🎯 " + random.choice(facts)

# -------- публичная точка подключения

VALID_PERIODS = {"day", "week", "month", "all"}

async def setup_fact_commands(bot: commands.Bot, tree: app_commands.CommandTree) -> None:
    # читаем гильдии для локальной регистрации слэшей (мгновенное появление)
    GUILD_IDS = [int(x) for x in os.getenv("SLASH_GUILDS", "").split(",") if x.strip()]
    guild_objs = [discord.Object(id=g) for g in GUILD_IDS]
    guilds_deco = app_commands.guilds(*guild_objs) if guild_objs else (lambda f: f)

    # --- префикс: !fact [day|week|month|all], по умолчанию day
    @bot.command(name="fact", help="Show a random highlight fact for today (or !fact week/month/all)")
    async def fact_prefix(ctx: commands.Context, period: str = "day"):
        if ctx.guild is None:
            return await ctx.send("This command can be used in a server only.")
        p = (period or "day").lower()
        if p not in VALID_PERIODS:
            p = "day"
        text = await _pick_fact_text(ctx.guild, p)
        await ctx.send(text)

    # --- слэш: /fact period=day/week/month/all, ephemeral=bool
    @guilds_deco
    @tree.command(name="fact", description="Show a random highlight fact (day/week/month/all)")
    @app_commands.describe(
        period="day / week / month / all",
        ephemeral="Reply visible only to you"
    )
    async def fact_slash(
        interaction: discord.Interaction,
        period: str = "day",
        ephemeral: bool = True
    ):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        except discord.InteractionResponded:
            pass

        p = (period or "day").lower()
        if p not in VALID_PERIODS:
            p = "day"

        if interaction.guild is None:
            return await interaction.followup.send("This command can be used in a server only.", ephemeral=True)

        text = await _pick_fact_text(interaction.guild, p)
        await interaction.followup.send(text, ephemeral=ephemeral)
