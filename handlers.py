"""Логика бота: приёмка инкассации, отчёт по дате, управление кассами и кассирами."""
import datetime as dt
import html
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from config import CURRENCIES, TZ, is_admin, is_allowed
from db import Cashier, Point, Receipt, Session
from keyboards import (
    BTN_CANCEL, BTN_EDIT, BTN_POINTS, BTN_RECV, BTN_REPORT,
    cancel_menu, cashiers_admin_kb, cashiers_kb, confirm_delete_kb, currency_kb,
    date_kb, denom_kb, edit_actions_kb, edit_list_kb, main_menu, more_kb,
    points_admin_kb, points_kb, points_pick_kb, points_toggle_kb,
)

router = Router()

NO_CASHIER = "—"


# ---------- Контроль доступа ----------
class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or not is_allowed(user.id):
            if isinstance(event, Message):
                await event.answer("⛔ Нет доступа. Обратитесь к владельцу.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Нет доступа", show_alert=True)
            return None
        return await handler(event, data)


router.message.middleware(AccessMiddleware())
router.callback_query.middleware(AccessMiddleware())


# ---------- Состояния ----------
class Recv(StatesGroup):
    date = State()
    point = State()
    cashier = State()
    currency = State()
    denom = State()
    total = State()
    normal = State()
    work = State()
    more = State()


class Rep(StatesGroup):
    date = State()


class Pts(StatesGroup):
    add = State()
    add_cashier = State()


class Edit(StatesGroup):
    total = State()
    normal = State()
    work = State()


# ---------- Утилиты ----------
def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def parse_int(text: str) -> int | None:
    text = (text or "").strip().replace(" ", "")
    return int(text) if text.isdigit() else None


def parse_date(text: str) -> dt.date | None:
    text = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def active_points(session):
    res = await session.execute(
        select(Point).where(Point.active.is_(True)).order_by(Point.name)
    )
    return res.scalars().all()


async def all_points(session):
    res = await session.execute(select(Point).order_by(Point.name))
    return res.scalars().all()


async def active_cashiers(session, point_id: int):
    res = await session.execute(
        select(Cashier)
        .where(Cashier.point_id == point_id, Cashier.active.is_(True))
        .order_by(Cashier.name)
    )
    return res.scalars().all()


async def all_cashiers(session, point_id: int):
    res = await session.execute(
        select(Cashier).where(Cashier.point_id == point_id).order_by(Cashier.name)
    )
    return res.scalars().all()


# ---------- Старт / отмена ----------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это бот учёта инкассации изношенной валюты.\n\n"
        "• <b>Принять инкассацию</b> — зафиксировать приём с кассы\n"
        "• <b>Отчёт</b> — сводка за выбранный день"
        + ("\n• <b>Пункты</b> — кассы и кассиры" if is_admin(message.from_user.id) else ""),
        reply_markup=main_menu(message.from_user.id),
    )


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu(message.from_user.id))


# ---------- Приёмка: дата ----------
@router.message(F.text == BTN_RECV)
async def recv_start(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(lines=[])
    await state.set_state(Recv.date)
    await message.answer("За какую дату приёмка?", reply_markup=cancel_menu())
    await message.answer("Выберите дату:", reply_markup=date_kb("rdt"))


@router.callback_query(Recv.date, F.data.startswith("rdt:"))
async def recv_date(cb: CallbackQuery, state: FSMContext):
    choice = cb.data.split(":", 1)[1]
    if choice == "today":
        d = today()
    elif choice == "yesterday":
        d = today() - dt.timedelta(days=1)
    else:
        await cb.message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
        await cb.answer()
        return
    await state.update_data(report_date=d.isoformat())
    await cb.answer()
    await _ask_point(cb.message, state, cb.from_user.id)


@router.message(Recv.date)
async def recv_date_text(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("Не понял дату. Пример: 20.07.2026")
        return
    await state.update_data(report_date=d.isoformat())
    await _ask_point(message, state, message.from_user.id)


async def _ask_point(message: Message, state: FSMContext, uid: int):
    async with Session() as s:
        points = await active_points(s)
    if not points:
        await state.clear()
        await message.answer(
            "Список касс пуст. Владелец должен добавить кассу в меню «Пункты».",
            reply_markup=main_menu(uid),
        )
        return
    await state.set_state(Recv.point)
    await message.answer("С какой кассы принимаем?", reply_markup=points_kb(points))


# ---------- Приёмка: касса ----------
@router.callback_query(Recv.point, F.data.startswith("pt:"))
async def recv_point(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        point = await s.get(Point, pid)
    if not point:
        await cb.answer("Касса не найдена", show_alert=True)
        return
    await state.update_data(point_id=point.id, point_name=point.name)
    await cb.answer()
    await _ask_cashier(cb.message, state)


async def _ask_cashier(message: Message, state: FSMContext):
    data = await state.get_data()
    async with Session() as s:
        cashiers = await active_cashiers(s, data["point_id"])
    if not cashiers:
        # У кассы не заведены кассиры — пропускаем шаг.
        await state.update_data(cashier_id=None, cashier_name=NO_CASHIER)
        await _ask_currency(message, state)
        return
    await state.set_state(Recv.cashier)
    await message.answer(
        f"🏢 {html.escape(data['point_name'])}\nОт какого кассира?",
        reply_markup=cashiers_kb(cashiers),
    )


@router.callback_query(Recv.cashier, F.data == "csh:back")
async def recv_cashier_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _ask_point(cb.message, state, cb.from_user.id)


@router.callback_query(Recv.cashier, F.data.startswith("csh:"))
async def recv_cashier(cb: CallbackQuery, state: FSMContext):
    cid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        cashier = await s.get(Cashier, cid)
    if not cashier:
        await cb.answer("Кассир не найден", show_alert=True)
        return
    await state.update_data(cashier_id=cashier.id, cashier_name=cashier.name)
    await cb.answer()
    await _ask_currency(cb.message, state)


# ---------- Приёмка: валюта и номинал ----------
async def _ask_currency(message: Message, state: FSMContext):
    currencies = list(CURRENCIES.keys())
    if len(currencies) == 1:
        await state.update_data(currency=currencies[0])
        await _ask_denom(message, state)
        return
    await state.set_state(Recv.currency)
    await message.answer("Валюта:", reply_markup=currency_kb(currencies))


@router.callback_query(Recv.currency, F.data.startswith("cur:"))
async def recv_currency(cb: CallbackQuery, state: FSMContext):
    cur = cb.data.split(":", 1)[1]
    if cur not in CURRENCIES:
        await cb.answer("Нет такой валюты", show_alert=True)
        return
    await state.update_data(currency=cur)
    await cb.answer()
    await _ask_denom(cb.message, state)


async def _ask_denom(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Recv.denom)
    await message.answer(
        f"👤 {html.escape(data.get('cashier_name', NO_CASHIER))}\n"
        f"Номинал ({data['currency']}):",
        reply_markup=denom_kb(CURRENCIES[data["currency"]]),
    )


@router.callback_query(Recv.denom, F.data.startswith("dn:"))
async def recv_denom(cb: CallbackQuery, state: FSMContext):
    denom = int(cb.data.split(":", 1)[1])
    await state.update_data(denom=denom)
    await state.set_state(Recv.total)
    data = await state.get_data()
    await cb.answer()
    await cb.message.answer(
        f"{data['currency']} {denom}\nСколько купюр <b>принято всего</b>? (число)"
    )


# ---------- Приёмка: числа ----------
@router.message(Recv.total)
async def recv_total(message: Message, state: FSMContext):
    n = parse_int(message.text)
    if n is None or n <= 0:
        await message.answer("Нужно положительное число. Попробуйте ещё раз.")
        return
    await state.update_data(total=n)
    await state.set_state(Recv.normal)
    await message.answer("Сколько из них <b>в нормальном (годном)</b> состоянии?")


@router.message(Recv.normal)
async def recv_normal(message: Message, state: FSMContext):
    n = parse_int(message.text)
    data = await state.get_data()
    if n is None:
        await message.answer("Нужно число (можно 0).")
        return
    if n > data["total"]:
        await message.answer(f"Не больше принятых ({data['total']}). Введите ещё раз.")
        return
    await state.update_data(normal=n)
    await state.set_state(Recv.work)
    await message.answer("Сколько купюр <b>взято в работу</b>?")


@router.message(Recv.work)
async def recv_work(message: Message, state: FSMContext):
    n = parse_int(message.text)
    data = await state.get_data()
    if n is None:
        await message.answer("Нужно число (можно 0).")
        return
    if n > data["total"]:
        await message.answer(f"Не больше принятых ({data['total']}). Введите ещё раз.")
        return

    line = {
        "cashier_id": data.get("cashier_id"),
        "cashier_name": data.get("cashier_name", NO_CASHIER),
        "currency": data["currency"],
        "denom": data["denom"],
        "total": data["total"],
        "normal": data["normal"],
        "work": n,
    }
    lines = data.get("lines", [])
    lines.append(line)
    await state.update_data(lines=lines)
    await state.set_state(Recv.more)

    await message.answer(
        f"Записал: {line['cashier_name']} · {line['currency']} {line['denom']} — "
        f"принято {line['total']}, годных {line['normal']}, в работу {line['work']}.",
        reply_markup=more_kb(),
    )


# ---------- Приёмка: ещё / другой кассир / завершить ----------
@router.callback_query(Recv.more, F.data == "more:add")
async def recv_more_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _ask_currency(cb.message, state)


@router.callback_query(Recv.more, F.data == "more:cashier")
async def recv_more_cashier(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _ask_cashier(cb.message, state)


@router.callback_query(Recv.more, F.data == "more:done")
async def recv_more_done(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lines = data.get("lines", [])
    if not lines:
        await cb.answer("Нет строк для сохранения", show_alert=True)
        return

    report_date = dt.date.fromisoformat(data["report_date"])
    session_id = uuid.uuid4().hex
    async with Session() as s:
        for ln in lines:
            s.add(Receipt(
                session_id=session_id,
                report_date=report_date,
                worker_id=cb.from_user.id,
                worker_name=cb.from_user.full_name,
                point_id=data["point_id"],
                point_name=data["point_name"],
                cashier_id=ln.get("cashier_id"),
                cashier_name=ln.get("cashier_name", NO_CASHIER),
                currency=ln["currency"],
                denomination=ln["denom"],
                qty_total=ln["total"],
                qty_normal=ln["normal"],
                qty_work=ln["work"],
            ))
        await s.commit()

    summary = _receipt_summary(data["point_name"], report_date, lines)
    await state.clear()
    await cb.answer("Сохранено ✅")
    await cb.message.answer(summary, reply_markup=main_menu(cb.from_user.id))


def _receipt_summary(point_name: str, d: dt.date, lines: list[dict]) -> str:
    out = [f"✅ <b>Приёмка сохранена</b>\n🏢 {html.escape(point_name)} · {fmt_date(d)}\n"]
    t_amount = n_amount = w_amount = 0
    by_cashier: dict[str, list[dict]] = defaultdict(list)
    for ln in lines:
        by_cashier[ln.get("cashier_name", NO_CASHIER)].append(ln)

    for cashier in sorted(by_cashier):
        out.append(f"👤 <b>{html.escape(cashier)}</b>")
        for ln in by_cashier[cashier]:
            out.append(
                f"   {ln['currency']} {ln['denom']} — принято {ln['total']}, "
                f"годных {ln['normal']}, в работу {ln['work']}"
            )
            t_amount += ln["total"] * ln["denom"]
            n_amount += ln["normal"] * ln["denom"]
            w_amount += ln["work"] * ln["denom"]

    out.append(
        f"\nΣ принято: ${t_amount:,} · годных: ${n_amount:,} · в работу: ${w_amount:,}"
    )
    return "\n".join(out)


# ---------- Отчёт ----------
@router.message(F.text == BTN_REPORT)
async def report_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Rep.date)
    await message.answer("За какой день отчёт?", reply_markup=cancel_menu())
    await message.answer("Выберите дату:", reply_markup=date_kb("pdt"))


@router.callback_query(Rep.date, F.data.startswith("pdt:"))
async def report_date_cb(cb: CallbackQuery, state: FSMContext):
    choice = cb.data.split(":", 1)[1]
    if choice == "today":
        d = today()
    elif choice == "yesterday":
        d = today() - dt.timedelta(days=1)
    else:
        await cb.message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
        await cb.answer()
        return
    await cb.answer()
    await _send_report(cb.message, state, d, cb.from_user.id)


@router.message(Rep.date)
async def report_date_text(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("Не понял дату. Пример: 20.07.2026")
        return
    await _send_report(message, state, d, message.from_user.id)


async def _send_report(message: Message, state: FSMContext, d: dt.date, uid: int):
    async with Session() as s:
        res = await s.execute(
            select(Receipt)
            .where(Receipt.report_date == d, Receipt.deleted_at.is_(None))
            .order_by(Receipt.point_name)
        )
        rows = res.scalars().all()

    await state.clear()
    if not rows:
        await message.answer(f"За {fmt_date(d)} записей нет.", reply_markup=main_menu(uid))
        return
    await message.answer(_format_report(d, rows), reply_markup=main_menu(uid))


# ---------- Исправление записей (только за сегодняшнюю дату) ----------
async def _editable(session, uid: int):
    """Строки за сегодня, доступные пользователю: свои, а владельцу — все."""
    q = select(Receipt).where(
        Receipt.report_date == today(),
        Receipt.deleted_at.is_(None),
    )
    if not is_admin(uid):
        q = q.where(Receipt.worker_id == uid)
    res = await session.execute(q.order_by(Receipt.id.desc()).limit(30))
    return res.scalars().all()


def _receipt_card(r: Receipt) -> str:
    mark = " ✏️ (уже правилась)" if r.edited_at else ""
    return (
        f"🏢 {html.escape(r.point_name)}\n"
        f"👤 {html.escape(r.cashier_name or NO_CASHIER)}\n"
        f"💵 {r.currency} {r.denomination}\n\n"
        f"Принято: <b>{r.qty_total}</b>\n"
        f"Годных: <b>{r.qty_normal}</b>\n"
        f"В работу: <b>{r.qty_work}</b>\n"
        f"Внёс: {html.escape(r.worker_name)}{mark}"
    )


@router.message(F.text == BTN_EDIT)
async def edit_start(message: Message, state: FSMContext):
    await state.clear()
    async with Session() as s:
        rows = await _editable(s, message.from_user.id)
    if not rows:
        await message.answer(
            "За сегодня нет записей, которые можно исправить.\n"
            "Править можно только приёмки с сегодняшней датой.",
            reply_markup=main_menu(message.from_user.id),
        )
        return
    await message.answer(
        f"Записи за {fmt_date(today())}. Выберите строку:\n"
        "<i>формат: касса · кассир · номинал: принято/годных/в работу</i>",
        reply_markup=edit_list_kb(rows),
    )


@router.callback_query(F.data == "edback")
async def edit_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    async with Session() as s:
        rows = await _editable(s, cb.from_user.id)
    await cb.answer()
    if not rows:
        await cb.message.answer("Записей больше нет.", reply_markup=main_menu(cb.from_user.id))
        return
    await cb.message.answer("Выберите строку:", reply_markup=edit_list_kb(rows))


async def _get_editable(session, rid: int, uid: int) -> Receipt | None:
    """Строку можно трогать: она сегодняшняя, не удалена и принадлежит юзеру."""
    r = await session.get(Receipt, rid)
    if r is None or r.deleted_at is not None:
        return None
    if r.report_date != today():
        return None
    if not is_admin(uid) and r.worker_id != uid:
        return None
    return r


@router.callback_query(F.data.startswith("ed:"))
async def edit_pick(cb: CallbackQuery, state: FSMContext):
    rid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        r = await _get_editable(s, rid, cb.from_user.id)
    if r is None:
        await cb.answer("Эту запись править нельзя", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(_receipt_card(r), reply_markup=edit_actions_kb(rid))


@router.callback_query(F.data.startswith("edn:"))
async def edit_numbers_start(cb: CallbackQuery, state: FSMContext):
    rid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        r = await _get_editable(s, rid, cb.from_user.id)
    if r is None:
        await cb.answer("Эту запись править нельзя", show_alert=True)
        return
    await state.update_data(edit_id=rid)
    await state.set_state(Edit.total)
    await cb.answer()
    await cb.message.answer(
        f"Было принято: {r.qty_total}\nВведите новое количество <b>принято всего</b>:",
        reply_markup=cancel_menu(),
    )


@router.message(Edit.total)
async def edit_total(message: Message, state: FSMContext):
    n = parse_int(message.text)
    if n is None or n <= 0:
        await message.answer("Нужно положительное число.")
        return
    await state.update_data(new_total=n)
    await state.set_state(Edit.normal)
    await message.answer("Сколько из них <b>годных</b>?")


@router.message(Edit.normal)
async def edit_normal(message: Message, state: FSMContext):
    n = parse_int(message.text)
    data = await state.get_data()
    if n is None:
        await message.answer("Нужно число (можно 0).")
        return
    if n > data["new_total"]:
        await message.answer(f"Не больше принятых ({data['new_total']}).")
        return
    await state.update_data(new_normal=n)
    await state.set_state(Edit.work)
    await message.answer("Сколько <b>взято в работу</b>?")


@router.message(Edit.work)
async def edit_work(message: Message, state: FSMContext):
    n = parse_int(message.text)
    data = await state.get_data()
    if n is None:
        await message.answer("Нужно число (можно 0).")
        return
    if n > data["new_total"]:
        await message.answer(f"Не больше принятых ({data['new_total']}).")
        return

    async with Session() as s:
        r = await _get_editable(s, data["edit_id"], message.from_user.id)
        if r is None:
            await state.clear()
            await message.answer(
                "Запись больше недоступна для правки.",
                reply_markup=main_menu(message.from_user.id),
            )
            return
        was = (r.qty_total, r.qty_normal, r.qty_work)
        r.qty_total = data["new_total"]
        r.qty_normal = data["new_normal"]
        r.qty_work = n
        r.edited_at = dt.datetime.now(dt.timezone.utc)
        r.changed_by = message.from_user.full_name
        await s.commit()
        card = _receipt_card(r)

    await state.clear()
    await message.answer(
        f"✅ Исправлено.\nБыло: {was[0]}/{was[1]}/{was[2]} → "
        f"стало: {data['new_total']}/{data['new_normal']}/{n}\n\n{card}",
        reply_markup=main_menu(message.from_user.id),
    )


@router.callback_query(F.data.startswith("eddy:"))
async def edit_delete_confirmed(cb: CallbackQuery, state: FSMContext):
    rid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        r = await _get_editable(s, rid, cb.from_user.id)
        if r is None:
            await cb.answer("Эту запись удалить нельзя", show_alert=True)
            return
        r.deleted_at = dt.datetime.now(dt.timezone.utc)
        r.changed_by = cb.from_user.full_name
        await s.commit()
        label = f"{r.point_name} · {r.cashier_name} · {r.currency} {r.denomination}"
    await state.clear()
    await cb.answer("Удалено")
    await cb.message.answer(
        f"🗑 Строка удалена из отчётов:\n{html.escape(label)}\n\n"
        "<i>Запись сохранена в базе со следом об удалении.</i>",
        reply_markup=main_menu(cb.from_user.id),
    )


@router.callback_query(F.data.startswith("edd:"))
async def edit_delete_ask(cb: CallbackQuery):
    rid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        r = await _get_editable(s, rid, cb.from_user.id)
    if r is None:
        await cb.answer("Эту запись удалить нельзя", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(
        f"Удалить эту строку?\n\n{_receipt_card(r)}",
        reply_markup=confirm_delete_kb(rid),
    )


def _format_report(d: dt.date, rows: list[Receipt]) -> str:
    # by_point[точка][кассир][(валюта, номинал)] = агрегаты
    tree: dict[str, dict[str, dict[tuple, dict]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(
            lambda: {"total": 0, "normal": 0, "work": 0, "edited": False}
        ))
    )
    for r in rows:
        cell = tree[r.point_name][r.cashier_name or NO_CASHIER][(r.currency, r.denomination)]
        cell["total"] += r.qty_total
        cell["normal"] += r.qty_normal
        cell["work"] += r.qty_work
        if r.edited_at:
            cell["edited"] = True

    out = [f"📊 <b>Отчёт за {fmt_date(d)}</b>"]
    g_t = g_n = g_w = 0                # суммы в $
    gc_t = gc_n = gc_w = 0             # количество купюр

    for point in sorted(tree):
        out.append(f"\n🏢 <b>{html.escape(point)}</b>")
        p_t = p_n = p_w = 0
        for cashier in sorted(tree[point]):
            out.append(f"  👤 {html.escape(cashier)}")
            cells = tree[point][cashier]
            for (cur, denom) in sorted(cells, key=lambda x: (x[0], -x[1])):
                c = cells[(cur, denom)]
                out.append(
                    f"     {cur} {denom} — принято {c['total']}, "
                    f"годных {c['normal']}, в работу {c['work']}"
                    + (" ✏️" if c["edited"] else "")
                )
                p_t += c["total"] * denom
                p_n += c["normal"] * denom
                p_w += c["work"] * denom
                gc_t += c["total"]
                gc_n += c["normal"]
                gc_w += c["work"]
        out.append(f"  Σ ${p_t:,} принято · ${p_n:,} годных · ${p_w:,} в работу")
        g_t += p_t
        g_n += p_n
        g_w += p_w

    out.append("\n━━━━━━━━━━━━━━")
    out.append("<b>ИТОГО за день</b>")
    out.append(f"  Купюр: принято {gc_t}, годных {gc_n}, в работу {gc_w}")
    out.append(f"  Сумма: ${g_t:,} принято · ${g_n:,} годных · ${g_w:,} в работу")
    return "\n".join(out)


# ---------- Админ: кассы ----------
@router.message(F.text == BTN_POINTS)
async def points_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Только для владельца.")
        return
    await state.clear()
    await message.answer("Управление кассами и кассирами:", reply_markup=points_admin_kb())


@router.callback_query(F.data == "padd")
async def points_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    await state.set_state(Pts.add)
    await cb.answer()
    await cb.message.answer(
        "Введите название кассы в формате «номер — адрес».\n"
        "Например: <code>701 — Соборна 12</code>",
        reply_markup=cancel_menu(),
    )


@router.message(Pts.add)
async def points_add_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пустое название. Введите ещё раз.")
        return
    async with Session() as s:
        exists = (await s.execute(select(Point).where(Point.name == name))).scalar_one_or_none()
        if exists:
            await message.answer("Такая касса уже есть. Введите другое название.")
            return
        s.add(Point(name=name, active=True))
        await s.commit()
    await state.clear()
    await message.answer(
        f"Добавлена касса: {html.escape(name)} 🟢\n"
        "Кассиров для неё заведите в меню «Пункты» → «Кассиры».",
        reply_markup=main_menu(message.from_user.id),
    )


@router.callback_query(F.data == "plist")
async def points_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    async with Session() as s:
        points = await all_points(s)
    await cb.answer()
    if not points:
        await cb.message.answer("Касс пока нет. Нажмите «Добавить кассу».")
        return
    await cb.message.answer(
        "Нажмите на кассу, чтобы включить/выключить её (🟢 активна, 🔴 скрыта):",
        reply_markup=points_toggle_kb(points),
    )


@router.callback_query(F.data.startswith("ptog:"))
async def points_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    pid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        point = await s.get(Point, pid)
        if not point:
            await cb.answer("Касса не найдена", show_alert=True)
            return
        point.active = not point.active
        await s.commit()
        points = await all_points(s)
    await cb.answer("Готово")
    await cb.message.edit_reply_markup(reply_markup=points_toggle_kb(points))


# ---------- Админ: кассиры ----------
@router.callback_query(F.data == "clist")
async def cashiers_pick_point(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    async with Session() as s:
        points = await all_points(s)
    await cb.answer()
    if not points:
        await cb.message.answer("Сначала добавьте кассу.")
        return
    await cb.message.answer(
        "Выберите кассу, чтобы посмотреть её кассиров:",
        reply_markup=points_pick_kb(points, "cpt"),
    )


@router.callback_query(F.data.startswith("cpt:"))
async def cashiers_show(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    pid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        point = await s.get(Point, pid)
        cashiers = await all_cashiers(s, pid)
    await cb.answer()
    title = html.escape(point.name) if point else "Касса"
    await cb.message.answer(
        f"🏢 <b>{title}</b>\nКассиры (нажмите, чтобы включить/выключить):"
        if cashiers else f"🏢 <b>{title}</b>\nКассиров пока нет.",
        reply_markup=cashiers_admin_kb(cashiers, pid),
    )


@router.callback_query(F.data.startswith("ctog:"))
async def cashier_toggle(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    cid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        cashier = await s.get(Cashier, cid)
        if not cashier:
            await cb.answer("Кассир не найден", show_alert=True)
            return
        cashier.active = not cashier.active
        await s.commit()
        cashiers = await all_cashiers(s, cashier.point_id)
        pid = cashier.point_id
    await cb.answer("Готово")
    await cb.message.edit_reply_markup(reply_markup=cashiers_admin_kb(cashiers, pid))


@router.callback_query(F.data.startswith("cadd:"))
async def cashier_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    pid = int(cb.data.split(":", 1)[1])
    await state.update_data(cashier_point_id=pid)
    await state.set_state(Pts.add_cashier)
    await cb.answer()
    await cb.message.answer("Введите имя и фамилию кассира:", reply_markup=cancel_menu())


@router.message(Pts.add_cashier)
async def cashier_add_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пустое имя. Введите ещё раз.")
        return
    data = await state.get_data()
    pid = data.get("cashier_point_id")
    async with Session() as s:
        dup = (await s.execute(
            select(Cashier).where(Cashier.point_id == pid, Cashier.name == name)
        )).scalar_one_or_none()
        if dup:
            await message.answer("Такой кассир на этой кассе уже есть.")
            return
        s.add(Cashier(point_id=pid, name=name, active=True))
        await s.commit()
    await state.clear()
    await message.answer(
        f"Добавлен кассир: {html.escape(name)} 🟢",
        reply_markup=main_menu(message.from_user.id),
    )


# ---------- Фолбэк ----------
@router.message(StateFilter(None))
async def fallback(message: Message):
    await message.answer(
        "Выберите действие в меню ниже.",
        reply_markup=main_menu(message.from_user.id),
    )
