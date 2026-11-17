"""Тексты сообщений (HTML-разметка Telegram)."""

from __future__ import annotations

from datetime import datetime
from html import escape

from .config import MSK, NY, Settings
from .models import FairPrice, NewsEvent, Trade

LINE = "—" * 16


def fmt_price(x: float) -> str:
    return f"{x:.2f}"


def fmt_pts(x: float) -> str:
    return f"{x:+.1f}".replace(".0", "") + " п"


def fmt_usd(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(x):.2f}"


def hhmm(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def pnl_usd(points: float, s: Settings) -> float:
    """Риск фиксирован в $: стоп = −RISK_USD, остальное пропорционально."""
    return points / s.stop_points * s.risk_usd


def status_message(
    s: Settings, now: datetime, red_news: list[NewsEvent], fair: FairPrice | None
) -> str:
    end = s.session_end.strftime("%H:%M")
    start = s.session_start.strftime("%H:%M")
    start_msk = hhmm(datetime.combine(now.astimezone(NY).date(), s.session_start, tzinfo=NY), MSK)
    end_msk = hhmm(datetime.combine(now.astimezone(NY).date(), s.session_end, tzinfo=NY), MSK)
    lines = [
        f"🤖 <b>JJ бот запущен</b> · {escape(s.instrument_name)}",
        LINE,
        f"🕒 Время NY: <b>{hhmm(now, NY)}</b> (МСК {hhmm(now, MSK)})",
    ]
    if red_news:
        lines.append(f"📰 Красные новости {s.news_currency} сегодня:")
        for ev in red_news:
            lines.append(f"   • {hhmm(ev.time, NY)} NY ({hhmm(ev.time, MSK)} МСК) — {escape(ev.title)}")
    else:
        lines.append(f"📰 Сегодня красных новостей {s.news_currency} до {end} NY нет")
    if fair:
        lines.append(f"📍 Справедливая: <code>{fmt_price(fair.price)}</code> ({escape(fair.describe())})")
    elif red_news:
        lines.append("📍 Справедливая: будет взята по цене на выходе новости")
    else:
        lines.append(f"📍 Справедливая: будет взята по цене открытия {start} NY")
    lines += [
        f"⚙️ Стоп/тейк: {s.stop_points:g}/{s.take_points:g} п · сигналы {start}–{end} NY "
        f"({start_msk}–{end_msk} МСК) · риск ${s.risk_usd:g}",
        "📅 Итоги недели: пятница, 16:00 NY",
    ]
    return "\n".join(lines)


def fair_message(s: Settings, fair: FairPrice) -> str:
    return (
        f"📍 <b>Справедливая цена</b> · {escape(s.instrument_name)}\n"
        f"<code>{fmt_price(fair.price)}</code> — {escape(fair.describe())}"
    )


def signal_message(s: Settings, t: Trade) -> str:
    icon = "🟢" if t.is_long else "🔴"
    return "\n".join(
        [
            f"{icon} <b>{t.side}</b> · {escape(s.instrument_name)}",
            LINE,
            f"🧩 Сетап: {escape(t.setup)}",
            f"➡️ Вход: <code>{fmt_price(t.entry)}</code>",
            f"🎯 Тейк: <code>{fmt_price(t.take)}</code> (+{s.take_points:g} п)",
            f"🛑 Стоп: <code>{fmt_price(t.stop)}</code> (−{s.stop_points:g} п)",
            f"📍 Справедливая: <code>{fmt_price(t.fair)}</code>",
            f"🕒 {hhmm(t.opened_at, NY)} NY ({hhmm(t.opened_at, MSK)} МСК) · риск ${s.risk_usd:g}",
        ]
    )


def result_message(s: Settings, t: Trade) -> str:
    pts = t.pnl_points()
    usd = pnl_usd(pts, s)
    when = f"{hhmm(t.closed_at, NY)} NY ({hhmm(t.closed_at, MSK)} МСК)" if t.closed_at else ""
    if t.status == "take":
        head = "✅ <b>Тейк</b>"
    elif t.status == "stop":
        head = "❌ <b>Стоп</b>"
    else:
        head = "⏹ <b>Закрыта по окончании сессии</b>"
    return f"{head} · {fmt_pts(pts)} · {fmt_usd(usd)}\nВыход: <code>{fmt_price(t.exit_price or 0)}</code> · {when}"


def weekly_message(s: Settings, trades: list[Trade], first_day: str, last_day: str) -> str:
    closed = [t for t in trades if t.status != "open"]
    if not closed:
        return f"📅 <b>Итоги недели</b> {first_day} — {last_day}\nСделок не было."
    takes = sum(t.status == "take" for t in closed)
    stops = sum(t.status == "stop" for t in closed)
    timed = sum(t.status == "time" for t in closed)
    pts = sum(t.pnl_points() for t in closed)
    usd = sum(pnl_usd(t.pnl_points(), s) for t in closed)
    winrate = 100 * sum(t.pnl_points() > 0 for t in closed) / len(closed)
    lines = [
        f"📅 <b>Итоги недели</b> {first_day} — {last_day} · {escape(s.instrument_name)}",
        LINE,
        f"Сделок: {len(closed)} · ✅ {takes} · ❌ {stops} · ⏹ {timed}",
        f"Винрейт: {winrate:.0f}%",
        f"Результат: <b>{fmt_pts(pts)}</b> · <b>{fmt_usd(usd)}</b>",
        "",
    ]
    for t in closed:
        icon = {"take": "✅", "stop": "❌"}.get(t.status, "⏹")
        lines.append(f"{icon} {t.day} {hhmm(t.opened_at, NY)} {t.side} · {fmt_pts(t.pnl_points())}")
    return "\n".join(lines)
