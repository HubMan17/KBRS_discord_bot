# moduls/stats_command.py
from __future__ import annotations
from datetime import datetime, timezone as tz
import os
import discord
from discord import app_commands
from discord.ext import commands
from api_client import api

def _parse_iso_z(s: str | None) -> datetime | None:
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
    local = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=tz.utc).astimezone()
    return local.strftime("%H:%M • %d.%m.%Y")

async def _render_stats_embed(user: discord.abc.User, period: str) -> discord.Embed:
    data = api.user_stats(user.id, range=period)

    xp        = int(data.get("xp", 0))
    xp_needed = data.get("xp_needed")
    to_next   = data.get("to_next")

    first_seen  = _parse_iso_z(data.get("first_seen"))
    last_seen   = _parse_iso_z(data.get("last_seen"))
    first_s_str = _fmt_hhmm_ddmmyyyy(first_seen)
    last_s_str  = _fmt_hhmm_ddmmyyyy(last_seen)

    if xp_needed is None or (isinstance(xp_needed, str) and not xp_needed.isdigit()):
        try:
            LEVEL_BASE = 100
            LEVEL_STEP = 25
            xp_needed = LEVEL_BASE + LEVEL_STEP * int(data.get("level", 0))
        except Exception:
            xp_needed = 0

    if to_next is None:
        to_next = max(0, int(xp_needed) - xp) if xp_needed else 0

    days_on_srv = 0
    if first_seen:
        delta = datetime.now(tz.utc) - first_seen.astimezone(tz.utc)
        days_on_srv = max(1, delta.days + 1)

    top_emoji = data.get("top_emoji_given") or []
    top_line = " • ".join(f"{e['emoji_key']}×{e['count']}" for e in top_emoji[:5]) if top_emoji else "—"

    title = f"📈 Stats — {user.display_name} ({period})"
    level = int(data.get("level", 0))
    desc_lines = [
        f"Level **{level}**, XP **{xp} / {xp_needed if xp_needed else '—'}**  •  To next: **{to_next}**",
        "",
        f"Messages: **{data.get('messages_total', 0)}**"
        f" • Stickers: **{data.get('stickers_sent', 0)}**"
        f" • Attachments: **{data.get('attachments_sent', 0)}**",
        f"Words: **{data.get('words_total', 0)}**"
        f" • Chars: **{data.get('chars_total') if data.get('chars_total') is not None else 0}**"
        f" • Channels used: **{data.get('channels_used', 0)}**",
        f"Reactions given: **{data.get('reactions_given', 0)}**"
        f" • received: **{data.get('reactions_received', 0)}**",
        f"Top emoji (given): {top_line}",
        "",
        f"Days on server: **{days_on_srv}**",
    ]

    emb = discord.Embed(title=title, description="\n".join(desc_lines), color=discord.Color.green())
    emb.add_field(name="First seen", value=first_s_str, inline=True)
    emb.add_field(name="Last seen", value=last_s_str, inline=True)
    emb.set_thumbnail(url=user.display_avatar.url)
    return emb

# ===== PUBLIC SETUP =====
async def setup_stats_commands(bot: commands.Bot, tree: app_commands.CommandTree) -> None:
    # prefix !stats / !stat
    @bot.command(name="stats", aliases=["stat"], help="Show your activity stats")
    async def stats_cmd(ctx: commands.Context, period: str | None = None):
        period = (period or "all").lower()
        if period not in {"day", "week", "month", "all"}:
            period = "all"
        try:
            emb = await _render_stats_embed(ctx.author, period)
            await ctx.send(embed=emb)
        except Exception as e:
            print("!stats error:", e)
            await ctx.send("Service unavailable.")

    GUILD_IDS = [int(x) for x in os.getenv("SLASH_GUILDS", "").split(",") if x.strip()]
    guild_objs = [discord.Object(id=g) for g in GUILD_IDS]
    guilds_deco = app_commands.guilds(*guild_objs) if guild_objs else (lambda f: f)

    # slash /stats (ГЛОБАЛЬНАЯ — без app_commands.guilds)
    @app_commands.guilds(*guild_objs)
    @tree.command(name="stats", description="Show your activity stats")
    @app_commands.describe(
        period="day / week / month / all",
        ephemeral="Reply visible only to you"
    )
    async def stats_slash(
        interaction: discord.Interaction,
        period: str = "all",
        ephemeral: bool = True
    ):
        # 1) мгновенно подтверждаем интеракшн (защита от таймаута 3s)
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        except discord.InteractionResponded:
            pass

        # 2) нормализуем период
        period = (period or "all").lower()
        if period not in {"day", "week", "month", "all"}:
            period = "all"

        # 3) строим эмбед и отправляем followup
        try:
            emb = await _render_stats_embed(interaction.user, period)
            await interaction.followup.send(embed=emb, ephemeral=ephemeral)
        except Exception as e:
            print("/stats error:", e)
            try:
                await interaction.followup.send("Service unavailable.", ephemeral=True)
            except Exception:
                pass
            
    