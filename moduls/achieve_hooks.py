# bot/achieve_hooks.py
import datetime
import discord
from discord.ext import commands
from api_client import api

def _iso(dt):  # dt ожидаем timezone-aware
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

class AchievementsIngest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        payload = {
            "words": len((message.content or "").split()),
            "chars": len(message.content or ""),
            "has_attach": bool(message.attachments),
            "has_sticker": bool(getattr(message, "stickers", [])),
            "is_reply": bool(message.reference),
            "emoji_count": 0,
            "link_count": 0,
        }
        try:
            resp = api.send_event(
                guild_id=message.guild.id, user_id=message.author.id, username=str(message.author),
                type="message", payload=payload, created_at_iso=_iso(message.created_at),
                dedupe_key=f"msg:{message.id}"
            )
            print("[achievements] INGEST OK", resp)
        except Exception as e:
            import traceback; traceback.print_exc()
            print("[achievements] INGEST FAIL:", repr(e))

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        if user.bot or not reaction.message.guild:
            return
        try:
            resp = api.send_event(
                guild_id=reaction.message.guild.id, user_id=user.id, username=str(user),
                type="reaction",
                payload={
                    "message_id": reaction.message.id,
                    "emoji": str(reaction.emoji),
                    "target_author_id": reaction.message.author.id,
                },
                created_at_iso=_iso(reaction.message.created_at),
                dedupe_key=f"react:{reaction.message.id}:{user.id}:{str(reaction.emoji)}"
            )
            print("[achievements] INGEST OK (reaction)", resp)
        except Exception as e:
            import traceback; traceback.print_exc()
            print("[achievements] INGEST FAIL (reaction):", repr(e))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            resp = api.send_event(
                guild_id=member.guild.id, user_id=member.id, username=str(member),
                type="member_join", payload={}, created_at_iso=_iso(datetime.datetime.utcnow().astimezone(datetime.timezone.utc))
            )
            print("[achievements] ingest join OK:", resp)
        except Exception as e:
            print("[achievements] ingest join FAIL:", e)

def setup_hooks(bot: commands.Bot):
    bot.add_cog(AchievementsIngest(bot))
