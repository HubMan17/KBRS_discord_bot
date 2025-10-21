import os

from dotenv import load_dotenv

load_dotenv()

def _ids_from_env(key: str) -> set[int]:
    raw = os.getenv(key, "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}

ROLE_IDS_JA    = _ids_from_env("LANG_ROLE_IDS_JA")
ROLE_IDS_DE    = _ids_from_env("LANG_ROLE_IDS_DE")
ROLE_IDS_ZH_TW = _ids_from_env("LANG_ROLE_IDS_ZH_TW")
ROLE_IDS_ZH_CN = _ids_from_env("LANG_ROLE_IDS_ZH_CN")

def _build_role_lang_map() -> dict[int, str]:
    m = {}
    for rid in ROLE_IDS_JA:    m[rid] = "ja"
    for rid in ROLE_IDS_DE:    m[rid] = "de"
    for rid in ROLE_IDS_ZH_TW: m[rid] = "zh-TW"
    for rid in ROLE_IDS_ZH_CN: m[rid] = "zh-CN"
    return m

ROLE_ID_LANG_MAP = _build_role_lang_map()
