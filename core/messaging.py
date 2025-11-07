"""
Unified messaging abstraction for Discord and Telegram.

Features:
- Unified interface for both platforms
- Automatic message chunking
- Retry logic
- Attachment handling
- Type-safe message builders
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Any
from pathlib import Path

import discord
import telebot
from telebot import apihelper

from core.logger import get_logger
from core.exceptions import KBRSException

logger = get_logger(__name__)


@dataclass
class MessageContent:
    """Unified message content structure."""

    text: str | None = None
    embeds: list[Any] | None = None
    files: list[Path | str] | None = None
    reply_to: int | None = None
    mentions: list[int | str] | None = None


@dataclass
class MessageOptions:
    """Message sending options."""

    ephemeral: bool = False
    delete_after: float | None = None
    thread_id: int | None = None
    retry_count: int = 3
    timeout: float = 30.0


class MessagingBackend(ABC):
    """Abstract base for messaging backends."""

    @abstractmethod
    async def send(
        self,
        channel_or_chat_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> Any:
        """Send message to channel/chat."""
        pass

    @abstractmethod
    async def edit(
        self,
        message_id: int,
        channel_or_chat_id: int,
        new_content: MessageContent,
    ) -> Any:
        """Edit existing message."""
        pass

    @abstractmethod
    async def delete(self, message_id: int, channel_or_chat_id: int) -> None:
        """Delete message."""
        pass


class DiscordMessaging(MessagingBackend):
    """Discord messaging implementation."""

    def __init__(self, bot: discord.Client):
        self.bot = bot

    def _chunk_text(self, text: str, limit: int = 2000) -> list[str]:
        """Split text into Discord-compatible chunks."""
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        chunks = []
        current = []
        current_len = 0

        for line in text.splitlines(True):
            if current_len + len(line) > limit:
                chunks.append("".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line)

        if current:
            chunks.append("".join(current))

        return chunks

    async def send(
        self,
        channel_or_chat_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> discord.Message | list[discord.Message]:
        """Send message to Discord channel."""
        options = options or MessageOptions()

        # Get channel
        channel = self.bot.get_channel(channel_or_chat_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_or_chat_id)
            except Exception as e:
                raise KBRSException(f"Channel {channel_or_chat_id} not found: {e}")

        # Handle thread override
        if options.thread_id and isinstance(channel, discord.Thread):
            channel = channel

        # Build Discord message
        send_kwargs = {}

        if content.text:
            chunks = self._chunk_text(content.text)
        else:
            chunks = [""]

        if content.embeds:
            send_kwargs["embeds"] = content.embeds

        if content.files:
            send_kwargs["files"] = [
                discord.File(f) if isinstance(f, (str, Path)) else f
                for f in content.files
            ]

        if content.reply_to:
            try:
                msg = await channel.fetch_message(content.reply_to)
                send_kwargs["reference"] = msg
            except Exception:
                pass

        if options.delete_after:
            send_kwargs["delete_after"] = options.delete_after

        # Send messages
        messages = []
        for i, chunk in enumerate(chunks):
            try:
                # Only include files/embeds in first message
                kwargs = send_kwargs.copy() if i == 0 else {}
                kwargs["content"] = chunk

                msg = await channel.send(**kwargs)
                messages.append(msg)

                # Small delay between chunks
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.5)

            except discord.Forbidden as e:
                logger.error(f"No permission to send in channel {channel_or_chat_id}: {e}")
                raise
            except discord.HTTPException as e:
                logger.error(f"Failed to send message to {channel_or_chat_id}: {e}")
                raise

        return messages[0] if len(messages) == 1 else messages

    async def edit(
        self,
        message_id: int,
        channel_or_chat_id: int,
        new_content: MessageContent,
    ) -> discord.Message:
        """Edit Discord message."""
        channel = self.bot.get_channel(channel_or_chat_id)
        if not channel:
            channel = await self.bot.fetch_channel(channel_or_chat_id)

        message = await channel.fetch_message(message_id)

        kwargs = {}
        if new_content.text is not None:
            kwargs["content"] = new_content.text
        if new_content.embeds is not None:
            kwargs["embeds"] = new_content.embeds

        return await message.edit(**kwargs)

    async def delete(self, message_id: int, channel_or_chat_id: int) -> None:
        """Delete Discord message."""
        channel = self.bot.get_channel(channel_or_chat_id)
        if not channel:
            channel = await self.bot.fetch_channel(channel_or_chat_id)

        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            logger.warning(f"Message {message_id} not found for deletion")
        except discord.Forbidden:
            logger.error(f"No permission to delete message {message_id}")


class TelegramMessaging(MessagingBackend):
    """Telegram messaging implementation."""

    def __init__(self, bot: telebot.TeleBot):
        self.bot = bot

    def _chunk_text(self, text: str, limit: int = 4096) -> list[str]:
        """Split text into Telegram-compatible chunks."""
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        chunks = []
        current = []
        current_len = 0

        for line in text.splitlines(True):
            if current_len + len(line) > limit:
                chunks.append("".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line)

        if current:
            chunks.append("".join(current))

        return chunks

    def _is_409_conflict(self, e: Exception) -> bool:
        """Check if exception is 409 Conflict."""
        try:
            return (
                getattr(e, "result", None)
                and getattr(e.result, "status_code", None) == 409
            ) or ("409" in str(e))
        except Exception:
            return False

    async def send(
        self,
        channel_or_chat_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> Any:
        """Send message to Telegram chat."""
        options = options or MessageOptions()

        chunks = self._chunk_text(content.text or "")

        messages = []

        for i, chunk in enumerate(chunks):
            attempt = 0
            while attempt < options.retry_count:
                try:
                    # Send text
                    if chunk:
                        msg = await asyncio.to_thread(
                            self.bot.send_message,
                            channel_or_chat_id,
                            chunk,
                            message_thread_id=options.thread_id,
                        )
                        messages.append(msg)

                    # Send files (only in first chunk)
                    if i == 0 and content.files:
                        for file in content.files:
                            file_msg = await asyncio.to_thread(
                                self.bot.send_document,
                                channel_or_chat_id,
                                file,
                                message_thread_id=options.thread_id,
                            )
                            messages.append(file_msg)

                    break  # Success

                except apihelper.ApiTelegramException as e:
                    # Handle 409 Conflict - retry without thread
                    if self._is_409_conflict(e) and options.thread_id is not None:
                        logger.warning("409 Conflict, retrying without thread_id")
                        options.thread_id = None
                        continue

                    attempt += 1
                    if attempt >= options.retry_count:
                        logger.error(f"Failed to send to Telegram after {attempt} attempts: {e}")
                        raise
                    await asyncio.sleep(1.0)

                except Exception as e:
                    logger.error(f"Telegram send error: {e}")
                    raise

            # Delay between chunks
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

        return messages[0] if len(messages) == 1 else messages

    async def edit(
        self,
        message_id: int,
        channel_or_chat_id: int,
        new_content: MessageContent,
    ) -> Any:
        """Edit Telegram message."""
        return await asyncio.to_thread(
            self.bot.edit_message_text,
            new_content.text or "",
            channel_or_chat_id,
            message_id,
        )

    async def delete(self, message_id: int, channel_or_chat_id: int) -> None:
        """Delete Telegram message."""
        try:
            await asyncio.to_thread(
                self.bot.delete_message,
                channel_or_chat_id,
                message_id,
            )
        except Exception as e:
            logger.warning(f"Failed to delete Telegram message {message_id}: {e}")


class UnifiedMessaging:
    """
    Unified messaging interface supporting both Discord and Telegram.

    Usage:
        messaging = UnifiedMessaging(discord_bot, telegram_bot)
        await messaging.send_discord(channel_id, MessageContent(text="Hello!"))
        await messaging.send_telegram(chat_id, MessageContent(text="Привет!"))
    """

    def __init__(
        self,
        discord_bot: discord.Client | None = None,
        telegram_bot: telebot.TeleBot | None = None,
    ):
        self.discord = DiscordMessaging(discord_bot) if discord_bot else None
        self.telegram = TelegramMessaging(telegram_bot) if telegram_bot else None

    async def send_discord(
        self,
        channel_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> Any:
        """Send message to Discord channel."""
        if not self.discord:
            raise KBRSException("Discord backend not initialized")
        return await self.discord.send(channel_id, content, options)

    async def send_telegram(
        self,
        chat_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> Any:
        """Send message to Telegram chat."""
        if not self.telegram:
            raise KBRSException("Telegram backend not initialized")
        return await self.telegram.send(chat_id, content, options)

    async def send_to_platform(
        self,
        platform: Literal["discord", "telegram"],
        target_id: int,
        content: MessageContent,
        options: MessageOptions | None = None,
    ) -> Any:
        """Send message to specified platform."""
        if platform == "discord":
            return await self.send_discord(target_id, content, options)
        elif platform == "telegram":
            return await self.send_telegram(target_id, content, options)
        else:
            raise ValueError(f"Unknown platform: {platform}")
