"""
Configuration management with Pydantic validation.

Features:
- Type-safe configuration
- Automatic validation
- Environment variable loading
- Secrets management
"""

import os
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DiscordConfig(BaseSettings):
    """Discord bot configuration."""

    token: str = Field(..., min_length=50, description="Discord bot token")
    guild_ids: list[int] = Field(default_factory=list, description="Guild IDs for slash commands")

    # Channels
    allowed_command_channels: list[int] = Field(default_factory=list)
    level_up_log_channel_id: int = 0
    welcome_channel_id: int = 0
    announcement_channel_id: int = 0

    # Language channels
    english_chat_id: int = 0
    japanese_chat_id: int = 0
    korean_chat_id: int = 0
    russian_chat_id: int = 0
    taiwan_chat_id: int = 0

    # Behavior
    level_up_public_in_same_channel: bool = False
    sync_slash: bool = False

    model_config = SettingsConfigDict(
        env_prefix="DISCORD_",
        case_sensitive=False,
    )


class TelegramConfig(BaseSettings):
    """Telegram bot configuration."""

    bot_token: str = Field(..., min_length=30, description="Telegram bot token")
    chat_id: int = Field(..., description="Main chat ID")

    # Topics
    thread_id: int = 2
    topic_hw: int = 3
    topic_facts: int = 2

    # Watchers
    watcher_usernames: list[str] = Field(
        default_factory=lambda: ["Twokndos", "Billy_Nagamy"]
    )

    # Behavior
    disable_send: bool = False
    start_polling: bool = True

    model_config = SettingsConfigDict(
        env_prefix="TG_",
        case_sensitive=False,
    )

    @field_validator("watcher_usernames", mode="before")
    @classmethod
    def parse_usernames(cls, v):
        if isinstance(v, str):
            return [u.strip() for u in v.split(",") if u.strip()]
        return v


class APIConfig(BaseSettings):
    """Backend API configuration."""

    base_url: str = Field(..., description="API base URL")
    username: str = Field(..., min_length=3, description="API username")
    password: str = Field(..., min_length=8, description="API password")

    # Timeouts
    timeout: int = Field(default=10, ge=1, le=60, description="Request timeout in seconds")
    token_lifetime: int = Field(default=25 * 60, description="Access token lifetime in seconds")

    # Retry
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay: float = Field(default=1.0, ge=0.1, le=10.0)

    model_config = SettingsConfigDict(
        env_prefix="API_",
        case_sensitive=False,
    )


class TranslationConfig(BaseSettings):
    """Translation service configuration."""

    enabled: bool = Field(default=True)
    url: str = Field(default="", description="Translation API URL")
    key: str = Field(default="", description="API key")
    model: str = Field(default="deepseek-ai/DeepSeek-R1-0528")

    # Retry
    max_attempts: int = Field(default=4, ge=1, le=10)
    base_delay: float = Field(default=0.6, ge=0.1)

    # Timeouts per language
    timeout_en: int = Field(default=30, ge=5, le=120)
    timeout_zh: int = Field(default=40, ge=5, le=120)
    timeout_ja: int = Field(default=30, ge=5, le=120)
    timeout_ru: int = Field(default=30, ge=5, le=120)
    timeout_ko: int = Field(default=30, ge=5, le=120)

    model_config = SettingsConfigDict(
        env_prefix="DEEP_",
        case_sensitive=False,
    )


class RelayConfig(BaseSettings):
    """Discord relay configuration."""

    enabled: bool = Field(default=True)
    send_text_first: bool = Field(default=True)
    mode: Literal["STRICT", "BEST_EFFORT"] = Field(default="BEST_EFFORT")

    # Thread IDs
    ds_thread_id: int = 0
    ds_topic_hw: int = 0

    model_config = SettingsConfigDict(
        env_prefix="RELAY_",
        case_sensitive=False,
    )


class XPConfig(BaseSettings):
    """XP system configuration."""

    # Message XP
    xp_per_message_min: int = Field(default=8, ge=1, le=100)
    xp_per_message_max: int = Field(default=15, ge=1, le=100)
    message_cooldown: int = Field(default=45, ge=0, le=300, description="Cooldown in seconds")

    # Reaction XP
    enable_reaction_xp: bool = Field(default=True)
    xp_per_reaction_min: int = Field(default=3, ge=1, le=50)
    xp_per_reaction_max: int = Field(default=6, ge=1, le=50)
    reaction_cooldown: int = Field(default=45, ge=0, le=300)
    disallow_self_react: bool = Field(default=True)

    # Bonuses
    bonus_sticker: int = Field(default=5, ge=0, le=50)
    bonus_attachment: int = Field(default=8, ge=0, le=50)
    bonus_reply: int = Field(default=5, ge=0, le=50)

    model_config = SettingsConfigDict(
        env_prefix="XP_",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.xp_per_message_min > self.xp_per_message_max:
            raise ValueError("xp_per_message_min must be <= xp_per_message_max")
        if self.xp_per_reaction_min > self.xp_per_reaction_max:
            raise ValueError("xp_per_reaction_min must be <= xp_per_reaction_max")
        return self


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    path: Path = Field(default=Path("bridge.db"), description="SQLite database path")
    wal_mode: bool = Field(default=True, description="Enable WAL mode")

    # Connection pool
    max_connections: int = Field(default=10, ge=1, le=100)
    timeout: float = Field(default=5.0, ge=0.1, le=60.0)

    model_config = SettingsConfigDict(
        env_prefix="DB_",
        case_sensitive=False,
    )


class BufferConfig(BaseSettings):
    """Event buffer configuration."""

    safe_max: int = Field(default=2000, ge=100, le=10000, description="Max events before forced flush")
    flush_period: int = Field(default=300, ge=30, le=3600, description="Flush period in seconds")

    model_config = SettingsConfigDict(
        env_prefix="BUFFER_",
        case_sensitive=False,
    )


class BotConfig(BaseSettings):
    """Main bot configuration."""

    # Environment
    environment: Literal["development", "production"] = Field(default="production")
    debug: bool = Field(default=False)

    # Feature flags
    enable_tg_bridge: bool = Field(default=True)
    enable_discord_relay: bool = Field(default=True)
    enable_translation: bool = Field(default=True)

    # Nested configs
    discord: DiscordConfig
    telegram: TelegramConfig
    api: APIConfig
    translation: TranslationConfig
    relay: RelayConfig
    xp: XPConfig = Field(default_factory=XPConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_to_file: bool = Field(default=True)
    log_to_console: bool = Field(default=True)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_config(self):
        """Cross-field validation."""
        # Validate translation is only enabled if credentials exist
        if self.enable_translation and not (self.translation.url and self.translation.key):
            self.enable_translation = False

        # Validate TG bridge requires valid token
        if self.enable_tg_bridge and not self.telegram.bot_token:
            raise ValueError("Telegram bridge enabled but TG_BOT_TOKEN is missing")

        return self


# Singleton instance
_settings: BotConfig | None = None


def get_settings() -> BotConfig:
    """
    Get or create the singleton settings instance.

    Returns:
        Validated BotConfig instance
    """
    global _settings
    if _settings is None:
        _settings = BotConfig(
            discord=DiscordConfig(),
            telegram=TelegramConfig(),
            api=APIConfig(),
            translation=TranslationConfig(),
            relay=RelayConfig(),
        )
    return _settings


# Convenience export
settings = get_settings()
