import discord
from discord import app_commands
from discord.ext import commands

# предполагаем, что api и M импортированы тут или передаются сверху
from api_client import api
from messages import M


async def setup_top_commands(bot: commands.Bot, tree: app_commands.CommandTree):
    """регистрирует команды /top и !top"""

    # ---------- Утилиты ----------
    async def send_reply(target, *args, **kwargs):
        if isinstance(target, discord.Interaction):
            if not target.response.is_done():
                return await target.response.send_message(*args, **kwargs)
            return await target.followup.send(*args, **kwargs)
        kwargs.pop("ephemeral", None)
        return await target.send(*args, **kwargs)

    async def maybe_defer(interaction: discord.Interaction, **kwargs):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(**kwargs)
        except discord.InteractionResponded:
            pass

    async def build_top_embed(guild, limit):
        items = api.top(limit=limit)
        if not items:
            return None
        lines = []
        for i, row in enumerate(items, 1):
            did = int(row["discord_id"])
            member = guild.get_member(did) if guild else None
            user = member or await bot.fetch_user(did)
            name = user.display_name if isinstance(user, discord.Member) else user.name
            lines.append(f"{i}. **{name}** — lvl {row['level']} ({row['xp']} XP)")
        emb = discord.Embed(
            title=M["top_title"],
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        return emb

    # ---------- Slash ----------
    @tree.command(name="top", description="Show top members by level/XP")
    @app_commands.describe(limit="How many to show", ephemeral="Reply visible only to you")
    async def top_slash(interaction: discord.Interaction, limit: int = 10, ephemeral: bool = False):
        await maybe_defer(interaction, thinking=True, ephemeral=ephemeral)
        emb = await build_top_embed(interaction.guild, limit)
        if emb is None:
            return await send_reply(interaction, M["top_empty"], ephemeral=ephemeral)
        await send_reply(interaction, embed=emb, ephemeral=ephemeral)

    # ---------- Prefix ----------
    @bot.command(name="top", help="Show top members by level/XP")
    async def top_prefix(ctx: commands.Context, limit: int = 10):
        emb = await build_top_embed(ctx.guild, limit)
        if emb is None:
            return await send_reply(ctx, M["top_empty"])
        await send_reply(ctx, embed=emb)
