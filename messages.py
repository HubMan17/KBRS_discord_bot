import os
import re

from dotenv import load_dotenv

load_dotenv()

WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))

M = {
    "level_up_public": "🎉 {mention} reached **level {level}**!",
    "level_up_log":    "🚀 {mention} leveled up to **{level}** (XP: {xp}/{need})",
    "rank_title":      "📊 Rank — {name}",
    "rank_desc":       "Level: **{level}**\nXP: **{xp} / {need}**",
    "top_empty":       "No XP data yet.",
    "top_title":       "🏆 Top 10",
    "sync_done":       "✅ Synced. Added **{n}** members to the XP system.",
}


# ── статичные многоязычные шаблоны level-up ───────────────────────────────
LEVELUP_MSG_EN = "reached level {level}!"
LEVELUP_MSG_JA = "レベル{level}に到達しました！"
LEVELUP_MSG_KO = "레벨 {level}에 도달했어요!"
LEVELUP_MSG_ZH = "达到了 {level} 级！"

def format_multilang_levelup(mention: str, level: int) -> str:
    """Формирует многоязычное сообщение с флажками."""
    return (
        f"🎉 {mention} {LEVELUP_MSG_EN.format(level=level)}\n\n"
        f"🇯🇵 **JA:** {LEVELUP_MSG_JA.format(level=level)}\n"
        f"🇰🇷 **KO:** {LEVELUP_MSG_KO.format(level=level)}\n"
        f"🇨🇳 **ZH-CN:** {LEVELUP_MSG_ZH.format(level=level)}"
    )
    

# ── статичные многоязычные шаблоны welcome ───────────────────────────────
WELCOME_EN = "welcome to the server! We're glad to have you with us — feel free to say hi in any channel. 🙂"
WELCOME_JA = "サーバーへようこそ！来てくれて嬉しいです。気軽にどのチャンネルでも挨拶してね。🙂"
WELCOME_KO = "서버에 오신 것을 환영해요! 함께하게 되어 기뻐요—아무 채널에서나 인사해 주세요. 🙂"
WELCOME_ZH = "欢迎加入服务器！很高兴你来到这里——随时在任意频道打个招呼吧。🙂"

def format_multilang_welcome(mention: str) -> str:
    return (
        f"👋 {mention} {WELCOME_EN}\n\n"
        f"🇯🇵 **JA:** {WELCOME_JA}\n"
        f"🇰🇷 **KO:** {WELCOME_KO}\n"
        f"🇨🇳 **ZH-CN:** {WELCOME_ZH}"
    )
    
# кастомизация под языки для карточки rank
ROLE_LANG_MAP = {
    "Japan":  "ja",
    "German": "de",
    "Taiwan": "zh-CN",   # (если хочешь упрощённый китайский — поставь "zh-CN")
}

LABELS = {
    "en":    {"lvl": "Lvl",   "rank": "Rank",  "top": "Top",    "next": "Next"},
    "ja":    {"lvl": "レベル", "rank": "順位",   "top": "上位",     "next": "次"},
    "de":    {"lvl": "Stufe", "rank": "Rang",  "top": "Top",    "next": "Nächstes"},
    "zh-TW": {"lvl": "等級",   "rank": "排名",   "top": "Top",    "next": "下一個目標"},
    "zh-CN": {"lvl": "等级",   "rank": "排名",   "top": "Top",    "next": "下一个目标"},
}

# birthdays
# DD-MM or DD-MM-YYYY or with dots
DATE_RE = re.compile(
    r"^\s*(\d{1,2})[-.](\d{1,2})(?:[-.](\d{4}))?\s*$"
)

def birthday_instr_multilang() -> str:
    return (
        "For privacy, please set your birthday **in DM** with me.\n"
        "Use:\n"
        "`!set_birthday DD-MM [TZ]`\n"
        "or\n"
        "`!set_birthday DD-MM-YYYY [TZ]`\n"
        "(Dots are supported too, e.g. `07.05.2003 Asia/Tokyo`).\n\n"
        "🇯🇵 **日本語**:\n"
        "誕生日は**ダイレクトメッセージ**で設定してください。\n"
        "`!set_birthday DD-MM [TZ]` または `!set_birthday DD-MM-YYYY [TZ]`\n"
        "（例: `07.05.2003 Asia/Tokyo`）\n\n"
        "🇰🇷 **한국어**:\n"
        "개인정보 보호를 위해 **DM**에서 생일을 설정해주세요.\n"
        "`!set_birthday DD-MM [TZ]` 또는 `!set_birthday DD-MM-YYYY [TZ]`\n"
        "(`07.05.2003 Asia/Seoul` 같은 형식 지원)\n\n"
        "🇨🇳 **简体中文**:\n"
        "为保护隐私，请在**私信**中设置生日。\n"
        "使用：`!set_birthday DD-MM [TZ]` 或 `!set_birthday DD-MM-YYYY [TZ]`\n"
        "（支持点号：`07.05.2003 Asia/Shanghai`）"
    )

def birthday_invalid_multilang() -> str:
    return (
        "Invalid format. Use `!set_birthday DD-MM [TZ]` or `!set_birthday DD-MM-YYYY [TZ]` "
        "(dots are supported: `DD.MM[.YYYY]`).\n\n"
        "🇯🇵 正しい形式で入力してください：`DD-MM` または `DD-MM-YYYY`（ドットも可）。\n"
        "🇰🇷 형식이 올바르지 않습니다: `DD-MM` 또는 `DD-MM-YYYY` (점표기 가능).\n"
        "🇨🇳 格式不正确：`DD-MM` 或 `DD-MM-YYYY`（支持点号）。"
    )

def birthday_saved_multilang(pretty: str, tz_used: str) -> str:
    return (
        f"✅ Birthday saved as **{pretty}** (TZ: **{tz_used}**). I’ll celebrate it here!\n\n"
        f"🇯🇵 保存しました：**{pretty}**（タイムゾーン：**{tz_used}**）。\n"
        f"🇰🇷 저장되었습니다: **{pretty}** (시간대: **{tz_used}**).\n"
        f"🇨🇳 已保存：**{pretty}**（时区：**{tz_used}**）。"
    )