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

from app.alerts import default_channel
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
