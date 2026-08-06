"""Сейф: остатки по корзинам и операции движения денег.

Модель простая: фонд фиксированного размера разложен по трём корзинам.
  💵 нормальные — готовы к выдаче на кассы
  🔧 в работе   — взяты в восстановление
  🗑 неликвид   — ждут сдачи в банк

Приёмка с кассы идёт обменом 1:1 (забрали изношенные — отдали столько же
нормальными), поэтому сумма фонда от неё не меняется, меняется распределение.
Операция записывается автоматически при завершении приёмки.
"""
import datetime as dt
import html

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import func, select

from config import TZ, is_admin, is_allowed
from db import SafeOp, Session

router = Router()

BTN_SAFE = "📦 Сейф"
BTN_WORK = "🔧 В работе"

KIND_TITLE = {
    "intake": "📥 Приёмка",
    "fund": "➕ Пополнение фонда",
    "restored": "🔧 Восстановлено",
    "failed": "🗑 Не восстановить",
    "bank": "🏦 Сдано в банк",
}


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or not is_allowed(user.id):
            return None
        return await handler(event, data)


router.message.middleware(AccessMiddleware())
router.callback_query.middleware(AccessMiddleware())


class SafeOps(StatesGroup):
    fund = State()
    setfund = State()
    restored = State()
    failed = State()
    bank_amount = State()
    bank_fee = State()


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def parse_money(text: str) -> int | None:
    """Принимаем «1200», «1 200», «1200$», «1.200»."""
    t = (text or "").strip().replace(" ", "").replace("$", "")
    t = t.replace(".", "").replace(",", "")
    return int(t) if t.isdigit() and int(t) > 0 else None


async def balance(session) -> dict:
    row = (await session.execute(select(
        func.coalesce(func.sum(SafeOp.d_normal), 0),
        func.coalesce(func.sum(SafeOp.d_work), 0),
        func.coalesce(func.sum(SafeOp.d_bad), 0),
        func.coalesce(func.sum(SafeOp.fee), 0),
    ))).one()
    normal, work, bad, fee = (int(x) for x in row)
    return {
        "normal": normal, "work": work, "bad": bad, "fee": fee,
        "fund": normal + work + bad,
    }


async def add_op(session, user, kind: str, *, d_normal: int = 0, d_work: int = 0,
                 d_bad: int = 0, fee: int = 0, note: str | None = None) -> None:
    session.add(SafeOp(
        op_date=today(), user_id=user.id, user_name=user.full_name, kind=kind,
        d_normal=d_normal, d_work=d_work, d_bad=d_bad, fee=fee, note=note,
    ))


async def log_intake(session, user, total: int, normal: int, work: int,
                     bad: int, point_name: str) -> None:
    """Операция сейфа по итогам приёмки (обмен 1:1 с кассой)."""
    await add_op(
        session, user, "intake",
        d_normal=normal - total,   # выдали кассе всю сумму, вернулись только годные
        d_work=work,
        d_bad=bad,
        note=point_name,
    )


def safe_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔧 Восстановлено", callback_data="sf:restored")],
        [InlineKeyboardButton(text="🗑 Не восстановить", callback_data="sf:failed")],
        [InlineKeyboardButton(text="🏦 Сдал в банк", callback_data="sf:bank")],
        [InlineKeyboardButton(text="📜 История", callback_data="sf:log")],
    ]
    if is_admin(uid):
        # Фонд — зона владельца: работник его не видит и не меняет.
        rows.insert(0, [
            InlineKeyboardButton(text="➕ Пополнить фонд", callback_data="sf:fund"),
            InlineKeyboardButton(text="✏️ Изменить фонд", callback_data="sf:setfund"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card(b: dict) -> str:
    return (
        "📦 <b>Сейф</b>\n\n"
        f"💵 Нормальные: <b>${b['normal']:,}</b>\n"
        f"🔧 В работе: <b>${b['work']:,}</b>\n"
        f"🗑 Неликвид: <b>${b['bad']:,}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Фонд: <b>${b['fund']:,}</b>"
        + (f"\nКомиссия банка всего: ${b['fee']:,}" if b["fee"] else "")
    )


@router.message(F.text == BTN_SAFE)
async def safe_show(message: Message, state: FSMContext):
    await state.clear()
    async with Session() as s:
        b = await balance(s)
    hint = ""
    if b["fund"] == 0:
        hint = ("\n\n<i>Фонд пуст. Владелец: «Пополнить фонд» — "
                "введите сумму, которую завезли для работы.</i>")
    elif b["normal"] < 0:
        hint = ("\n\n⚠️ <i>Нормальные ушли в минус: выдано на кассы больше, "
                "чем было в сейфе. Проверьте пополнение фонда.</i>")
    await message.answer(_card(b) + hint, reply_markup=safe_kb(message.from_user.id))


# ---------- Что сейчас в работе ----------
@router.message(F.text == BTN_WORK)
async def work_show(message: Message, state: FSMContext):
    """Мгновенный срез: сколько в работе и откуда это пришло."""
    await state.clear()
    async with Session() as s:
        b = await balance(s)
        rows = (await s.execute(
            select(SafeOp)
            .where(SafeOp.d_work != 0)
            .order_by(SafeOp.id.desc())
            .limit(12)
        )).scalars().all()

    if b["work"] == 0 and not rows:
        await message.answer(
            "🔧 В работе сейчас пусто.\n"
            "<i>Купюры попадают сюда при приёмке с кассы.</i>"
        )
        return

    # Списать из работы можно прямо отсюда — не заходя в «Сейф».
    actions = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔧 Восстановлено", callback_data="sf:restored")],
        [InlineKeyboardButton(text="🗑 Не восстановить", callback_data="sf:failed")],
    ]) if b["work"] > 0 else None

    out = [f"🔧 <b>В работе сейчас: ${b['work']:,}</b>\n"]
    if rows:
        out.append("Последние движения:")
        for r in rows:
            src = f" · {html.escape(r.note)}" if r.note else ""
            title = {
                "intake": "приёмка",
                "restored": "восстановлено",
                "failed": "не восстановить",
            }.get(r.kind, r.kind)
            out.append(
                f"  {r.op_date.strftime('%d.%m')}  "
                f"<b>{r.d_work:+,}</b>  {title}{src}"
            )
    out.append(
        f"\n📦 Остальное в сейфе: нормальные ${b['normal']:,} · "
        f"неликвид ${b['bad']:,}"
    )
    if b["work"] > 0:
        out.append("\n<i>Списать из работы — кнопками ниже.</i>")
    await message.answer("\n".join(out), reply_markup=actions)


# ---------- Пополнение фонда ----------
@router.callback_query(F.data == "sf:fund")
async def fund_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    await state.set_state(SafeOps.fund)
    await cb.answer()
    await cb.message.answer(
        "Сколько завезли в сейф? Введите сумму в долларах (например <code>5000</code>):"
    )


@router.message(SafeOps.fund)
async def fund_save(message: Message, state: FSMContext):
    amt = parse_money(message.text)
    if amt is None:
        await message.answer("Нужна сумма числом. Например: 5000")
        return
    async with Session() as s:
        await add_op(s, message.from_user, "fund", d_normal=amt)
        await s.commit()
        b = await balance(s)
    await state.clear()
    await message.answer(
        f"➕ Фонд пополнен на ${amt:,}\n\n{_card(b)}",
        reply_markup=safe_kb(message.from_user.id),
    )


# ---------- Изменение размера фонда (только владелец) ----------
@router.callback_query(F.data == "sf:setfund")
async def setfund_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для владельца", show_alert=True)
        return
    async with Session() as s:
        b = await balance(s)
    await state.set_state(SafeOps.setfund)
    await cb.answer()
    await cb.message.answer(
        f"Сейчас фонд ${b['fund']:,}.\n"
        "Введите, каким он должен стать — разницу спишу или добавлю к нормальным."
    )


@router.message(SafeOps.setfund)
async def setfund_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip().replace(" ", "").replace("$", "")
    new_fund = 0 if text == "0" else (parse_money(message.text) or -1)
    if new_fund < 0:
        await message.answer("Нужна сумма числом. Например: 6000")
        return

    async with Session() as s:
        b = await balance(s)
        delta = new_fund - b["fund"]
        if delta == 0:
            await state.clear()
            await message.answer(
                f"Фонд и так ${new_fund:,} — ничего не меняю.",
                reply_markup=safe_kb(message.from_user.id),
            )
            return
        if b["normal"] + delta < 0:
            await message.answer(
                f"Так нельзя: свободных нормальных всего ${b['normal']:,}, "
                f"из них не вычесть ${-delta:,}.\n"
                f"Минимально возможный фонд сейчас — "
                f"${b['fund'] - b['normal']:,} (в работе + неликвид)."
            )
            return
        await add_op(s, message.from_user, "fund", d_normal=delta,
                     note=f"фонд {b['fund']:,} → {new_fund:,}")
        await s.commit()
        b = await balance(s)

    await state.clear()
    sign = "увеличен" if delta > 0 else "уменьшен"
    await message.answer(
        f"✏️ Фонд {sign} на ${abs(delta):,}.\n\n{_card(b)}",
        reply_markup=safe_kb(message.from_user.id),
    )


# ---------- Восстановлено: в работе → нормальные ----------
@router.callback_query(F.data == "sf:restored")
async def restored_start(cb: CallbackQuery, state: FSMContext):
    async with Session() as s:
        b = await balance(s)
    if b["work"] <= 0:
        await cb.answer("В работе ничего нет", show_alert=True)
        return
    await state.set_state(SafeOps.restored)
    await cb.answer()
    await cb.message.answer(
        f"В работе сейчас ${b['work']:,}.\n"
        "На какую сумму купюры <b>восстановлены</b> и стали нормальными?"
    )


@router.message(SafeOps.restored)
async def restored_save(message: Message, state: FSMContext):
    amt = parse_money(message.text)
    if amt is None:
        await message.answer("Нужна сумма числом.")
        return
    async with Session() as s:
        b = await balance(s)
        if amt > b["work"]:
            await message.answer(f"В работе только ${b['work']:,}. Введите меньше.")
            return
        await add_op(s, message.from_user, "restored", d_work=-amt, d_normal=amt)
        await s.commit()
        b = await balance(s)
    await state.clear()
    await message.answer(
        f"🔧 Восстановлено ${amt:,} — переложено в нормальные.\n\n{_card(b)}",
        reply_markup=safe_kb(message.from_user.id),
    )


# ---------- Не вышло: в работе → неликвид ----------
@router.callback_query(F.data == "sf:failed")
async def failed_start(cb: CallbackQuery, state: FSMContext):
    async with Session() as s:
        b = await balance(s)
    if b["work"] <= 0:
        await cb.answer("В работе ничего нет", show_alert=True)
        return
    await state.set_state(SafeOps.failed)
    await cb.answer()
    await cb.message.answer(
        f"В работе сейчас ${b['work']:,}.\n"
        "На какую сумму купюры <b>не удалось восстановить</b>?"
    )


@router.message(SafeOps.failed)
async def failed_save(message: Message, state: FSMContext):
    amt = parse_money(message.text)
    if amt is None:
        await message.answer("Нужна сумма числом.")
        return
    async with Session() as s:
        b = await balance(s)
        if amt > b["work"]:
            await message.answer(f"В работе только ${b['work']:,}. Введите меньше.")
            return
        await add_op(s, message.from_user, "failed", d_work=-amt, d_bad=amt)
        await s.commit()
        b = await balance(s)
    await state.clear()
    await message.answer(
        f"🗑 ${amt:,} признаны неликвидом.\n\n{_card(b)}",
        reply_markup=safe_kb(message.from_user.id),
    )


# ---------- Сдача в банк: неликвид → нормальные ----------
@router.callback_query(F.data == "sf:bank")
async def bank_start(cb: CallbackQuery, state: FSMContext):
    async with Session() as s:
        b = await balance(s)
    if b["bad"] <= 0:
        await cb.answer("Неликвида в сейфе нет", show_alert=True)
        return
    await state.set_state(SafeOps.bank_amount)
    await cb.answer()
    await cb.message.answer(
        f"Неликвида в сейфе ${b['bad']:,}.\n"
        "На какую сумму сдали в банк?"
    )


@router.message(SafeOps.bank_amount)
async def bank_amount(message: Message, state: FSMContext):
    amt = parse_money(message.text)
    if amt is None:
        await message.answer("Нужна сумма числом.")
        return
    async with Session() as s:
        b = await balance(s)
    if amt > b["bad"]:
        await message.answer(f"Неликвида только ${b['bad']:,}. Введите меньше.")
        return
    await state.update_data(bank_amt=amt)
    await state.set_state(SafeOps.bank_fee)
    await message.answer(
        "Комиссия банка? Введите сумму или <code>0</code>, если без комиссии."
    )


@router.message(SafeOps.bank_fee)
async def bank_save(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(" ", "").replace("$", "")
    fee = 0 if text in ("0", "-") else (parse_money(message.text) or -1)
    if fee < 0:
        await message.answer("Нужна сумма числом или 0.")
        return
    data = await state.get_data()
    amt = data["bank_amt"]
    async with Session() as s:
        b = await balance(s)
        if amt > b["bad"]:
            await state.clear()
            await message.answer("Остаток неликвида изменился, начните заново.")
            return
        # Банк меняет неликвид на нормальные купюры той же суммой,
        # комиссия учитывается отдельной цифрой (ложится на кассу).
        await add_op(s, message.from_user, "bank",
                     d_bad=-amt, d_normal=amt, fee=fee)
        await s.commit()
        b = await balance(s)
    await state.clear()
    tail = f"\nКомиссия: ${fee:,}" if fee else ""
    await message.answer(
        f"🏦 Сдано в банк ${amt:,} — вернулись нормальными.{tail}\n\n{_card(b)}",
        reply_markup=safe_kb(message.from_user.id),
    )


# ---------- История ----------
@router.callback_query(F.data == "sf:log")
async def safe_log(cb: CallbackQuery):
    async with Session() as s:
        rows = (await s.execute(
            select(SafeOp).order_by(SafeOp.id.desc()).limit(15)
        )).scalars().all()
    await cb.answer()
    if not rows:
        await cb.message.answer("Операций пока нет.")
        return
    out = ["📜 <b>Последние операции</b>\n"]
    for r in rows:
        parts = []
        if r.d_normal:
            parts.append(f"💵 {r.d_normal:+,}")
        if r.d_work:
            parts.append(f"🔧 {r.d_work:+,}")
        if r.d_bad:
            parts.append(f"🗑 {r.d_bad:+,}")
        if r.fee:
            parts.append(f"комиссия ${r.fee:,}")
        note = f" · {html.escape(r.note)}" if r.note else ""
        out.append(
            f"{r.op_date.strftime('%d.%m')} {KIND_TITLE.get(r.kind, r.kind)}"
            f"{note}\n    {' · '.join(parts) or '—'}"
        )
    await cb.message.answer("\n".join(out))
