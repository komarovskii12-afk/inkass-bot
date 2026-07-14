"""Логика бота: приёмка инкассации, отчёт по дате, управление пунктами."""
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
from db import Point, Receipt, Session
from keyboards import (
    BTN_CANCEL, BTN_POINTS, BTN_RECV, BTN_REPORT,
    cancel_menu, currency_kb, date_kb, denom_kb, main_menu,
    more_kb, points_admin_kb, points_kb, points_toggle_kb,
)

router = Router()


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


# ---------- Утилиты ----------
def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def parse_int(text: str) -> int | None:
    text = (text or "").strip().replace(" ", "")
    if not text.isdigit():
        return None
    return int(text)


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


# ---------- Старт / отмена ----------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это бот учёта инкассации изношенной валюты.\n\n"
        "• <b>Принять инкассацию</b> — зафиксировать приём с пункта\n"
        "• <b>Отчёт</b> — сводка за выбранный день"
        + ("\n• <b>Пункты</b> — управление списком точек" if is_admin(message.from_user.id) else ""),
        reply_markup=main_menu(message.from_user.id),
    )


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu(message.from_user.id))


# ---------- Приёмка: выбор даты ----------
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
    await _ask_point(cb.message, state)


@router.message(Recv.date)
async def recv_date_text(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("Не понял дату. Пример: 14.07.2026")
        return
    await state.update_data(report_date=d.isoformat())
    await _ask_point(message, state)


async def _ask_point(message: Message, state: FSMContext):
    async with Session() as s:
        points = await active_points(s)
    if not points:
        await state.clear()
        await message.answer(
            "Список пунктов пуст. Владелец должен добавить пункт в меню «Пункты».",
            reply_markup=main_menu(message.chat.id),
        )
        return
    await state.set_state(Recv.point)
    await message.answer("С какого пункта принимаем?", reply_markup=points_kb(points))


# ---------- Приёмка: пункт ----------
@router.callback_query(Recv.point, F.data.startswith("pt:"))
async def recv_point(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":", 1)[1])
    async with Session() as s:
        point = await s.get(Point, pid)
    if not point:
        await cb.answer("Пункт не найден", show_alert=True)
        return
    await state.update_data(point_id=point.id, point_name=point.name)
    await cb.answer()
    await _ask_currency(cb.message, state)


async def _ask_currency(message: Message, state: FSMContext):
    currencies = list(CURRENCIES.keys())
    if len(currencies) == 1:
        # Валюта одна — не спрашиваем, сразу к номиналу.
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
    denoms = CURRENCIES[data["currency"]]
    await state.set_state(Recv.denom)
    await message.answer(
        f"Номинал ({data['currency']}):", reply_markup=denom_kb(denoms)
    )


@router.callback_query(Recv.denom, F.data.startswith("dn:"))
async def recv_denom(cb: CallbackQuery, state: FSMContext):
    denom = int(cb.data.split(":", 1)[1])
    await state.update_data(denom=denom)
    await state.set_state(Recv.total)
    await cb.answer()
    await cb.message.answer(
        f"{(await state.get_data())['currency']} {denom}\n"
        "Сколько купюр <b>принято всего</b>? (число)"
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

    # Складываем строку в буфер приёмки.
    line = {
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
        f"Записал: {line['currency']} {line['denom']} — "
        f"принято {line['total']}, годных {line['normal']}, в работу {line['work']}.",
        reply_markup=more_kb(),
    )


# ---------- Приёмка: ещё / завершить ----------
@router.callback_query(Recv.more, F.data == "more:add")
async def recv_more_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    # Тот же пункт и дата, снова выбираем валюту/номинал.
    await _ask_currency(cb.message, state)


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
    for ln in lines:
        out.append(
            f"  {ln['currency']} {ln['denom']} — принято {ln['total']}, "
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
        await message.answer("Не понял дату. Пример: 14.07.2026")
        return
    await _send_report(message, state, d, message.from_user.id)


async def _send_report(message: Message, state: FSMContext, d: dt.date, uid: int):
    async with Session() as s:
        res = await s.execute(
            select(Receipt).where(Receipt.report_date == d).order_by(Receipt.point_name)
        )
        rows = res.scalars().all()

    await state.clear()
    if not rows:
        await message.answer(
            f"За {fmt_date(d)} записей нет.", reply_markup=main_menu(uid)
        )
        return

    await message.answer(_format_report(d, rows), reply_markup=main_menu(uid))


def _format_report(d: dt.date, rows: list[Receipt]) -> str:
    # group[point][(currency, denom)] = агрегаты
    by_point: dict[str, dict[tuple, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"total": 0, "normal": 0, "work": 0}
    ))
    for r in rows:
        cell = by_point[r.point_name][(r.currency, r.denomination)]
        cell["total"] += r.qty_total
        cell["normal"] += r.qty_normal
        cell["work"] += r.qty_work

    out = [f"📊 <b>Отчёт за {fmt_date(d)}</b>"]
    g_t = g_n = g_w = 0                # суммы в $
    gc_t = gc_n = gc_w = 0            # количество купюр

    for point in sorted(by_point):
        out.append(f"\n🏢 <b>{html.escape(point)}</b>")
        p_t = p_n = p_w = 0
        for (cur, denom) in sorted(by_point[point], key=lambda x: (x[0], -x[1])):
            c = by_point[point][(cur, denom)]
            out.append(
                f"  {cur} {denom} — принято {c['total']}, "
                f"годных {c['normal']}, в работу {c['work']}"
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


# ---------- Управление пунктами (только владелец) ----------
@router.message(F.text == BTN_POINTS)
async def points_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Только для владельца.")
        return
    await state.clear()
    await message.answer("Управление пунктами:", reply_markup=points_admin_kb())


@router.callback_query(F.data == "padd")
async def points_add_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    await state.set_state(Pts.add)
    await cb.answer()
    await cb.message.answer("Введите название нового пункта:", reply_markup=cancel_menu())


@router.message(Pts.add)
async def points_add_save(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пустое название. Введите ещё раз.")
        return
    async with Session() as s:
        exists = (await s.execute(select(Point).where(Point.name == name))).scalar_one_or_none()
        if exists:
            await message.answer("Такой пункт уже есть. Введите другое название.")
            return
        s.add(Point(name=name, active=True))
        await s.commit()
    await state.clear()
    await message.answer(f"Добавлен пункт: {html.escape(name)} 🟢",
                         reply_markup=main_menu(message.from_user.id))


@router.callback_query(F.data == "plist")
async def points_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    async with Session() as s:
        points = await all_points(s)
    await cb.answer()
    if not points:
        await cb.message.answer("Пунктов пока нет. Нажмите «Добавить пункт».")
        return
    await cb.message.answer(
        "Нажмите на пункт, чтобы включить/выключить его "
        "(🟢 активен, 🔴 скрыт):",
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
            await cb.answer("Пункт не найден", show_alert=True)
            return
        point.active = not point.active
        await s.commit()
        points = await all_points(s)
    await cb.answer("Готово")
    await cb.message.edit_reply_markup(reply_markup=points_toggle_kb(points))


# ---------- Фолбэк ----------
@router.message(StateFilter(None))
async def fallback(message: Message):
    await message.answer(
        "Выберите действие в меню ниже.",
        reply_markup=main_menu(message.from_user.id),
    )
