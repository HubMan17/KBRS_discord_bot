import discord
from discord import app_commands
from discord.ext import commands


async def setup_help_commands(bot: commands.Bot, tree: app_commands.CommandTree):
    """Register shared help commands for prefix and slash styles."""

    # Replace default help so that prefix and slash versions share the same renderer.
    bot.help_command = None
    try:
        bot.remove_command("help")
    except Exception:
        pass

    async def send_reply(target, *args, **kwargs):
        if isinstance(target, discord.Interaction):
            if not target.response.is_done():
                return await target.response.send_message(*args, **kwargs)
            return await target.followup.send(*args, **kwargs)
        kwargs.pop("ephemeral", None)
        return await target.send(*args, **kwargs)

    def build_help_embed(guild: discord.Guild | None) -> discord.Embed:
        prefix_items: list[str] = []
        for cmd in sorted(bot.commands, key=lambda c: c.qualified_name):
            if cmd.hidden or cmd.parent:
                continue
            description = cmd.help or "—"
            prefix_items.append(f"!{cmd.qualified_name} — {description}")

        slash_items: list[str] = []
        for scmd in sorted(tree.get_commands(), key=lambda c: c.qualified_name):
            if isinstance(scmd, app_commands.Group):
                if scmd.callback:
                    description = scmd.description or "—"
                    slash_items.append(f"/{scmd.qualified_name} — {description}")
                for sub in sorted(scmd.commands, key=lambda c: c.qualified_name):
                    description = sub.description or "—"
                    slash_items.append(f"/{sub.qualified_name} — {description}")
            else:
                description = scmd.description or "—"
                slash_items.append(f"/{scmd.qualified_name} — {description}")

        embed = discord.Embed(
            title="KBRS bot commands",
            description="Use either slash or prefix variants (where available).",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Slash commands",
            value="\n".join(slash_items) if slash_items else "—",
            inline=False,
        )
        embed.add_field(
            name="Prefix commands (!)",
            value="\n".join(prefix_items) if prefix_items else "—",
            inline=False,
        )
        if guild:
            embed.set_footer(text=f"Guild: {guild.name}")
        return embed

    @tree.command(name="help", description="Show all available commands")
    @app_commands.describe(ephemeral="Reply only visible to you")
    async def help_slash(
        interaction: discord.Interaction, ephemeral: bool = True
    ) -> None:
        embed = build_help_embed(interaction.guild)
        await send_reply(interaction, embed=embed, ephemeral=ephemeral)

    @bot.command(name="help", help="Show all available commands")
    async def help_prefix(ctx: commands.Context) -> None:
        embed = build_help_embed(ctx.guild)
        await send_reply(ctx, embed=embed)