#!/bin/bash
# Скрипт очистки перед запуском бота

set -e

echo "[cleanup] Starting cleanup process..."

# Загружаем .env
if [ -f /home/KBRS/kbrs_discord_bot/.env ]; then
    export $(grep -v '^#' /home/KBRS/kbrs_discord_bot/.env | xargs)
fi

# 1. Убиваем старые процессы Python бота
echo "[cleanup] Killing old bot processes..."
pkill -f "python.*bot.py" || echo "[cleanup] No old processes found"

# 2. Удаляем Telegram webhook (если был установлен)
if [ -n "$TG_BOT_TOKEN" ]; then
    echo "[cleanup] Removing Telegram webhook..."
    curl -s "https://api.telegram.org/bot${TG_BOT_TOKEN}/deleteWebhook" > /dev/null || echo "[cleanup] Failed to remove webhook"
else
    echo "[cleanup] TG_BOT_TOKEN not found, skipping webhook removal"
fi

# 3. Ждем 2 секунды для освобождения ресурсов
sleep 2

echo "[cleanup] Cleanup completed successfully"
