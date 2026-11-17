from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    start: datetime  # начало свечи, tz-aware (NY)
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class NewsEvent:
    time: datetime  # tz-aware (NY)
    title: str
    currency: str
    impact: str  # "High" = красная новость


@dataclass(frozen=True)
class FairPrice:
    price: float
    source: str  # "open" или "news"
    at: datetime
    title: str | None = None  # название новости, если source == "news"

    def describe(self) -> str:
        hhmm = self.at.strftime("%H:%M")
        if self.source == "news":
            return f"цена на выходе новости «{self.title}» в {hhmm} NY"
        return f"цена открытия NY {hhmm}"


@dataclass
class Trade:
    id: int | None
    day: str  # YYYY-MM-DD (NY)
    side: str  # "LONG" / "SHORT"
    setup: str
    entry: float
    stop: float
    take: float
    opened_at: datetime
    fair: float
    status: str = "open"  # open / take / stop / time
    closed_at: datetime | None = None
    exit_price: float | None = None

    @property
    def is_long(self) -> bool:
        return self.side == "LONG"

    def pnl_points(self) -> float:
        if self.exit_price is None:
            return 0.0
        diff = self.exit_price - self.entry
        return diff if self.is_long else -diff
