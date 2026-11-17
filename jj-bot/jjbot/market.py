"""Минутные свечи NAS100.

По умолчанию берём фьючерс Nasdaq-100 (NQ=F) с Yahoo Finance: он торгуется почти
круглосуточно, поэтому есть цена и в момент новостей 8:30 NY (до открытия).
Можно указать SYMBOL=^NDX (сам индекс, только с 9:30).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from .config import NY
from .models import Candle

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; jjbot/1.0)"}


async def fetch_candles_1m(client: httpx.AsyncClient, symbol: str) -> list[Candle]:
    resp = await client.get(
        YAHOO_URL.format(symbol=symbol),
        params={"interval": "1m", "range": "1d", "includePrePost": "true"},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return parse_yahoo_chart(resp.json())


def parse_yahoo_chart(payload: dict) -> list[Candle]:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    data = result[0]
    stamps = data.get("timestamp") or []
    quote = ((data.get("indicators") or {}).get("quote") or [{}])[0]
    candles = []
    for i, ts in enumerate(stamps):
        o, h, l, c = (quote.get(k, [None] * len(stamps))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        candles.append(Candle(datetime.fromtimestamp(ts, NY), float(o), float(h), float(l), float(c)))
    return candles


def closed_only(candles: list[Candle], now: datetime) -> list[Candle]:
    """Отбрасываем ещё не закрытую текущую минуту."""
    return [c for c in candles if c.start + timedelta(minutes=1) <= now]


def resample(candles: list[Candle], minutes: int, now: datetime) -> list[Candle]:
    """Собираем N-минутные свечи из минутных; возвращаем только закрытые."""
    buckets: dict[datetime, list[Candle]] = {}
    for c in candles:
        minute = c.start.hour * 60 + c.start.minute
        start = c.start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=minute - minute % minutes
        )
        buckets.setdefault(start, []).append(c)
    out = []
    for start in sorted(buckets):
        if start + timedelta(minutes=minutes) > now:
            continue
        group = buckets[start]
        out.append(
            Candle(start, group[0].open, max(x.high for x in group), min(x.low for x in group), group[-1].close)
        )
    return out
