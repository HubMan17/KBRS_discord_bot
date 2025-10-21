# moduls/bday_commands.py  — глобальные /set_birthday и /bdays

from __future__ import annotations
import re
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from api_client import api
from messages import (
    birthday_instr_multilang, birthday_invalid_multilang, birthday_saved_multilang
)

DATE_RE = re.compile(r"^(\d{2})[-.](\d{2})(?:[-.](\d{4}))?$")

def _fmt_days_left(n: int) -> str:
    if n == 0: return "today"
    if n == 1: return "in 1 day"
    return f"in {n} days"

def _format_pretty_ddmm(dd: str, mm: str) -> str:
    try:
        return datetime.strptime(f"{dd}-{mm}-2000", "%d-%m-%Y").strftime("%b %d")
    except Exception:
        return f"{dd}-{mm}"

async def _save_birthday_for_user(user: discord.abc.User, date_str: str, tz: str | None):
    api.set_birthday(user.id, date_str.strip(), tz)
    m = DATE_RE.match(date_str.strip())
    dd, mm = m.group(1), m.group(2)
    return _format_pretty_ddmm(dd, mm)

def _filter_kwargs_for_target(target, kwargs: dict):
    if isinstance(target, discord.Interaction):
        return kwargs
    kwargs = dict(kwargs); kwargs.pop("ephemeral", None); return kwargs

async def send_reply(target, *args, **kwargs):
    kwargs = _filter_kwargs_for_target(target, kwargs)
    if isinstance(target, discord.Interaction):
        if not target.response.is_done():
            return await target.response.send_message(*args, **kwargs)
        return await target.followup.send(*args, **kwargs)
    return await target.send(*args, **kwargs)

async def maybe_defer(interaction: discord.Interaction, **kwargs):
    try:
        if isinstance(interaction, discord.Interaction) and not interaction.response.is_done():
            await interaction.response.defer(**kwargs)
    except discord.InteractionResponded:
        pass

# ================== PUBLIC SETUP ==================
async def setup_bday_commands(bot: commands.Bot, tree: app_commands.CommandTree):

    @tree.command(name="set_birthday", description="Set your birthday (privately / DM-friendly).")
    @app_commands.describe(
        date="DD-MM or DD.MM (optionally with -YYYY/.YYYY), e.g. 23-10 or 23.10.2001",
        tz="Timezone like +03:00, -07:00 (optional)",
        ephemeral="Reply visible only to you"
    )
    async def set_birthday_slash(
        interaction: discord.Interaction,
        date: str | None = None,
        tz: str | None = None,
        ephemeral: bool = True
    ):
        await maybe_defer(interaction, thinking=True, ephemeral=ephemeral)

        if interaction.guild is not None and not date:
            dm_ok = False
            try:
                await interaction.user.send(birthday_instr_multilang()); dm_ok = True
            except discord.Forbidden:
                dm_ok = False
            msg = ("📩 I sent you instructions in DM. Use `/set_birthday` there."
                   if dm_ok else "I couldn't DM you. Please enable DMs or provide `date` here.")
            return await send_reply(interaction, msg, ephemeral=True)

        if not date or not DATE_RE.match(date.strip()):
            return await send_reply(interaction, birthday_invalid_multilang(), ephemeral=True)

        try:
            pretty = await _save_birthday_for_user(interaction.user, date, tz)
            await send_reply(interaction, birthday_saved_multilang(pretty, tz or "UTC"), ephemeral=True)
        except Exception:
            await send_reply(interaction, "Couldn't save your birthday. Please try again.", ephemeral=True)

    @tree.command(name="bdays", description="Show next upcoming birthdays on this server")
    @app_commands.describe(
        limit="How many entries to show (default 5, max 25)",
        ephemeral="Reply visible only to you"
    )
    async def bdays_slash(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 5,
        ephemeral: bool = False
    ):
        await maybe_defer(interaction, thinking=True, ephemeral=ephemeral)
        try:
            if interaction.guild is None:
                return await send_reply(interaction, "Use this command inside a server.", ephemeral=True)

            members = [m for m in interaction.guild.members if not m.bot]
            if not members:
                try:
                    members = [m async for m in interaction.guild.fetch_members(limit=None) if not m.bot]
                except Exception:
                    members = []
            ids = [m.id for m in members]
            if not ids:
                return await send_reply(interaction, "No members to check.", ephemeral=ephemeral)

            data = api.birthdays_upcoming(ids, limit=limit)
            items = data.get("items", []) if data else []
            if not items:
                return await send_reply(interaction, "No upcoming birthdays yet.", ephemeral=ephemeral)

            lines = []
            for row in items:
                uid = int(row["discord_id"]); days = int(row["days_left"])
                member = interaction.guild.get_member(uid)
                mention = member.mention if member else f"<@{uid}>"
                try:
                    d = datetime.fromisoformat(row["next_occurrence"]); date_str = d.strftime("%b %d")
                except Exception:
                    date_str = row.get("next_occurrence", "")
                lines.append(f"{mention} — {date_str} ({_fmt_days_left(days)})")

            await send_reply(interaction, "🎂 Upcoming birthdays:\n" + "\n".join(lines), ephemeral=ephemeral)
        except Exception as e:
            print("[/bdays] error:", e)
            await send_reply(interaction, "Service unavailable.", ephemeral=True)

    # ---------- PREFIX: !set_birthday ----------
    @bot.command(
        name="set_birthday",
        help="Set your birthday PRIVATELY via DM: !set_birthday DD-MM [TZ] or !set_birthday DD-MM-YYYY [TZ]"
    )
    async def set_birthday_cmd(ctx: commands.Context, *, args: str | None = None):
        # в гильдии — удаляем сообщение и уводим в ЛС с инструкциями
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except Exception:
                pass
            dm_ok = False
            try:
                await ctx.author.send(birthday_instr_multilang())
                dm_ok = True
            except discord.Forbidden:
                dm_ok = False
            try:
                if dm_ok:
                    await ctx.send("📩 Check your DMs (I sent you instructions).", delete_after=6)
                else:
                    await ctx.send(
                        "I couldn't DM you. Please enable DMs from server members and try again.",
                        delete_after=8
                    )
            except Exception:
                pass
            return

        # здесь уже ЛС
        if not args:
            return await ctx.send(birthday_invalid_multilang())

        parts = args.strip().split()
        date_str = parts[0]
        tz = parts[1] if len(parts) > 1 else None

        if not DATE_RE.match(date_str.strip()):
            return await ctx.send(birthday_invalid_multilang())

        try:
            pretty = await _save_birthday_for_user(ctx.author, date_str, tz)
            await ctx.send(birthday_saved_multilang(pretty, tz or "UTC"))
        except Exception:
            await ctx.send("Couldn't save your birthday. Please try again.")

    # ---------- PREFIX: !bdays ----------
    @bot.command(name="bdays", help="Show next 5 upcoming birthdays on this server")
    async def bdays_prefix(ctx: commands.Context, limit: int = 5):
        if ctx.guild is None:
            return await ctx.send("Use this command inside a server.")

        limit = max(1, min(25, int(limit)))
        # берём участников даже если кеш пуст
        members = [m for m in ctx.guild.members if not m.bot]
        if not members:
            try:
                members = [m async for m in ctx.guild.fetch_members(limit=None) if not m.bot]
            except Exception:
                members = []
        if not members:
            return await ctx.send("No members to check.")

        data = api.birthdays_upcoming([m.id for m in members], limit=limit)
        items = data.get("items", []) if data else []
        if not items:
            return await ctx.send("No upcoming birthdays yet.")

        lines = []
        for row in items:
            uid = int(row["discord_id"])
            days = int(row["days_left"])
            member = ctx.guild.get_member(uid)
            mention = member.mention if member else f"<@{uid}>"
            try:
                d = datetime.fromisoformat(row["next_occurrence"])
                date_str = d.strftime("%b %d")
            except Exception:
                date_str = row.get("next_occurrence", "")
            lines.append(f"{mention} — {date_str} ({_fmt_days_left(days)})")

        await ctx.send("🎂 Upcoming birthdays:\n" + "\n".join(lines))