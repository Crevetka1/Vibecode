"""Telegram-бот: команды, опрос рынка во время сессии, рассылка сигналов."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from . import messages as M
from .config import NY, Settings, load_settings
from .market import closed_only, fetch_candles_1m
from .models import NewsEvent, Trade
from .news import fetch_week, red_news_for_day
from .storage import Storage
from .strategy import SessionEngine, compute_fair

log = logging.getLogger("jjbot")

NEWS_REFRESH = timedelta(minutes=60)


class JJBot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = Storage(settings.db_path)
        self.http = httpx.AsyncClient()
        self.week_news: list[NewsEvent] = []
        self.news_loaded_at: datetime | None = None
        self.news_ok = False
        self.engine: SessionEngine | None = None
        self.last_candles: list = []

    # ---------- данные ----------
    async def refresh_news(self, force: bool = False) -> None:
        now = datetime.now(NY)
        if not force and self.news_loaded_at and now - self.news_loaded_at < NEWS_REFRESH:
            return
        try:
            self.week_news = await fetch_week(self.http)
            self.news_ok = True
            self.news_loaded_at = now
        except Exception as exc:  # сеть, лимиты ForexFactory и т.п.
            log.warning("Не удалось загрузить новости ForexFactory: %s", exc)
            self.news_ok = bool(self.week_news)
            # повторим попытку через 5 минут
            self.news_loaded_at = now - NEWS_REFRESH + timedelta(minutes=5)

    def red_news(self, day: date) -> list[NewsEvent]:
        return red_news_for_day(self.week_news, day, self.s.news_currency, self.s.news_from, self.s.session_end)

    async def candles_today(self, now: datetime) -> list:
        candles = await fetch_candles_1m(self.http, self.s.symbol)
        day = now.date()
        self.last_candles = [c for c in closed_only(candles, now) if c.start.date() == day]
        return self.last_candles

    # ---------- рассылка ----------
    def targets(self) -> list[int | str]:
        out: list[int | str] = list(self.db.subscribers())
        if self.s.channel_id:
            out.append(self.s.channel_id)
        return out

    async def broadcast(self, app: Application, text: str) -> list[tuple[int, int]]:
        sent = []
        for chat in self.targets():
            try:
                msg = await app.bot.send_message(chat, text, parse_mode=ParseMode.HTML)
                sent.append((msg.chat_id, msg.message_id))
            except TelegramError as exc:
                log.warning("Не удалось отправить в %s: %s", chat, exc)
        return sent

    async def send_signal(self, app: Application, trade: Trade) -> None:
        self.db.save_trade(trade)
        for chat_id, message_id in await self.broadcast(app, M.signal_message(self.s, trade)):
            self.db.save_message(trade.id, chat_id, message_id)

    async def send_result(self, app: Application, trade: Trade) -> None:
        self.db.close_trade(trade)
        text = M.result_message(self.s, trade)
        for chat_id, message_id in self.db.messages_for(trade.id):
            try:
                await app.bot.send_message(
                    chat_id, text, parse_mode=ParseMode.HTML, reply_to_message_id=message_id
                )
            except TelegramError as exc:
                log.warning("Не удалось ответить в %s: %s", chat_id, exc)

    # ---------- цикл сессии ----------
    def session_window(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, self.s.session_start, tzinfo=NY),
            datetime.combine(day, self.s.session_end, tzinfo=NY) + timedelta(minutes=2),
        )

    async def tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = datetime.now(NY)
        day = now.date()
        if now.weekday() >= 5:
            return
        start, end = self.session_window(day)
        if not (start <= now <= end):
            return

        await self.refresh_news()
        try:
            candles = await self.candles_today(now)
        except Exception as exc:
            log.warning("Не удалось получить котировки %s: %s", self.s.symbol, exc)
            return
        if not candles:
            return

        if self.engine is None or self.engine.day != day:
            self.engine = SessionEngine(
                self.s,
                day,
                self.red_news(day),
                open_trade=self.db.open_trade(day.isoformat()),
                trades_today=self.db.trades_count(day.isoformat()),
            )
            if now - start > timedelta(minutes=self.s.timeframe_min):
                # бот перезапущен посреди сессии - не выдаём сигналы задним числом
                self.engine.skip_history(candles, now)
        else:
            self.engine.red_news = self.red_news(day)

        for ev in self.engine.step(candles, now):
            if ev.kind == "fair" and ev.fair:
                await self.broadcast(context.application, M.fair_message(self.s, ev.fair))
            elif ev.kind == "open" and ev.trade:
                await self.send_signal(context.application, ev.trade)
            elif ev.kind == "close" and ev.trade:
                await self.send_result(context.application, ev.trade)

    # ---------- расписание ----------
    async def pre_session(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.refresh_news(force=True)
        await self.broadcast(context.application, self.status_text())

    async def weekly(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.broadcast(context.application, self.week_text())

    # ---------- тексты ----------
    def status_text(self) -> str:
        now = datetime.now(NY)
        news = self.red_news(now.date())
        fair = None
        if self.engine and self.engine.day == now.date():
            fair = self.engine.fair
        elif self.last_candles:
            fair = compute_fair(self.last_candles, news, now.date(), self.s, now)
        text = M.status_message(self.s, now, news, fair)
        if not self.news_ok:
            text += "\n⚠️ Календарь ForexFactory сейчас недоступен — новости не учтены"
        return text

    def week_text(self) -> str:
        today = datetime.now(NY).date()
        monday = today - timedelta(days=today.weekday())
        friday = monday + timedelta(days=4)
        trades = self.db.trades_between(monday.isoformat(), friday.isoformat())
        return M.weekly_message(self.s, trades, monday.isoformat(), friday.isoformat())

    # ---------- команды ----------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.db.add_subscriber(update.effective_chat.id)
        await self.refresh_news()
        await update.effective_message.reply_text(self.status_text(), parse_mode=ParseMode.HTML)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.refresh_news()
        await update.effective_message.reply_text(self.status_text(), parse_mode=ParseMode.HTML)

    async def cmd_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.refresh_news()
        today = datetime.now(NY).date()
        week = sorted(
            (e for e in self.week_news if e.impact == "High" and e.currency == self.s.news_currency
             and e.time.date() >= today),
            key=lambda e: e.time,
        )
        if not week:
            text = f"Красных новостей {self.s.news_currency} до конца недели нет."
        else:
            days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            text = f"📰 <b>Красные новости {self.s.news_currency}</b>\n" + "\n".join(
                f"{days[e.time.weekday()]} {e.time:%d.%m} {M.hhmm(e.time, NY)} NY "
                f"({M.hhmm(e.time, M.MSK)} МСК) — {M.escape(e.title)}"
                for e in week
            )
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(self.week_text(), parse_mode=ParseMode.HTML)

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.db.remove_subscriber(update.effective_chat.id)
        await update.effective_message.reply_text("Вы отписаны от сигналов. /start — подписаться снова.")


def build_app(settings: Settings) -> Application:
    if not settings.token:
        raise SystemExit("Не задан TELEGRAM_TOKEN (см. .env.example)")
    bot = JJBot(settings)
    app = Application.builder().token(settings.token).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("status", bot.cmd_status))
    app.add_handler(CommandHandler("news", bot.cmd_news))
    app.add_handler(CommandHandler("stats", bot.cmd_stats))
    app.add_handler(CommandHandler("stop", bot.cmd_stop))

    jq = app.job_queue
    jq.run_repeating(bot.tick, interval=settings.poll_seconds, first=5)
    pre = (datetime.combine(date.today(), settings.session_start) - timedelta(minutes=5)).time()
    jq.run_daily(bot.pre_session, time=pre.replace(tzinfo=NY), days=(1, 2, 3, 4, 5))
    jq.run_daily(bot.weekly, time=time(16, 0, tzinfo=NY), days=(5,))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_app(load_settings()).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
