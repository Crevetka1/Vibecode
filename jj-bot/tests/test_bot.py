import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import jjbot.bot as botmod
from jjbot.config import NY, load_settings
from tests.test_strategy import build_sweep_day, t


class FakeTgBot:
    def __init__(self):
        self.sent = []
        self.n = 0

    async def send_message(self, chat, text, parse_mode=None, reply_to_message_id=None):
        self.n += 1
        chat = int(chat)
        self.sent.append((chat, text, reply_to_message_id))
        return SimpleNamespace(chat_id=chat, message_id=self.n)


def test_full_session_flow(tmp_path, monkeypatch):
    s = replace(load_settings(), token="x", db_path=tmp_path / "db.sqlite3", channel_id="-1001")
    candles = build_sweep_day()
    clock = {"now": t(9, 30)}

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    async def fake_fetch(client, symbol):
        return candles

    async def fake_news(client):
        return []

    monkeypatch.setattr(botmod, "datetime", FakeDT)
    monkeypatch.setattr(botmod, "fetch_candles_1m", fake_fetch)
    monkeypatch.setattr(botmod, "fetch_week", fake_news)

    jj = botmod.JJBot(s)
    jj.db.add_subscriber(42)
    tg = FakeTgBot()
    ctx = SimpleNamespace(application=SimpleNamespace(bot=tg))

    async def go():
        while clock["now"] <= t(10, 40):
            await jj.tick(ctx)
            clock["now"] += timedelta(minutes=1)

    asyncio.run(go())
    texts = [x[1] for x in tg.sent]
    assert any("Справедливая цена" in x for x in texts)
    signals = [(c, i) for i, (c, txt, r) in enumerate(tg.sent, 1) if "SHORT" in txt]
    assert {c for c, _ in signals[:2]} == {42, -1001}
    replies = [x for x in tg.sent if x[2] is not None]
    assert replies and "Тейк" in replies[0][1]
    # ответ привязан к id сообщения с сигналом в том же чате
    first_sig_ids = {c: i for c, i in signals[:2]}
    assert replies[0][2] == first_sig_ids[replies[0][0]]
    assert "Итоги недели" in jj.week_text()
    print("\n\n".join(texts[:6]))


def test_build_app():
    app = botmod.build_app(replace(load_settings(), token="123:abc"))
    assert len(app.job_queue.jobs()) == 3
