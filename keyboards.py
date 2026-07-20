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


def cashiers_kb(cashiers) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"👤 {c.name}", callback_data=f"csh:{c.id}")]
        for c in cashiers
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Другая касса", callback_data="csh:back")])
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
        [InlineKeyboardButton(text="👤 Другой кассир", callback_data="more:cashier")],
        [InlineKeyboardButton(text="✅ Завершить приёмку", callback_data="more:done")],
    ])


# ---------- Админ: пункты и кассиры ----------
def points_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить кассу", callback_data="padd")],
        [InlineKeyboardButton(text="📋 Кассы: вкл/выкл", callback_data="plist")],
        [InlineKeyboardButton(text="👤 Кассиры", callback_data="clist")],
    ])


def points_toggle_kb(points) -> InlineKeyboardMarkup:
    rows = []
    for p in points:
        mark = "🟢" if p.active else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {p.name}", callback_data=f"ptog:{p.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def points_pick_kb(points, prefix: str) -> InlineKeyboardMarkup:
    """Выбор кассы для управления её кассирами."""
    rows = [
        [InlineKeyboardButton(text=p.name, callback_data=f"{prefix}:{p.id}")]
        for p in points
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cashiers_admin_kb(cashiers, point_id: int) -> InlineKeyboardMarkup:
    rows = []
    for c in cashiers:
        mark = "🟢" if c.active else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {c.name}", callback_data=f"ctog:{c.id}"
        )])
    rows.append([InlineKeyboardButton(
        text="➕ Добавить кассира", callback_data=f"cadd:{point_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
