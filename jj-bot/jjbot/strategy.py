"""Логика анализа: справедливая цена, поиск сетапов и сопровождение сделки.

Модуль не знает ничего про Telegram и сеть, поэтому его легко тестировать
и прогонять на истории.

Справедливая цена (fair):
  * красных новостей USD за день нет  -> цена открытия Нью-Йорка (9:30 NY);
  * красная новость уже вышла         -> цена в минуту выхода новости
    (если новостей несколько - берётся последняя вышедшая).

Сетапы (на закрытых свечах TIMEFRAME_MIN, по умолчанию 5 минут):
  1. «Снятие ликвидности»: свеча обновляет максимум последних N свечей,
     но закрывается обратно под ним (медвежья), находясь далеко над fair
     -> SHORT в сторону справедливой цены. Зеркально для LONG.
  2. «Ретест справедливой»: цена была по одну сторону от fair, пробила её,
     вернулась проверить уровень и закрылась в сторону пробоя -> вход
     в сторону пробоя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import NY, Settings
from .market import resample
from .models import Candle, FairPrice, NewsEvent, Trade

SETUP_SWEEP_HIGH = "Снятие ликвидности сверху → возврат к справедливой"
SETUP_SWEEP_LOW = "Снятие ликвидности снизу → возврат к справедливой"
SETUP_RETEST_LONG = "Пробой и ретест справедливой снизу вверх"
SETUP_RETEST_SHORT = "Пробой и ретест справедливой сверху вниз"


def at_ny(day: date, t) -> datetime:
    return datetime.combine(day, t, tzinfo=NY)


def first_candle_from(candles: list[Candle], moment: datetime) -> Candle | None:
    for c in candles:
        if c.start >= moment:
            return c
    return None


def compute_fair(
    candles: list[Candle], red_news: list[NewsEvent], day: date, settings: Settings, now: datetime
) -> FairPrice | None:
    fair: FairPrice | None = None
    open_at = at_ny(day, settings.session_start)
    if now >= open_at:
        c = first_candle_from(candles, open_at)
        if c and c.start - open_at < timedelta(minutes=15):
            fair = FairPrice(c.open, "open", c.start)
    for ev in red_news:
        if ev.time > now:
            break
        c = first_candle_from(candles, ev.time)
        if c and c.start - ev.time < timedelta(minutes=15):
            fair = FairPrice(c.open, "news", ev.time, ev.title)
    return fair


@dataclass(frozen=True)
class Signal:
    side: str
    setup: str


def detect_setup(tf: list[Candle], fair: float, s: Settings) -> Signal | None:
    if len(tf) < 4:
        return None
    c = tf[-1]
    prev = tf[-1 - s.sweep_lookback : -1]
    prev_high = max(p.high for p in prev)
    prev_low = min(p.low for p in prev)
    bearish = c.close < c.open
    bullish = c.close > c.open

    # 1. Снятие ликвидности и возврат к справедливой
    if c.high > prev_high and c.close < prev_high and bearish and c.high - fair >= s.min_deviation and c.close > fair:
        return Signal("SHORT", SETUP_SWEEP_HIGH)
    if c.low < prev_low and c.close > prev_low and bullish and fair - c.low >= s.min_deviation and c.close < fair:
        return Signal("LONG", SETUP_SWEEP_LOW)

    # 2. Пробой и ретест справедливой
    before = tf[-2]
    tol = s.retest_tolerance
    was_below = any(p.close < fair for p in prev[:-1])
    was_above = any(p.close > fair for p in prev[:-1])
    if was_below and before.close > fair and fair - tol <= c.low <= fair + tol and c.close > fair and bullish:
        return Signal("LONG", SETUP_RETEST_LONG)
    if was_above and before.close < fair and fair - tol <= c.high <= fair + tol and c.close < fair and bearish:
        return Signal("SHORT", SETUP_RETEST_SHORT)
    return None


@dataclass
class Event:
    kind: str  # "fair" | "open" | "close"
    fair: FairPrice | None = None
    trade: Trade | None = None


class SessionEngine:
    """Состояние одной торговой сессии (одного дня NY)."""

    def __init__(
        self,
        settings: Settings,
        day: date,
        red_news: list[NewsEvent],
        open_trade: Trade | None = None,
        trades_today: int = 0,
    ):
        self.s = settings
        self.day = day
        self.red_news = red_news
        self.open_trade = open_trade
        self.trades_today = trades_today
        self.fair: FairPrice | None = None
        self.last_processed: datetime | None = None  # начало последней обработанной свечи ТФ
        self.last_checked: datetime | None = None  # последняя минутка, проверенная на стоп/тейк

    @property
    def session_open(self) -> datetime:
        return at_ny(self.day, self.s.session_start)

    @property
    def session_close(self) -> datetime:
        return at_ny(self.day, self.s.session_end)

    def skip_history(self, candles: list[Candle], now: datetime) -> None:
        """После перезапуска не выдаём сигналы по уже прошедшим свечам."""
        tf = resample(candles, self.s.timeframe_min, now)
        if tf:
            self.last_processed = tf[-1].start
        self.fair = compute_fair(candles, self.red_news, self.day, self.s, now)

    def in_news_blackout(self, moment: datetime) -> bool:
        gap = timedelta(minutes=self.s.news_blackout_min)
        return any(abs(moment - ev.time) <= gap for ev in self.red_news)

    def step(self, candles: list[Candle], now: datetime) -> list[Event]:
        """candles - только закрытые минутные свечи за день."""
        events: list[Event] = []

        fair = compute_fair(candles, self.red_news, self.day, self.s, now)
        if fair and fair != self.fair:
            events.append(Event("fair", fair=fair))
        self.fair = fair or self.fair

        if self.open_trade:
            closed = self._track(candles)
            if closed:
                events.append(Event("close", trade=closed))

        if self.open_trade and now >= self.session_close and candles:
            t = self.open_trade
            last = [c for c in candles if c.start < self.session_close] or candles
            t.status, t.exit_price, t.closed_at = "time", last[-1].close, self.session_close
            self.open_trade = None
            events.append(Event("close", trade=t))

        signal_event = self._look_for_signal(candles, now)
        if signal_event:
            events.append(signal_event)
        return events

    def _track(self, candles: list[Candle]) -> Trade | None:
        t = self.open_trade
        assert t is not None
        for c in candles:
            if c.start < t.opened_at or (self.last_checked and c.start <= self.last_checked):
                continue
            self.last_checked = c.start
            hit_stop = c.low <= t.stop if t.is_long else c.high >= t.stop
            hit_take = c.high >= t.take if t.is_long else c.low <= t.take
            if hit_stop or hit_take:
                # Если в одной минуте задеты оба уровня - считаем стоп (консервативно).
                t.status = "stop" if hit_stop else "take"
                t.exit_price = t.stop if hit_stop else t.take
                t.closed_at = c.start + timedelta(minutes=1)
                self.open_trade = None
                return t
        return None

    def _look_for_signal(self, candles: list[Candle], now: datetime) -> Event | None:
        tf = resample(candles, self.s.timeframe_min, now)
        tf = [c for c in tf if c.start >= self.session_open - timedelta(minutes=self.s.timeframe_min * 10)]
        if not tf:
            return None
        latest = tf[-1]
        if self.last_processed and latest.start <= self.last_processed:
            return None
        self.last_processed = latest.start

        candle_end = latest.start + timedelta(minutes=self.s.timeframe_min)
        if latest.start < self.session_open or candle_end > self.session_close:
            return None
        if self.open_trade or self.trades_today >= self.s.max_trades:
            return None
        if not self.fair or self.fair.at > latest.start or self.in_news_blackout(candle_end):
            return None

        sig = detect_setup(tf, self.fair.price, self.s)
        if not sig:
            return None
        entry = latest.close
        sign = 1 if sig.side == "LONG" else -1
        trade = Trade(
            id=None,
            day=self.day.isoformat(),
            side=sig.side,
            setup=sig.setup,
            entry=entry,
            stop=round(entry - sign * self.s.stop_points, 2),
            take=round(entry + sign * self.s.take_points, 2),
            opened_at=candle_end,
            fair=self.fair.price,
        )
        self.open_trade = trade
        self.trades_today += 1
        self.last_checked = None
        return Event("open", trade=trade)
