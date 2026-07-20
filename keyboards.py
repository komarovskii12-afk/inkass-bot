"""Клавиатуры бота."""
import datetime as dt

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from config import is_admin

_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

BTN_RECV = "📥 Принять инкассацию"
BTN_EDIT = "✏️ Исправить"
BTN_REPORT = "📊 Отчёт"
BTN_POINTS = "🏢 Пункты"
BTN_CANCEL = "❌ Отмена"


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_RECV)],
        [KeyboardButton(text=BTN_EDIT)],
        [KeyboardButton(text=BTN_REPORT)],
    ]
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


def week_kb(prefix: str, today: dt.date) -> InlineKeyboardMarkup:
    """Последние 7 дней кнопками + ручной ввод даты.

    В callback кладём саму дату (ISO), так что обработчику не нужно
    пересчитывать «сегодня» — нет рассинхрона, если день сменился
    между показом клавиатуры и нажатием.
    """
    def btn(d: dt.date, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label, callback_data=f"{prefix}:{d.isoformat()}"
        )

    def day_label(d: dt.date) -> str:
        return f"{_WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"

    rows = [[
        btn(today, f"Сегодня · {today.strftime('%d.%m')}"),
        btn(today - dt.timedelta(days=1),
            f"Вчера · {(today - dt.timedelta(days=1)).strftime('%d.%m')}"),
    ]]
    older = [today - dt.timedelta(days=i) for i in range(2, 7)]
    for i in range(0, len(older), 2):
        rows.append([btn(d, day_label(d)) for d in older[i:i + 2]])
    rows.append([InlineKeyboardButton(
        text="📅 Другая дата", callback_data=f"{prefix}:custom"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_again_kb() -> InlineKeyboardMarkup:
    """Кнопка под готовым отчётом: вернуться к выбору даты."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Выбрать другую дату", callback_data="pdt_again")],
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
        [InlineKeyboardButton(text="✏️ Исправить", callback_data="more:fix")],
        [InlineKeyboardButton(text="➕ Ещё номинал", callback_data="more:add")],
        [InlineKeyboardButton(text="👤 Другой кассир", callback_data="more:cashier")],
        [InlineKeyboardButton(text="✅ Завершить приёмку", callback_data="more:done")],
    ])


# ---------- Исправление записей ----------
def _short_point(name: str) -> str:
    """«265 — Торгова площа 4/27» -> «265»."""
    return name.split("—")[0].strip() or name


def _short_cashier(name: str) -> str:
    """«Зінченко Ніна» -> «Зінченко»."""
    return (name or "").split()[0] if name and name.strip() else "—"


def edit_list_kb(receipts) -> InlineKeyboardMarkup:
    rows = []
    for r in receipts:
        label = (
            f"{_short_point(r.point_name)} · {_short_cashier(r.cashier_name)} · "
            f"{r.denomination}: {r.qty_total}/{r.qty_normal}/"
            f"{r.qty_bad or 0}/{r.qty_work}"
        )
        rows.append([InlineKeyboardButton(text=label, callback_data=f"ed:{r.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_actions_kb(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить числа", callback_data=f"edn:{receipt_id}")],
        [InlineKeyboardButton(text="🗑 Удалить строку", callback_data=f"edd:{receipt_id}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="edback")],
    ])


def confirm_delete_kb(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"eddy:{receipt_id}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="edback")],
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
