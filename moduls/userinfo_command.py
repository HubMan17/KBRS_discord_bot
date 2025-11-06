from __future__ import annotations

from datetime import datetime, timezone as tz

import discord
from discord import app_commands
from discord.ext import commands


def _fmt_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    local = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=tz.utc).astimezone()
    return local.strftime("%H:%M • %d.%m.%Y")


def _status_label(member: discord.Member | None) -> str:
    if member is None:
        return "Unknown"

    status = getattr(member, "status", None)
    if status is None:
        return "Unknown"

    mapping: dict[discord.Status, str] = {
        discord.Status.online: "🟢 Online",
        discord.Status.idle: "🌙 Idle",
        discord.Status.do_not_disturb: "⛔ Do Not Disturb",
        discord.Status.dnd: "⛔ Do Not Disturb",
        discord.Status.offline: "⚫ Offline",
        discord.Status.invisible: "⚫ Invisible",
    }
    return mapping.get(status, status.name.replace("_", " ").title())


def _build_userinfo_embed(
    user: discord.abc.User,
    member: discord.Member | None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"ℹ️ Profile — {user.display_name}",
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    embed.add_field(name="Account created", value=_fmt_datetime(user.created_at), inline=True)

    joined_value = _fmt_datetime(getattr(member, "joined_at", None)) if member else "—"
    embed.add_field(name="Joined server", value=joined_value, inline=True)

    embed.add_field(name="Status", value=_status_label(member), inline=True)

    if member and member.guild:
        roles = [role.mention for role in member.roles if role != member.guild.default_role]
        if roles:
            roles_value = ", ".join(roles)
            if len(roles_value) > 1024:
                visible = roles[:10]
                roles_value = ", ".join(visible)
                remaining = len(roles) - len(visible)
                if remaining > 0:
                    roles_value += f" (+{remaining} more)"
        else:
            roles_value = "—"
        embed.add_field(name="Roles", value=roles_value, inline=False)
    else:
        embed.add_field(name="Roles", value="—", inline=False)

    return embed


async def setup_userinfo_commands(bot: commands.Bot, tree: app_commands.CommandTree) -> None:
    async def resolve_member(
        guild: discord.Guild | None,
        user: discord.abc.User,
    ) -> discord.Member | None:
        if guild is None:
            return None
        if isinstance(user, discord.Member) and user.guild == guild:
            return user
        # Используем только локальный кэш гильдии, чтобы избежать сетевых запросов
        # (например, при офлайн-запуске бота без доступа к Discord API).
        return guild.get_member(user.id)

    @bot.command(name="userinfo", aliases=["user"], help="Show account information")
    async def userinfo_prefix(
        ctx: commands.Context,
        member: discord.Member | discord.User | None = None,
    ) -> None:
        if ctx.guild is None:
            await ctx.send("This command is available only within a server.")
            return

        target = member or ctx.author
        guild_member = await resolve_member(ctx.guild, target)
        embed = _build_userinfo_embed(target, guild_member)
        await ctx.send(embed=embed)

    @tree.command(name="userinfo", description="Show account information about a member")
    @app_commands.describe(
        member="Server member to inspect",
        ephemeral="Reply visible only to you",
    )
    async def userinfo_slash(
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        ephemeral: bool = True,
    ) -> None:
        if interaction.guild is None:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "This command is available only within a server.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "This command is available only within a server.",
                    ephemeral=True,
                )
            return

        target: discord.Member | discord.User = member or interaction.user
        guild_member = await resolve_member(interaction.guild, target)
        embed = _build_userinfo_embed(target, guild_member)

        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)