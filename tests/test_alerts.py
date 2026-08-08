"""알림 채널 — 어디로 보내는가, 그리고 알림이 무엇을 실을 수 있는가.

⚠️ **2026-08-07에 `[샀다/안 샀다]` 응답 경로를 걷어냈다.** 버튼은 신호 1건짜리
알림에만 붙일 수 있었는데 실제 신호가 하루 3~6건이라 거의 안 붙었고, 무엇보다
**응답을 빠짐없이 해야만 성립하는 비교**였다 — 산 것만 답하고 무시한 것은 넘기면
acted/ignored가 자기가 고른 분할이 되어 결론이 아첨하는 쪽으로 기운다.
`tests/test_ack.py`에 있던 콜백·오버라이드 테스트는 그때 함께 사라졌고, 여기 남은
것은 **버튼과 무관하게 계속 지켜야 하는 둘**이다.

  1. **채널은 설정에서도 결정된다** — 환경변수만 보면 알림이 조용히 안 나간다
  2. **기록된 신호의 id가 알림에 실린다** — `stockscan explain <id>`를 실을 수 있어야 한다
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts import TelegramChannel, bold, code, default_channel, esc, strip_tags
from app.engine.signals import SignalDraft
from app.storage import history
from app.storage.models import Base

NOW = datetime(2026, 8, 4, 6, 30, tzinfo=UTC)


# ------------------------------------------------------------------ 채널 결정
def test_the_channel_comes_from_the_config_not_only_the_env(monkeypatch):
    """★ 실제로 밟은 사고다 (2026-08-06).

    토큰은 `config.yml`의 `telegram:`에 사는데 `default_channel()`이 환경변수만
    보고 있었다. 그래서 서버(`.env` 없음)에서 **알림이 영영 안 나갔는데**,
    `describe`는 설정을 읽어 "알림 telegram"이라고 표시했다 — **화면과 실제가
    어긋난 채 조용히 도는** 것이 정확히 이 프로젝트가 막으려는 실패다.
    """
    from app.config import AppConfig
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)

    config = AppConfig.model_validate(
        {
            "strategy": "demo_momentum",
            "universe": {"nasdaq": 1},
            "telegram": {"token": "123:ABC", "chat_id": "42"},
        }
    )

    assert default_channel(config).id == "telegram"
    assert default_channel(None).id == "log"  # 환경변수만 보면 못 찾는다


def test_placeholder_tokens_are_not_treated_as_real(monkeypatch):
    """`<봇 토큰>`을 그대로 두고 돌리는 일이 흔하다. 진짜로 취급하면 매 전송이
    실패하면서 원인이 "토큰이 틀렸다"로 보인다."""
    from app.config import AppConfig
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)

    config = AppConfig.model_validate(
        {
            "strategy": "demo_momentum",
            "universe": {"nasdaq": 1},
            "telegram": {"token": "<봇 토큰>", "chat_id": "<채팅 ID>"},
        }
    )
    assert default_channel(config).id == "log"


# ------------------------------------------------------------------ 서식
def test_text_from_outside_cannot_break_the_markup():
    """★ 서식을 켜면 **바깥에서 온 문자열이 메시지를 통째로 깰 수 있다.**

    종목명은 소스가 주는 값이라 `&`가 들어온다(`AT&T`·`S&P Global`). 이스케이프가
    빠지면 텔레그램이 메시지 전체를 거부하고, 그날 알림이 통째로 사라진다.
    """
    assert esc("AT&T") == "AT&amp;T"
    assert bold("<script>") == "<b>&lt;script&gt;</b>"
    assert code("a > b") == "<code>a &gt; b</code>"


def test_stripping_tags_gives_back_the_original_text():
    """평문 되돌림은 **읽을 수 있어야** 한다 — `&amp;`가 남으면 되돌린 의미가 없다."""
    assert strip_tags("🇰🇷 <b>AT&amp;T</b> (nasdaq:T)") == "🇰🇷 AT&T (nasdaq:T)"


@pytest.mark.asyncio
async def test_a_rejected_format_falls_back_to_plain_text(monkeypatch):
    """★★ **서식 때문에 알림이 사라지지 않는다.**

    태그가 하나만 어긋나도 텔레그램은 메시지 **전체**를 거부한다. 서식은 읽기
    편하자고 붙인 것이지 내용이 아니므로, 걷어내고 다시 보낸다 — 못생긴 알림이
    없는 알림보다 낫다. 없는 알림은 "신호가 없었다"와 구분되지 않는다.
    """
    calls: list[dict] = []

    def fake_call(self, method, payload):
        calls.append(payload)
        if payload.get("parse_mode"):
            raise ValueError("텔레그램이 거부했습니다: can't parse entities")
        return {"ok": True}

    monkeypatch.setattr(TelegramChannel, "_call", fake_call)
    channel = TelegramChannel(token="123:ABC", chat_id="42")

    delivery = await channel.send("📈 <b>삼성전자</b>")

    assert delivery.ok is True
    assert [c.get("parse_mode") for c in calls] == ["HTML", None]
    assert calls[1]["text"] == "📈 삼성전자"
    # 되돌렸다는 사실은 남는다 — 조용히 서식이 사라지면 원인을 못 찾는다.
    assert "can't parse entities" in delivery.error


@pytest.mark.asyncio
async def test_a_dead_network_is_not_retried_as_plain_text(monkeypatch):
    """서식과 무관한 실패까지 두 번 두드리지 않는다. 네트워크는 그대로 실패다."""
    import urllib.error

    calls: list[dict] = []

    def fake_call(self, method, payload):
        calls.append(payload)
        raise urllib.error.URLError("연결 실패")

    monkeypatch.setattr(TelegramChannel, "_call", fake_call)
    channel = TelegramChannel(token="123:ABC", chat_id="42")

    delivery = await channel.send("📈 <b>삼성전자</b>")

    assert delivery.ok is False
    assert len(calls) == 1


# --------------------------------------------------------------- 신호 id 왕복
@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def draft(instrument: str = "krx:005930") -> SignalDraft:
    return SignalDraft(
        run_id="run_t",
        pipeline_id="pipe_t",
        node_id="persist",
        instrument=instrument,
        venue="krx",
        timeframe="1d",
        as_of=NOW,
    )


@pytest.mark.asyncio
async def test_the_sink_hands_back_the_row_id(maker):
    """★ id가 없으면 알림에 `stockscan explain <id>`를 실을 수 없다.

    종목명과 등수만 오면 받고 나서 할 수 있는 다음 행동이 없다 — 되짚을 수단이
    같이 와야 한다 (12.5).
    """
    sink = history.SqlSignalSink(maker)

    assert await sink.emit(draft("krx:005930")) is True
    assert await sink.emit(draft("krx:000660")) is True

    assert len(sink.ids) == 2
    assert sink.ids == sorted(sink.ids)  # drafts와 같은 순서
    assert all(isinstance(i, int) for i in sink.ids)


@pytest.mark.asyncio
async def test_duplicates_do_not_leave_a_dangling_id(maker):
    """중복은 행을 만들지 않는다. id가 하나 더 붙으면 알림이 남의 신호를 가리킨다."""
    sink = history.SqlSignalSink(maker)
    await sink.emit(draft())

    assert await sink.emit(draft()) is False  # 같은 dedup_key
    assert len(sink.ids) == 1
    assert len(sink.drafts) == 1
