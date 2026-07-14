"""Точка входа. Webhook на Render, polling локально."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import (
    BASE_WEBHOOK_URL, BOT_TOKEN, PORT,
    WEBHOOK_PATH, WEBHOOK_SECRET,
)
from db import Session, init_db, seed_points
from handlers import router

logging.basicConfig(level=logging.INFO)


async def on_startup(bot: Bot) -> None:
    await init_db()
    async with Session() as s:
        await seed_points(s)
    if BASE_WEBHOOK_URL:
        await bot.set_webhook(
            f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logging.info("Webhook установлен: %s%s", BASE_WEBHOOK_URL, WEBHOOK_PATH)


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)
    return dp


def run_webhook() -> None:
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()

    app = web.Application()
    app.router.add_get("/", health)          # health-check + пробуждение сервиса
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET
    ).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)       # свяжет startup/shutdown диспетчера
    web.run_app(app, host="0.0.0.0", port=PORT)


async def run_polling() -> None:
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN")
    if BASE_WEBHOOK_URL:
        run_webhook()
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
