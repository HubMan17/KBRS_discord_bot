# activities_prefix.py
from __future__ import annotations

import os, json
from typing import Dict, Optional
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()


def _log(msg: str) -> None:
    print(f"[activities] {msg}")


def _default_cfg_path() -> Path:
    return Path(__file__).parent / "activities.json"


CFG_PATH = Path(os.getenv("ACTIVITIES_FILE") or _default_cfg_path())


class ActivityManager:
    """
    key -> {app_id, voice_id, cmd_id}
    """
    def __init__(self, path: Path = CFG_PATH):
        self.path = Path(path)
        self.cfg: Dict[str, Dict[str, int]] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                self.cfg = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self.cfg = {}
        except Exception as e:
            _log(f"load error: {e!r}")
            self.cfg = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            _log(f"save error: {e!r}")

    def ensure(self, key: str) -> dict:
        self.cfg.setdefault(key, {"app_id": 0, "voice_id": 0, "cmd_id": 0})
        return self.cfg[key]

    def get(self, key: str) -> Optional[dict]:
        c = self.cfg.get(key)
        if not c:
            return None
        if not c.get("app_id") or not c.get("voice_id") or not c.get("cmd_id"):
            return None
        return c

    def in_cmd_channel(self, key: str, channel_id: int) -> bool:
        c = self.cfg.get(key) or {}
        return channel_id == c.get("cmd_id")


class ActivityOpenView(discord.ui.View):
    """Кнопка-ссылка 'Open <activity>' на инвайт активности."""
    def __init__(self, invite_url: str, label: str):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Button(label=label, url=invite_url, style=discord.ButtonStyle.link))


def setup_activity_commands(bot: commands.Bot) -> None:
    """
    Регистрирует ПРЕФИКС-команды:
      !activity_set_app <key> <app_id>
      !activity_set_channels <key> <voice_channel> <text_channel>
      !activity_start <key>
      !ping
    ВЫЗВАТЬ ОДИН РАЗ: setup_activity_prefix_commands(bot) до bot.run().
    """
    mgr = ActivityManager()
    _log(f"config path: {mgr.path}")

    @bot.command(name="ping", help="Health check (activities)")
    async def ping_cmd(ctx: commands.Context):
        await ctx.reply("pong", mention_author=False)

    @bot.command(name="activity_set_app", help="Bind application id to an activity key (admin only)")
    @commands.has_permissions(administrator=True)
    async def activity_set_app_cmd(ctx: commands.Context, activity: str, app_id: int):
        row = mgr.ensure(activity)
        row["app_id"] = int(app_id)
        mgr.save()
        await ctx.reply(f"Set **{activity}** app_id = `{app_id}`", mention_author=False)

    @bot.command(
        name="activity_set_channels",
        help="Bind voice + command channels for an activity (admin only)"
    )
    @commands.has_permissions(administrator=True)
    async def activity_set_channels_cmd(
        ctx: commands.Context,
        activity: str,
        voice: discord.VoiceChannel,
        cmd: discord.TextChannel,
    ):
        row = mgr.ensure(activity)
        row["voice_id"] = voice.id
        row["cmd_id"] = cmd.id
        mgr.save()
        await ctx.reply(
            f"Bound **{activity}** → voice: {voice.mention}, commands: {cmd.mention}",
            mention_author=False,
        )

    @bot.command(name="activity_start", help="Start configured activity and post an Open button")
    async def activity_start_cmd(ctx: commands.Context, activity: str):
        cfg = mgr.get(activity)
        if not cfg:
            return await ctx.reply("Unknown or not configured activity.", mention_author=False)

        # Ограничиваем запуск только в привязанном командном канале
        if not mgr.in_cmd_channel(activity, ctx.channel.id):
            return await ctx.reply("This command is disabled in this channel.", mention_author=False)

        guild = ctx.guild
        if guild is None:
            return await ctx.reply("Guild-only command.", mention_author=False)

        voice_ch = guild.get_channel(cfg["voice_id"])
        if not isinstance(voice_ch, discord.VoiceChannel):
            return await ctx.reply("Configured voice channel not found.", mention_author=False)

        try:
            invite = await voice_ch.create_invite(
                max_age=0,
                max_uses=0,
                temporary=False,
                target_type=discord.InviteTarget.embedded_application,
                target_application_id=cfg["app_id"],
            )
            invite_url = f"https://discord.com/invite/{invite.code}"
            view = ActivityOpenView(invite_url, label=f"Open {activity}")
            await ctx.reply(
                content=(
                    f"🎮 **{activity}** is ready in {voice_ch.mention}.\n"
                    f"Click the button to join & open the activity."
                ),
                view=view,
                mention_author=False,
            )
        except discord.Forbidden:
            await ctx.reply("I don't have permission to create invites in that voice channel.", mention_author=False)
        except Exception as e:
            await ctx.reply(f"Failed to start activity: {e}", mention_author=False)
