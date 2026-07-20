"""Модели данных и подключение к БД (внешний PostgreSQL / SQLite локально)."""
import datetime as dt
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, func, select, text,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import DATABASE_URL, DEFAULT_POINTS
from seed_data import POINTS as SEED_POINTS

# Параметры строки подключения в стиле libpq, которые asyncpg не понимает
# и на которых падает (Neon отдаёт их в своей строке по умолчанию).
_LIBPQ_ONLY = {"sslmode", "channel_binding", "options", "target_session_attrs"}


def _normalize(url: str) -> str:
    """Приводим строку к виду, который понимает asyncpg.

    Render отдаёт postgres://..., Neon — postgresql://...?sslmode=require.
    Драйверу нужен префикс postgresql+asyncpg:// и никаких libpq-параметров.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parts = urlsplit(url)
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY]
        url = urlunsplit(parts._replace(query=urlencode(kept)))
    return url


def _sslmode(url: str) -> str | None:
    """Достаём sslmode из исходной строки до того, как его вырежут."""
    query = urlsplit(url).query
    return dict(parse_qsl(query)).get("sslmode") if query else None


def _connect_args(original_url: str, normalized_url: str) -> dict:
    """Внешним Postgres (Neon и т.п.) нужен SSL — asyncpg включает его явно."""
    if not normalized_url.startswith("postgresql+asyncpg://"):
        return {}
    if _sslmode(original_url) == "disable":
        return {}
    return {"ssl": True}


_DB_URL = _normalize(DATABASE_URL)
engine = create_async_engine(
    _DB_URL, pool_pre_ping=True, connect_args=_connect_args(DATABASE_URL, _DB_URL)
)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Point(Base):
    """Обменный пункт = касса. В названии есть номер, т.к. адреса повторяются."""
    __tablename__ = "points"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Cashier(Base):
    """Кассир, закреплённый за конкретной кассой."""
    __tablename__ = "cashiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Receipt(Base):
    """Одна строка приёмки = один номинал одной валюты от одного кассира.

    qty_total  — принято всего купюр (изношенных свезли на приём)
    qty_normal — из них оказались в нормальном (годном) состоянии
    qty_work   — сколько купюр работник взял в работу
    """
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(40), index=True)     # группирует строки одной приёмки
    report_date: Mapped[dt.date] = mapped_column(Date, index=True)      # дата, за которую считается инкассация
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    worker_id: Mapped[int] = mapped_column(BigInteger)
    worker_name: Mapped[str] = mapped_column(String(120))
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"))
    point_name: Mapped[str] = mapped_column(String(120))               # денормализовано — отчёт не сломается при удалении точки
    cashier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cashier_name: Mapped[str] = mapped_column(String(120), default="—")
    currency: Mapped[str] = mapped_column(String(10))
    denomination: Mapped[int] = mapped_column(Integer)
    qty_total: Mapped[int] = mapped_column(Integer)
    qty_normal: Mapped[int] = mapped_column(Integer)
    qty_work: Mapped[int] = mapped_column(Integer)


# Колонки, добавленные после первого релиза. create_all() умеет создавать
# новые таблицы, но не дописывает колонки в уже существующие — делаем сами.
_ADDED_COLUMNS = {
    "receipts": [
        ("cashier_id", "INTEGER"),
        ("cashier_name", "VARCHAR(120)"),
    ],
}


async def _migrate(conn) -> None:
    dialect = conn.dialect.name
    for table, columns in _ADDED_COLUMNS.items():
        for column, coltype in columns:
            if dialect == "postgresql":
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}"
                ))
            elif dialect == "sqlite":
                rows = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {r[1] for r in rows.fetchall()}
                if column not in existing:
                    await conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"
                    ))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)


async def seed_points(session) -> None:
    """Добавить недостающие кассы и кассиров из seed_data.

    Идемпотентно: существующие записи не трогаются, скрытые точки не возвращаются.
    """
    existing_points = {
        p.name: p for p in (await session.execute(select(Point))).scalars().all()
    }
    changed = False

    for item in SEED_POINTS:
        point = existing_points.get(item["name"])
        if point is None:
            point = Point(name=item["name"], active=True)
            session.add(point)
            await session.flush()          # нужен id для кассиров
            existing_points[point.name] = point
            changed = True

        known = {
            c.name for c in (await session.execute(
                select(Cashier).where(Cashier.point_id == point.id)
            )).scalars().all()
        }
        for cashier_name in item.get("cashiers", []):
            if cashier_name not in known:
                session.add(Cashier(point_id=point.id, name=cashier_name, active=True))
                changed = True

    # Совместимость: точки из переменной DEFAULT_POINTS (без кассиров)
    for name in DEFAULT_POINTS:
        if name not in existing_points:
            session.add(Point(name=name, active=True))
            changed = True

    if changed:
        await session.commit()
