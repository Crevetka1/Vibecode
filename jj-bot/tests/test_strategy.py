from dataclasses import replace
from datetime import date, datetime, timedelta

from jjbot.config import NY, load_settings
from jjbot.messages import result_message, signal_message, status_message, weekly_message
from jjbot.models import Candle, NewsEvent
from jjbot.news import parse_ff, red_news_for_day
from jjbot.strategy import SETUP_RETEST_LONG, SETUP_SWEEP_HIGH, SessionEngine, compute_fair

DAY = date(2026, 11, 17)
S = replace(load_settings(), token="x")


def t(h, m):
    return datetime(2026, 11, 17, h, m, tzinfo=NY)


def series(start, prices):
    """Минутки по списку (o, h, l, c)."""
    return [Candle(start + timedelta(minutes=i), *p) for i, p in enumerate(prices)]


def flat(start, n, price):
    return series(start, [(price, price + 1, price - 1, price)] * n)


def run(engine, candles, until):
    events = []
    now = candles[0].start
    while now <= until:
        closed = [c for c in candles if c.start + timedelta(minutes=1) <= now]
        events += engine.step(closed, now)
        now += timedelta(minutes=1)
    return events


def test_fair_is_ny_open_without_news():
    c = flat(t(9, 0), 60, 100.0)
    c[30] = Candle(t(9, 30), 123.5, 124, 123, 123.7)
    fair = compute_fair(c, [], DAY, S, t(9, 45))
    assert fair.price == 123.5 and fair.source == "open"
    assert compute_fair(c, [], DAY, S, t(9, 20)) is None


def test_fair_switches_to_news_price():
    c = flat(t(8, 0), 180, 100.0)
    c[120] = Candle(t(10, 0), 150.25, 151, 150, 150.5)
    news = [NewsEvent(t(10, 0), "ISM Services PMI", "USD", "High")]
    assert compute_fair(c, news, DAY, S, t(9, 50)).source == "open"
    fair = compute_fair(c, news, DAY, S, t(10, 5))
    assert fair.price == 150.25 and fair.source == "news" and fair.title == "ISM Services PMI"


def test_premarket_news_8_30_sets_fair():
    c = flat(t(8, 0), 120, 100.0)
    c[30] = Candle(t(8, 30), 90.0, 91, 89, 90)
    news = [NewsEvent(t(8, 30), "CPI m/m", "USD", "High")]
    assert compute_fair(c, news, DAY, S, t(9, 40)).price == 90.0


def build_sweep_day():
    # 9:30 открытие 1000, рост до 1035, затем вынос максимума и медвежья 5м свеча, потом падение
    c = flat(t(9, 0), 30, 1000.0)
    price = 1000.0
    for i in range(30):  # 9:30-9:59 плавный рост до ~1030
        o, price = price, price + 1
        c.append(Candle(t(9, 30) + timedelta(minutes=i), o, price + 0.5, o - 0.5, price))
    # 10:00-10:04: вынос до 1040 и закрытие 1026 (ниже прежнего хая)
    c += series(t(10, 0), [(1030, 1040, 1029, 1036), (1036, 1037, 1030, 1032), (1032, 1033, 1028, 1029),
                           (1029, 1030, 1026, 1027), (1027, 1028, 1025, 1026)])
    # падение к тейку (1026-38=988)
    price = 1026.0
    for i in range(30):
        o, price = price, price - 2
        c.append(Candle(t(10, 5) + timedelta(minutes=i), o, o + 0.5, price - 0.5, price))
    return c


def test_sweep_short_hits_take_and_replies():
    candles = build_sweep_day()
    eng = SessionEngine(S, DAY, [])
    events = run(eng, candles, t(10, 40))
    kinds = [e.kind for e in events]
    assert kinds[0] == "fair"
    opened = [e.trade for e in events if e.kind == "open"]
    tr = opened[0]
    assert tr.side == "SHORT" and tr.setup == SETUP_SWEEP_HIGH
    assert tr.entry == 1026 and tr.stop == 1051 and tr.take == 988
    closed = [e.trade for e in events if e.kind == "close"]
    assert closed and closed[0].status == "take" and closed[0].pnl_points() == 38
    assert "Тейк" in result_message(S, closed[0])
    assert "SHORT" in signal_message(S, tr)


def test_stop_and_time_close():
    candles = build_sweep_day()[:65]  # обрезаем после сигнала
    # после входа цена идёт вверх до стопа
    candles += series(t(10, 5), [(1026, 1052, 1025, 1050)])
    eng = SessionEngine(S, DAY, [])
    events = run(eng, candles, t(10, 10))
    closed = [e.trade for e in events if e.kind == "close"]
    assert closed[0].status == "stop" and closed[0].pnl_points() == -25

    # закрытие по времени
    candles = build_sweep_day()[:65] + flat(t(10, 5), 150, 1020.0)
    eng = SessionEngine(S, DAY, [])
    events = run(eng, candles, t(12, 31))
    closed = [e.trade for e in events if e.kind == "close"]
    assert closed[0].status == "time" and closed[0].pnl_points() == 6


def test_retest_long():
    c = flat(t(9, 0), 30, 1000.0)
    c.append(Candle(t(9, 30), 1000, 1001, 999, 1000))  # fair = 1000
    c += series(t(9, 31), [(1000, 1000, 990, 992)] + [(992, 993, 988, 990)] * 13)  # ниже fair
    c += series(t(9, 45), [(990, 1012, 989, 1010)] * 5)  # 9:45-9:49 пробой вверх, close > fair
    c += series(t(9, 50), [(1010, 1011, 1004, 1006), (1006, 1007, 1003, 1004), (1004, 1006, 1002, 1005),
                           (1005, 1007, 1004, 1006), (1006, 1013, 1005, 1012)])  # ретест 1002, бычья
    c += flat(t(9, 55), 10, 1012.0)
    eng = SessionEngine(S, DAY, [])
    opened = [e.trade for e in run(eng, c, t(10, 0)) if e.kind == "open"]
    assert opened and opened[0].side == "LONG" and opened[0].setup == SETUP_RETEST_LONG


def test_news_blackout_and_max_trades():
    news = [NewsEvent(t(10, 5), "X", "USD", "High")]
    eng = SessionEngine(S, DAY, news)
    events = run(eng, build_sweep_day(), t(10, 40))
    assert not [e for e in events if e.kind == "open"]  # сигнал в 10:05 попал в окно новости
    eng = SessionEngine(replace(S, max_trades=0), DAY, [])
    assert not [e for e in run(eng, build_sweep_day(), t(10, 40)) if e.kind == "open"]


def test_restart_skips_history():
    candles = build_sweep_day()
    eng = SessionEngine(S, DAY, [])
    closed = [c for c in candles if c.start + timedelta(minutes=1) <= t(10, 7)]
    eng.skip_history(closed, t(10, 7))
    assert not [e for e in eng.step(closed, t(10, 7)) if e.kind == "open"]


def test_forexfactory_parsing_and_messages():
    raw = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-11-17T08:30:00-05:00", "impact": "High"},
        {"title": "Retail Sales", "country": "USD", "date": "2026-11-17T10:00:00-05:00", "impact": "Medium"},
        {"title": "ECB", "country": "EUR", "date": "2026-11-17T09:00:00-05:00", "impact": "High"},
        {"title": "FOMC", "country": "USD", "date": "2026-11-17T14:00:00-05:00", "impact": "High"},
    ]
    red = red_news_for_day(parse_ff(raw), DAY, "USD", S.news_from, S.session_end)
    assert [e.title for e in red] == ["CPI m/m"]
    text = status_message(S, t(11, 57), [], None)
    assert "красных новостей USD до 12:30 NY нет" in text and "17:30–20:30 МСК" in text
    assert "Сделок не было" in weekly_message(S, [], "2026-11-16", "2026-11-20")
