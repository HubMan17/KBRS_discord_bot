# moduls/activity_commands.py
from __future__ import annotations
import os
import httpx
import discord
from discord.ext import commands
from discord import app_commands

API_BASE = "https://discord.com/api/v10"

# ——— конфиг
ACTIVITY_ID = int(os.getenv("ACTIVITY_ID", "1429576348657254490"))        # твоя активити
DEFAULT_VOICE_ID = int(os.getenv("ACTIVITY_VOICE_CHANNEL_ID", "0"))       # опционально

# ——— HTTP-обертка для инвайта активности
def _create_activity_invite(channel_id: int, bot_token: str, target_app_id: int) -> str:
    """Создаёт invite для embedded-application (activity) в voice-канале и возвращает https://discord.gg/<code>."""
    payload = {
        "max_age": 3600,
        "max_uses": 0,
        "temporary": False,
        "target_type": 2,                         # 2 = EMBEDDED_APPLICATION
        "target_application_id": str(target_app_id),
    }
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=10) as client:
        r = client.post(f"{API_BASE}/channels/{channel_id}/invites", json=payload, headers=headers)
        r.raise_for_status()
        inv = r.json()
    return f"https://discord.gg/{inv['code']}"

# ——— выбор voice-канала
def _resolve_voice_interaction(inter: discord.Interaction, explicit: discord.VoiceChannel | None) -> discord.VoiceChannel | None:
    if isinstance(explicit, discord.VoiceChannel):
        return explicit
    vc = getattr(getattr(inter.user, "voice", None), "channel", None)
    if isinstance(vc, discord.VoiceChannel):
        return vc
    if DEFAULT_VOICE_ID and inter.guild:
        ch = inter.guild.get_channel(DEFAULT_VOICE_ID)
        if isinstance(ch, discord.VoiceChannel):
            return ch
    return None

def _resolve_voice_ctx(ctx: commands.Context, explicit: discord.VoiceChannel | None) -> discord.VoiceChannel | None:
    if isinstance(explicit, discord.VoiceChannel):
        return explicit
    vc = getattr(getattr(ctx.author, "voice", None), "channel", None)
    if isinstance(vc, discord.VoiceChannel):
        return vc
    if DEFAULT_VOICE_ID and ctx.guild:
        ch = ctx.guild.get_channel(DEFAULT_VOICE_ID)
        if isinstance(ch, discord.VoiceChannel):
            return ch
    return None

# ——— публичная инициализация
async def setup_activity_commands(bot: commands.Bot, tree: app_commands.CommandTree) -> None:
    BOT_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set")

    # мгновенная регистрация по гильдиям (если заданы)
    GUILD_IDS = [int(x) for x in os.getenv("SLASH_GUILDS", "").split(",") if x.strip()]
    guild_objs = [discord.Object(id=g) for g in GUILD_IDS]
    guilds_deco = app_commands.guilds(*guild_objs) if guild_objs else (lambda f: f)

    # —— /activity
    @guilds_deco
    @tree.command(name="activity", description="Start your custom activity via invite to a voice channel")
    @app_commands.describe(channel="Voice channel to host the activity (optional — uses your current voice or env default)")
    async def activity_slash(
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.InteractionResponded:
            pass

        if interaction.guild is None:
            return await interaction.followup.send("This works only in a server.", ephemeral=True)

        vch = _resolve_voice_interaction(interaction, channel)
        if not isinstance(vch, discord.VoiceChannel):
            return await interaction.followup.send(
                "Voice channel not resolved. Join a voice channel first or set ACTIVITY_VOICE_CHANNEL_ID in .env.",
                ephemeral=True,
            )

        perms = vch.permissions_for(interaction.guild.me)
        if not perms.create_instant_invite:
            return await interaction.followup.send(f"I need **Create Instant Invite** in {vch.mention}.", ephemeral=True)

        try:
            url = _create_activity_invite(vch.id, BOT_TOKEN, ACTIVITY_ID)
        except Exception as e:
            return await interaction.followup.send(f"Failed to create invite: {e}", ephemeral=True)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Play now!", url=url, style=discord.ButtonStyle.link))
        await interaction.followup.send(
            f"Activity invite for {vch.mention}:\n{url}\n"
            "Click **Play now!** — говорить не нужно, канал только «хостит» активити.",
            ephemeral=True,
            view=view,
        )

    # —— !activity (ОДНА версия)
    @bot.command(name="activity", help="Start activity via invite. Usage: !activity [#voice-channel]")
    async def activity_prefix(ctx: commands.Context, channel: discord.VoiceChannel | None = None):
        if ctx.guild is None:
            return await ctx.send("This works only in a server.")

        vch = _resolve_voice_ctx(ctx, channel)
        if not isinstance(vch, discord.VoiceChannel):
            return await ctx.send("Voice channel not resolved — join a voice channel or set ACTIVITY_VOICE_CHANNEL_ID in .env")

        perms = vch.permissions_for(ctx.guild.me)
        if not perms.create_instant_invite:
            return await ctx.send(f"I need **Create Instant Invite** in {vch.mention}.")

        try:
            url = _create_activity_invite(vch.id, BOT_TOKEN, ACTIVITY_ID)
        except Exception as e:
            return await ctx.send(f"Failed to create invite: {e}")

        await ctx.send(f"Activity invite for {vch.mention}: {url}")
