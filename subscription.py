"""
subscription.py — обязательная подписка на каналы/группы.

Хранит список required-каналов в файле channels.json.
Экспортирует:
  - add_channel(channel_id, title, invite_link) -> bool
  - remove_channel(channel_id) -> bool
  - get_channels() -> list[dict]
  - check_subscribed(bot, user_id) -> (bool, list[dict])  # True = подписан на всё
  - sub_keyboard(unsubscribed) -> InlineKeyboardMarkup
  - sub_text(unsubscribed) -> str
"""

import json
import os
import asyncio
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ─────────────────────────────────────────
#  Хранилище каналов
# ─────────────────────────────────────────
_CHANNELS_FILE = "channels.json"

EMOJI_LOCK   = "5197288647275071607"
EMOJI_CHECK  = "5206607081334906820"
EMOJI_BACK   = "5906771962734057347"
EMOJI_RELOAD = "5271604874419647061"
EMOJI_HASH = "5206607081334906820"

def _load_channels() -> list[dict]:
    """Загружает список каналов из файла."""
    if not os.path.exists(_CHANNELS_FILE):
        return []
    try:
        with open(_CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_channels(channels: list[dict]) -> None:
    """Сохраняет список каналов в файл."""
    with open(_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def get_channels() -> list[dict]:
    """Возвращает список required-каналов."""
    return _load_channels()


def add_channel(channel_id: int | str, title: str, invite_link: str) -> bool:
    """
    Добавляет канал в список обязательных.
    channel_id — числовой ID (напр. -1001234567890) или @username.
    Возвращает False, если канал уже есть.
    """
    channels = _load_channels()
    cid = str(channel_id)
    for ch in channels:
        if str(ch["id"]) == cid:
            return False
    channels.append({
        "id":          cid,
        "title":       title,
        "invite_link": invite_link,
    })
    _save_channels(channels)
    return True


def remove_channel(channel_id: int | str) -> bool:
    """
    Удаляет канал из списка обязательных.
    Возвращает False, если канал не найден.
    """
    channels = _load_channels()
    cid      = str(channel_id)
    new_list = [ch for ch in channels if str(ch["id"]) != cid]
    if len(new_list) == len(channels):
        return False
    _save_channels(new_list)
    return True


# ─────────────────────────────────────────
#  Проверка подписки
# ─────────────────────────────────────────
async def check_subscribed(bot: Bot, user_id: int) -> tuple[bool, list[dict]]:
    """
    Проверяет подписку пользователя на все required-каналы.
    Возвращает (True, []) если подписан на всё,
    или (False, [список каналов, на которые не подписан]).
    """
    channels     = _load_channels()
    unsubscribed = []

    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            status = member.status
            # left / kicked / restricted (is_member=False) = не подписан
            if status in ("left", "kicked"):
                unsubscribed.append(ch)
            elif status == "restricted":
                # restricted может быть участником, проверяем is_member
                if not getattr(member, "is_member", False):
                    unsubscribed.append(ch)
        except (TelegramForbiddenError, TelegramBadRequest):
            # Бот не является администратором или канал недоступен — пропускаем
            pass
        except Exception:
            pass

    return (len(unsubscribed) == 0, unsubscribed)


# ─────────────────────────────────────────
#  Клавиатура и текст для «не подписан»
# ─────────────────────────────────────────
def sub_keyboard(unsubscribed: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками подписки и кнопкой «Проверить»."""
    rows = []
    for ch in unsubscribed:
        rows.append([InlineKeyboardButton(
            text=f"{ch['title']}",
            url=ch["invite_link"],
        )])
    rows.append([InlineKeyboardButton(
        text="проверить",
        callback_data="sub_check",
        icon_custom_emoji_id=EMOJI_HASH,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sub_text(unsubscribed: list[dict]) -> str:
    """Сообщение о необходимости подписки."""
    names = "\n".join(
        f'  • <b>{ch["title"]}</b>'
        for ch in unsubscribed
    )
    return (
        f'<tg-emoji emoji-id="{EMOJI_LOCK}">🔒</tg-emoji> <b>Доступ ограничен</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5397916757333654639">🔒</tg-emoji>Подпишитесь наканлы ниже!\n\n'
        f'{names}'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'После подписки нажмите кнопку <b>«проверить»</b>'
        f'</blockquote>'
    )
