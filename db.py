"""Модели данных и подключение к БД (PostgreSQL на Render, SQLite локально)."""
import datetime as dt

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, func, select,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import DATABASE_URL, DEFAULT_POINTS


def _normalize(url: str) -> str:
    """Render выдаёт postgres://..., а asyncpg ждёт postgresql+asyncpg://..."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(_normalize(DATABASE_URL), pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Point(Base):
    """Обменный пункт."""
    __tablename__ = "points"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Receipt(Base):
    """Одна строка приёмки = один номинал одной валюты с одного пункта.

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
    point_name: Mapped[str] = mapped_column(String(120))               # денормализовано — отчёт не сломается при удалении пункта
    currency: Mapped[str] = mapped_column(String(10))
    denomination: Mapped[int] = mapped_column(Integer)
    qty_total: Mapped[int] = mapped_column(Integer)
    qty_normal: Mapped[int] = mapped_column(Integer)
    qty_work: Mapped[int] = mapped_column(Integer)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_points(session) -> None:
    """При первом запуске заполнить список пунктов из DEFAULT_POINTS."""
    if not DEFAULT_POINTS:
        return
    existing = (await session.execute(select(func.count(Point.id)))).scalar_one()
    if existing:
        return
    for name in DEFAULT_POINTS:
        session.add(Point(name=name, active=True))
    await session.commit()
