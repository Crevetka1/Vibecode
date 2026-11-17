"""Настройки бота. Всё читается из переменных окружения (или файла .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MSK = ZoneInfo("Europe/Moscow")


def _load_dotenv(path: Path) -> None:
    """Простейший загрузчик .env без внешних зависимостей."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    token: str
    channel_id: str | None
    symbol: str
    instrument_name: str
    # Сессия задаётся по времени Нью-Йорка: 9:30–12:30 NY = 17:30–20:30 МСК зимой
    # (летом, когда в США летнее время, это 16:30–19:30 МСК).
    session_start: time
    session_end: time
    stop_points: float
    take_points: float
    risk_usd: float
    timeframe_min: int
    min_deviation: float
    retest_tolerance: float
    sweep_lookback: int
    max_trades: int
    news_blackout_min: int
    news_from: time
    news_currency: str
    db_path: Path
    poll_seconds: int
    admin_ids: frozenset[int] = field(default_factory=frozenset)


def load_settings(env_file: Path | None = None) -> Settings:
    _load_dotenv(env_file or Path(__file__).resolve().parent.parent / ".env")
    admins = os.environ.get("ADMIN_IDS", "")
    return Settings(
        token=os.environ.get("TELEGRAM_TOKEN", ""),
        channel_id=os.environ.get("CHANNEL_ID") or None,
        symbol=os.environ.get("SYMBOL", "NQ=F"),
        instrument_name=os.environ.get("INSTRUMENT_NAME", "NAS100"),
        session_start=_time(os.environ.get("SESSION_START_NY", "09:30")),
        session_end=_time(os.environ.get("SESSION_END_NY", "12:30")),
        stop_points=_float("STOP_POINTS", 25),
        take_points=_float("TAKE_POINTS", 38),
        risk_usd=_float("RISK_USD", 15),
        timeframe_min=_int("TIMEFRAME_MIN", 5),
        min_deviation=_float("MIN_DEVIATION", 30),
        retest_tolerance=_float("RETEST_TOLERANCE", 8),
        sweep_lookback=_int("SWEEP_LOOKBACK", 6),
        max_trades=_int("MAX_TRADES_PER_SESSION", 3),
        news_blackout_min=_int("NEWS_BLACKOUT_MIN", 5),
        news_from=_time(os.environ.get("NEWS_FROM_NY", "08:00")),
        news_currency=os.environ.get("NEWS_CURRENCY", "USD"),
        db_path=Path(os.environ.get("DB_PATH", Path(__file__).resolve().parent.parent / "jjbot.sqlite3")),
        poll_seconds=_int("POLL_SECONDS", 20),
        admin_ids=frozenset(int(x) for x in admins.replace(" ", "").split(",") if x),
    )
