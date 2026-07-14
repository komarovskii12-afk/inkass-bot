"""Клавиатуры бота."""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from config import is_admin

BTN_RECV = "📥 Принять инкассацию"
BTN_REPORT = "📊 Отчёт"
BTN_POINTS = "🏢 Пункты"
BTN_CANCEL = "❌ Отмена"


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_RECV)], [KeyboardButton(text=BTN_REPORT)]]
    if is_admin(uid):
        rows.append([KeyboardButton(text=BTN_POINTS)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True
    )


def date_kb(prefix: str) -> InlineKeyboardMarkup:
    """Выбор даты: сегодня / вчера / другая."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data=f"{prefix}:today")],
        [InlineKeyboardButton(text="Вчера", callback_data=f"{prefix}:yesterday")],
        [InlineKeyboardButton(text="Другая дата", callback_data=f"{prefix}:custom")],
    ])


def points_kb(points) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=p.name, callback_data=f"pt:{p.id}")] for p in points]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_kb(currencies) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=c, callback_data=f"cur:{c}")] for c in currencies]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def denom_kb(denoms) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=str(d), callback_data=f"dn:{d}")] for d in denoms]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Ещё номинал", callback_data="more:add")],
        [InlineKeyboardButton(text="✅ Завершить приёмку", callback_data="more:done")],
    ])


def points_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пункт", callback_data="padd")],
        [InlineKeyboardButton(text="📋 Список / вкл-выкл", callback_data="plist")],
    ])


def points_toggle_kb(points) -> InlineKeyboardMarkup:
    rows = []
    for p in points:
        mark = "🟢" if p.active else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {p.name}", callback_data=f"ptog:{p.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
