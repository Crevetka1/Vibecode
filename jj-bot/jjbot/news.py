"""Красные новости с ForexFactory (официальный JSON-фид календаря на неделю)."""

from __future__ import annotations

from datetime import date, datetime, time

import httpx

from .config import NY
from .models import NewsEvent

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


async def fetch_week(client: httpx.AsyncClient) -> list[NewsEvent]:
    resp = await client.get(FF_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; jjbot/1.0)"}, timeout=20)
    resp.raise_for_status()
    return parse_ff(resp.json())


def parse_ff(items: list[dict]) -> list[NewsEvent]:
    events = []
    for it in items:
        raw = it.get("date")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(raw).astimezone(NY)
        except ValueError:
            continue
        events.append(
            NewsEvent(when, it.get("title", "").strip(), it.get("country", "").upper(), it.get("impact", ""))
        )
    return events


def red_news_for_day(
    events: list[NewsEvent], day: date, currency: str, start: time, end: time
) -> list[NewsEvent]:
    """Красные новости нужной валюты за день в окне [start, end] по NY."""
    out = [
        e
        for e in events
        if e.impact == "High"
        and e.currency == currency
        and e.time.date() == day
        and start <= e.time.time() <= end
    ]
    return sorted(out, key=lambda e: e.time)
