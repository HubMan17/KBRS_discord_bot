# rank_commands.py
from __future__ import annotations
from pathlib import Path
import re
import tempfile
import discord
from discord import app_commands
from discord.ext import commands

# Импортируйте свои зависимости как у вас в проекте:
# from your_api_module import api, M, LABELS, resolve_lang_for_member, render_rank_png_inprocess
from api_client import api
from messages import M
from messages import LABELS
from datetime import datetime, timezone as tz
from utils.role_id_land import ROLE_ID_LANG_MAP



PLAY = None          # type: object | None
BROWSER = None       # type: object | None
RANK_TEMPLATE = None # type: object | None

# ---- Утилиты (та же идея, что и в top) ----

MENTION_RE = re.compile(r"<@!?(?P<id>\d+)>")

def _filter_kwargs_for_target(target, kwargs: dict):
    """Удаляем неподдерживаемые аргументы для ctx.send (например, ephemeral)."""
    if isinstance(target, discord.Interaction):
        return kwargs
    kwargs = dict(kwargs)
    kwargs.pop("ephemeral", None)
    return kwargs

async def send_reply(target, *args, **kwargs):
    """
    Универсальная отправка:
      - Interaction: первый ответ -> response.send_message(), далее -> followup.send()
      - ctx / messageable: .send()
    """
    kwargs = _filter_kwargs_for_target(target, kwargs)
    if isinstance(target, discord.Interaction):
        if not target.response.is_done():
            return await target.response.send_message(*args, **kwargs)
        return await target.followup.send(*args, **kwargs)
    return await target.send(*args, **kwargs)

async def maybe_defer(interaction: discord.Interaction, **kwargs):
    """Безопасный defer для слэш-команды."""
    try:
        if isinstance(interaction, discord.Interaction) and not interaction.response.is_done():
            await interaction.response.defer(**kwargs)
    except discord.InteractionResponded:
        pass


def resolve_lang_for_member(member: discord.Member) -> str:
    # если у участника есть несколько «языковых» ролей — возьмём первую найденную
    for r in member.roles:
        code = ROLE_ID_LANG_MAP.get(r.id)
        if code:
            return code
    return "en"

# ---- Общая логика рендера ----

async def _resolve_member_from_text(guild: discord.Guild | None, text: str | None) -> discord.User | None:
    if not text:
        return None
    m = MENTION_RE.search(text)
    if m and guild:
        uid = int(m.group("id"))
        return guild.get_member(uid) or await guild.fetch_member(uid)
    # если просто число — трактуем как ID
    if text.isdigit():
        uid = int(text)
        if guild:
            try:
                return guild.get_member(uid) or await guild.fetch_member(uid)
            except Exception:
                pass
        # на крайний случай попробуем глобально (может вернуть discord.User)
        try:
            return await guild._state.client.fetch_user(uid) if guild else None
        except Exception:
            return None
    return None

async def _make_payload_for_user(user: discord.abc.User, data: dict, author_for_lang: discord.abc.User):
    lang = resolve_lang_for_member(author_for_lang)
    labels = LABELS.get(lang, LABELS["en"])

    # Инициалы из display_name (если это Member) или name (если User)
    display_name = user.display_name if isinstance(user, discord.Member) else user.name
    initials = "".join([w[0] for w in display_name.split()[:2]]).upper() or "U"

    avatar_url = user.display_avatar.url if getattr(user, "display_avatar", None) else None

    payload = {
        "username": display_name,
        "initials": initials,
        "avatar": avatar_url,
        "level": data.get("level", 0),
        "value": data.get("xp", 0),
        "max": data.get("xp_needed", 0),
        "rank": data.get("position", 1),
        "top": data.get("position", 1),
        "label_lvl":  labels["lvl"],
        "label_rank": labels["rank"],
        "label_top":  labels["top"],
        "label_next": labels["next"],
    }
    return payload


async def _build_fallback_embed(target_user: discord.abc.User, data: dict | None):
    name = target_user.display_name if isinstance(target_user, discord.Member) else target_user.name
    level = (data or {}).get("level", 0)
    xp    = (data or {}).get("xp", 0)
    need  = (data or {}).get("xp_needed", 0)

    emb = discord.Embed(
        title=M["rank_title"].format(name=name),
        description=M["rank_desc"].format(level=level, xp=xp, need=need),
        color=discord.Color.blurple(),
    )
    avatar_url = target_user.display_avatar.url if getattr(target_user, "display_avatar", None) else None
    if avatar_url:
        emb.set_thumbnail(url=avatar_url)
    return emb

def compute_progress_pct(value: int, maxv: int) -> float:
    maxv = max(1, int(maxv))
    return round(min(100.0, max(0.0, (value / maxv) * 100.0)), 4)

async def render_rank_png_inprocess(ctx_obj: dict) -> Path:
    """Рендерит HTML→PNG внутри процесса бота через уже запущенный Chromium."""
    if RANK_TEMPLATE is None:
        raise RuntimeError("Rank template not loaded")
    if BROWSER is None:
        raise RuntimeError("Chromium is not running")

    # подготовка контекста для шаблона
    ctx = {
        "username": ctx_obj.get("username", "User"),
        "initials": ctx_obj.get("initials", "U"),
        "avatar": ctx_obj.get("avatar"),  # можно URL или data:
        "level": int(ctx_obj.get("level", 0)),
        "value": int(ctx_obj.get("value", 0)),
        "max": int(ctx_obj.get("max", 100)),
        "rank": int(ctx_obj.get("rank", 1)),
        "top": int(ctx_obj.get("top", 1)),
        
        "label_lvl":  ctx_obj.get("label_lvl", LABELS["en"]["lvl"]),
        "label_rank": ctx_obj.get("label_rank", LABELS["en"]["rank"]),
        "label_top":  ctx_obj.get("label_top", LABELS["en"]["top"]),
        "label_next": ctx_obj.get("label_next", LABELS["en"]["next"]),
    }
    ctx["progress_pct"] = compute_progress_pct(ctx["value"], ctx["max"])

    html = RANK_TEMPLATE.render(**ctx)

    page = await BROWSER.new_page(viewport={"width": 500, "height": 188, "deviceScaleFactor": 2})
    # немного отключим анимации/трансишны, чтобы картинка была стабильнее
    await page.add_style_tag(content="""
      * { animation: none !important; transition: none !important; }
    """)
    await page.set_content(html, wait_until="load")
    # tailwind с CDN может прогружаться доли секунды — дадим малую паузу
    await page.wait_for_timeout(120)

    tmpdir = Path(tempfile.mkdtemp(prefix="rankcard_"))
    out_png = tmpdir / "card.png"
    await page.locator("body").screenshot(path=str(out_png), omit_background=True)
    await page.close()
    return out_png

# ---- Публичная точка подключения ----

async def setup_rank_commands(
    bot: commands.Bot,
    tree: app_commands.CommandTree,
    play,
    browser,
    rank_template,
):
    # сохранить в модульные глобальные
    global PLAY, BROWSER, RANK_TEMPLATE
    PLAY = play
    BROWSER = browser
    RANK_TEMPLATE = rank_template
    """Регистрирует /rank и !rank."""

    # ----- SLASH: /rank -----
    @tree.command(name="rank", description="Show your (or member's) current level and XP")
    @app_commands.describe(
        member="Member to check (defaults to you)",
        who="Or paste @mention / numeric ID (fallback)",
        ephemeral="Reply visible only to you (slash only)"
    )
    async def rank_slash(
        interaction: discord.Interaction,
        member: discord.Member | discord.User | None = None,
        who: str | None = None,
        ephemeral: bool = False,
    ):
        await maybe_defer(interaction, thinking=True, ephemeral=ephemeral)

        try:
            target = member
            if target is None and who:
                target = await _resolve_member_from_text(interaction.guild, who)
            if target is None:
                target = interaction.user

            data = api.rank(target.id)
            if not data:
                return await send_reply(interaction, "No XP data yet.", ephemeral=ephemeral)

            payload = await _make_payload_for_user(target, data, author_for_lang=interaction.user)
            png_path = await render_rank_png_inprocess(payload)

            await send_reply(
                interaction,
                file=discord.File(str(png_path), filename="rank.png"),
                ephemeral=ephemeral,
            )

        except Exception as e:
            print("[/rank] render error:", e)
            try:
                fallback = await _build_fallback_embed(target, data if "data" in locals() else None)
                await send_reply(interaction, embed=fallback, ephemeral=ephemeral)
            except Exception:
                await send_reply(interaction, "Service unavailable.", ephemeral=True)

    # ----- PREFIX: !rank -----
    @bot.command(name="rank", help="Show your current level and XP")
    async def rank_prefix(ctx: commands.Context, member: discord.Member | None = None):
        target = member or ctx.author
        try:
            # 1) данные
            data = api.rank(target.id)
            if not data:
                return await send_reply(ctx, "No XP data yet.")

            # 2) рендер PNG
            payload = await _make_payload_for_user(target, data, author_for_lang=ctx.author)
            png_path = await render_rank_png_inprocess(payload)

            # 3) отправка файла
            await send_reply(ctx, file=discord.File(str(png_path), filename="rank.png"))

        except Exception as e:
            print("[!rank] render error:", e)
            try:
                fallback = await _build_fallback_embed(target, data if "data" in locals() else None)
                await send_reply(ctx, embed=fallback)
            except Exception:
                await send_reply(ctx, "Service unavailable.")
