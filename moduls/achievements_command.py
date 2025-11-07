"""
Discord slash commands for achievements system.

Commands:
- /achievements [user] - View achievements for yourself or another user
- /achievements_stats [user] - View detailed achievement statistics
"""

from __future__ import annotations
import re
import discord
from discord import app_commands
from discord.ext import commands

from api_client import api
from messages import LABELS
from utils.role_id_land import ROLE_ID_LANG_MAP


# Utility functions
MENTION_RE = re.compile(r"<@!?(?P<id>\d+)>")


def resolve_lang_for_member(member: discord.Member) -> str:
    """Get language code from user's roles."""
    for r in member.roles:
        code = ROLE_ID_LANG_MAP.get(r.id)
        if code:
            return code
    return "en"


async def _resolve_user(interaction: discord.Interaction, user_input: str | None) -> tuple[discord.User | None, int | None]:
    """
    Resolve user from mention, ID, or current user.

    Returns:
        (discord.User, discord_id) or (None, None) if not found
    """
    # If no input, use command author
    if not user_input:
        return interaction.user, interaction.user.id

    # Check for mention
    m = MENTION_RE.search(user_input)
    if m:
        user_id = int(m.group("id"))
        try:
            user = await interaction.client.fetch_user(user_id)
            return user, user_id
        except Exception:
            return None, user_id

    # Check if it's a raw ID
    if user_input.isdigit():
        user_id = int(user_input)
        try:
            user = await interaction.client.fetch_user(user_id)
            return user, user_id
        except Exception:
            return None, user_id

    return None, None


def create_achievement_embed(achievement_data: dict, lang: str = "en") -> discord.Embed:
    """Create embed for a single achievement."""
    labels = LABELS.get(lang, LABELS["en"])

    achievement = achievement_data.get("achievement", {})
    unlocked = achievement_data.get("unlocked", False)
    progress = achievement_data.get("progress", 0)
    progress_pct = achievement_data.get("progress_percentage", 0)

    # Color based on rarity
    color_map = {
        "common": 0x808080,      # Gray
        "uncommon": 0x00ff00,    # Green
        "rare": 0x0080ff,        # Blue
        "epic": 0x8000ff,        # Purple
        "legendary": 0xffa500,   # Orange
        "mythic": 0xff00ff,      # Magenta
    }
    color = color_map.get(achievement.get("rarity", "common"), 0x808080)

    icon = achievement.get("icon", "🏆")
    name = achievement.get("name", "Unknown")
    description = achievement.get("description", "")
    xp_reward = achievement.get("xp_reward", 0)
    rarity = achievement.get("rarity_display", "Common")
    condition_value = achievement.get("condition_value", 0)

    # Create embed
    if unlocked:
        title = f"{icon} {name} ✅"
        embed_description = f"{description}\n\n**Unlocked!** +{xp_reward} XP"
        embed_color = color
    else:
        title = f"{icon} {name}"
        embed_description = f"{description}\n\nProgress: {progress}/{condition_value} ({progress_pct}%)"
        embed_color = 0x808080  # Gray for locked

    embed = discord.Embed(
        title=title,
        description=embed_description,
        color=embed_color
    )

    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="XP Reward", value=f"{xp_reward} XP", inline=True)

    if not unlocked:
        embed.add_field(name="Progress", value=f"{progress}/{condition_value}", inline=True)

    return embed


def create_achievements_summary_embed(data: dict, user: discord.User, lang: str = "en") -> discord.Embed:
    """Create summary embed for user's achievements."""
    labels = LABELS.get(lang, LABELS["en"])

    username = data.get("username", user.name)
    total = data.get("total_achievements", 0)
    unlocked_count = data.get("unlocked_count", 0)
    completion = data.get("completion_percentage", 0)
    total_xp = data.get("total_xp_earned", 0)

    embed = discord.Embed(
        title=f"🏆 {username}'s Achievements",
        description=f"**Progress:** {unlocked_count}/{total} ({completion:.1f}%)\n"
                   f"**Total XP from Achievements:** {total_xp}",
        color=0xffd700  # Gold
    )

    embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)

    # Group achievements by status
    achievements = data.get("achievements", [])
    unlocked = [a for a in achievements if a.get("unlocked")]
    locked = [a for a in achievements if not a.get("unlocked")]

    # Show recent unlocks (up to 5)
    if unlocked:
        recent = unlocked[:5]
        recent_text = "\n".join([
            f"{a['achievement']['icon']} **{a['achievement']['name']}**"
            for a in recent
        ])
        embed.add_field(
            name=f"🎉 Recently Unlocked ({len(unlocked)} total)",
            value=recent_text or "None",
            inline=False
        )

    # Show in-progress achievements (closest to completion)
    if locked:
        in_progress = sorted(
            [a for a in locked if a.get("progress", 0) > 0],
            key=lambda x: x.get("progress_percentage", 0),
            reverse=True
        )[:5]

        if in_progress:
            progress_text = "\n".join([
                f"{a['achievement']['icon']} **{a['achievement']['name']}** - "
                f"{a.get('progress', 0)}/{a['achievement']['condition_value']} "
                f"({a.get('progress_percentage', 0)}%)"
                for a in in_progress
            ])
            embed.add_field(
                name=f"📈 In Progress ({len([a for a in locked if a.get('progress', 0) > 0])} total)",
                value=progress_text,
                inline=False
            )

    embed.set_footer(text=f"Use /achievements_stats for detailed statistics")

    return embed


def create_stats_embed(data: dict, user: discord.User, lang: str = "en") -> discord.Embed:
    """Create detailed statistics embed."""
    labels = LABELS.get(lang, LABELS["en"])

    total = data.get("total_achievements", 0)
    unlocked_count = data.get("unlocked_count", 0)
    completion = data.get("completion_percentage", 0)
    total_xp = data.get("total_xp_earned", 0)

    embed = discord.Embed(
        title=f"📊 {user.name}'s Achievement Statistics",
        description=f"**Overall Progress:** {unlocked_count}/{total} ({completion:.1f}%)\n"
                   f"**Total XP Earned:** {total_xp}",
        color=0x00ff00
    )

    embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)

    # By rarity
    by_rarity = data.get("by_rarity", {})
    if by_rarity:
        rarity_text = "\n".join([
            f"**{info['name']}:** {info['unlocked']}/{info['total']} ({info['percentage']:.0f}%)"
            for rarity, info in by_rarity.items()
        ])
        embed.add_field(name="🎨 By Rarity", value=rarity_text, inline=False)

    # By category
    by_category = data.get("by_category", {})
    if by_category:
        category_text = "\n".join([
            f"**{info['name']}:** {info['unlocked']}/{info['total']} ({info['percentage']:.0f}%)"
            for cat, info in by_category.items()
        ])
        embed.add_field(name="📁 By Category", value=category_text, inline=False)

    # Recent unlocks
    recent = data.get("recent_unlocks", [])
    if recent:
        recent_text = "\n".join([
            f"{a['achievement']['icon']} **{a['achievement']['name']}** - "
            f"<t:{int(a['unlocked_at'].timestamp()) if hasattr(a['unlocked_at'], 'timestamp') else 0}:R>"
            for a in recent[:5]
        ])
        embed.add_field(name="🕐 Recent Unlocks", value=recent_text, inline=False)

    return embed


# Command setup
async def setup_achievements_commands(bot: commands.Bot, tree: app_commands.CommandTree):
    """Register achievement commands with the bot."""

    @tree.command(name="achievements", description="View your achievements or another user's achievements")
    @app_commands.describe(user="User to view achievements for (mention or ID, leave empty for yourself)")
    async def cmd_achievements(interaction: discord.Interaction, user: str = None):
        """View achievements for a user."""
        await interaction.response.defer(ephemeral=False)

        try:
            # Resolve user
            discord_user, discord_id = await _resolve_user(interaction, user)

            if discord_id is None:
                await interaction.followup.send("❌ Could not find that user.", ephemeral=True)
                return

            # Get user's achievements
            data = api.get_user_achievements(discord_id, unlocked_only=False)

            if not data:
                await interaction.followup.send("❌ Could not fetch achievements data.", ephemeral=True)
                return

            # Determine language
            lang = resolve_lang_for_member(interaction.user) if isinstance(interaction.user, discord.Member) else "en"

            # Create embed
            embed = create_achievements_summary_embed(data, discord_user or interaction.user, lang)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"[achievements] cmd_achievements error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while fetching achievements. Please try again later.",
                ephemeral=True
            )

    @tree.command(name="achievements_stats", description="View detailed achievement statistics")
    @app_commands.describe(user="User to view stats for (mention or ID, leave empty for yourself)")
    async def cmd_achievements_stats(interaction: discord.Interaction, user: str = None):
        """View detailed achievement statistics."""
        await interaction.response.defer(ephemeral=False)

        try:
            # Resolve user
            discord_user, discord_id = await _resolve_user(interaction, user)

            if discord_id is None:
                await interaction.followup.send("❌ Could not find that user.", ephemeral=True)
                return

            # Get user's stats
            data = api.get_achievement_stats(discord_id)

            if not data:
                await interaction.followup.send("❌ Could not fetch achievement statistics.", ephemeral=True)
                return

            # Determine language
            lang = resolve_lang_for_member(interaction.user) if isinstance(interaction.user, discord.Member) else "en"

            # Create embed
            embed = create_stats_embed(data, discord_user or interaction.user, lang)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"[achievements] cmd_achievements_stats error: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                "❌ An error occurred while fetching achievement statistics. Please try again later.",
                ephemeral=True
            )
