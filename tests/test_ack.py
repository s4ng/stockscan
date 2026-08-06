"""버튼 응답 → `signals.acted` (ARCHITECTURE.md 4.8 오버라이드 추적).

★ **이 경로가 없으면 오버라이드 데이터가 영영 안 쌓인다.** 지금까지는 터미널에서
`signals ack <id> --ignored`를 쳐야 했는데 아무도 치지 않았고, 그래서 "무시한 신호가
나았는가"라는 이 시스템에서 가장 값진 질문에 답할 자료가 비어 있었다.

여기서 지키는 것은 넷이다.

  1. **콜백 데이터를 왕복시켜도 뜻이 변하지 않는다** (`ack:12:1` ↔ `(12, True)`)
  2. ★ **여러 건을 한 메시지로 보낼 때는 버튼을 달지 않는다** — 어느 신호에 대한
     답인지 정해지지 않는데 하나를 골라 기록하면 **틀린 데이터**가 된다
  3. **응답은 발화와 무관하게 매 tick 걷는다** — 버튼을 누르는 시각은 슬롯과 상관없다
  4. **없는 신호에 대한 응답도 조용히 넘기지 않는다** — 사용자는 기록된 줄 안다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts import AckResponse, LogChannel, ack_buttons, parse_ack
from app.engine.signals import SignalDraft
from app.storage import history
from app.storage.models import Base, SignalRow

NOW = datetime(2026, 8, 4, 6, 30, tzinfo=UTC)


# --------------------------------------------------------------------- 콜백 인코딩
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ack:12:1", (12, True)), ("ack:3:0", (3, False)), ("ack:999:1", (999, True))],
)
def test_callback_data_round_trips(raw: str, expected: tuple[int, bool]):
    assert parse_ack(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "ack:12", "ack:abc:1", "nope:12:1", "ack:12:1:extra", "12:1"]
)
def test_unknown_callback_data_is_ignored_not_guessed(raw: str):
    """모르는 콜백을 추측해서 기록하면 사용자가 누르지 않은 답이 남는다."""
    assert parse_ack(raw) is None


def test_callback_data_fits_telegram_limit():
    """텔레그램 callback_data는 64바이트까지다. 넘으면 버튼이 아예 안 붙는다."""
    buttons = ack_buttons([9_999_999])
    assert buttons is not None
    for row in buttons:
        for button in row:
            assert len(button["callback_data"].encode()) <= 64


# ------------------------------------------------------------------------ 버튼 부착
def test_a_single_signal_gets_buttons():
    buttons = ack_buttons([7])
    assert buttons is not None
    assert [b["callback_data"] for b in buttons[0]] == ["ack:7:1", "ack:7:0"]


@pytest.mark.parametrize("ids", [[], [1, 2], [1, 2, 3]])
def test_multiple_signals_get_no_buttons(ids: list[int]):
    """★ 버튼은 메시지 단위다. 여러 건이면 어느 신호에 대한 답인지 정해지지 않는다.

    그 상태로 하나를 골라 기록하면 **사용자가 누른 것과 다른 신호에 답이 붙는다.**
    틀린 데이터는 빈 데이터보다 나쁘다 — 오버라이드 비교가 통째로 거짓말이 된다.
    """
    assert ack_buttons(ids) is None


# -------------------------------------------------------------------------- DB 반영
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
    """★ id가 없으면 버튼이 어느 신호를 가리키는지 정할 수 없다."""
    sink = history.SqlSignalSink(maker)

    assert await sink.emit(draft("krx:005930")) is True
    assert await sink.emit(draft("krx:000660")) is True

    assert len(sink.ids) == 2
    assert sink.ids == sorted(sink.ids)  # drafts와 같은 순서
    assert all(isinstance(i, int) for i in sink.ids)


@pytest.mark.asyncio
async def test_duplicates_do_not_leave_a_dangling_id(maker):
    """중복은 행을 만들지 않는다. id가 하나 더 붙으면 버튼이 남의 신호를 가리킨다."""
    sink = history.SqlSignalSink(maker)
    await sink.emit(draft())

    assert await sink.emit(draft()) is False  # 같은 dedup_key
    assert len(sink.ids) == 1
    assert len(sink.drafts) == 1


@pytest.mark.asyncio
async def test_acted_is_recorded_and_reversible(maker):
    """잘못 눌러도 반대로 다시 누르면 그만이다 — 봉 소비와 달리 되돌릴 수 있다."""
    sink = history.SqlSignalSink(maker)
    await sink.emit(draft())
    signal_id = sink.ids[0]

    async with maker() as session:
        assert (await history.set_acted(session, signal_id, True)) is not None
    async with maker() as session:
        assert (await session.get(SignalRow, signal_id)).acted is True

    async with maker() as session:
        await history.set_acted(session, signal_id, False)
    async with maker() as session:
        assert (await session.get(SignalRow, signal_id)).acted is False


@pytest.mark.asyncio
async def test_answering_a_missing_signal_is_not_silent(maker):
    """조용히 넘기면 사용자는 기록된 줄 안다."""
    async with maker() as session:
        assert await history.set_acted(session, 12345, True) is None


# ------------------------------------------------------------------- 스케줄러 수거
@dataclass
class SpyChannel(LogChannel):
    """응답을 한 번만 내주고, 확인 호출을 기록하는 채널."""

    pending: list[AckResponse] = field(default_factory=list)
    confirmed: list[tuple[str, str]] = field(default_factory=list)

    async def poll_acks(self) -> list[AckResponse]:
        out, self.pending = self.pending, []
        return out

    async def confirm_ack(self, callback_id: str, text: str) -> None:
        self.confirmed.append((callback_id, text))


@pytest.mark.asyncio
async def test_the_scheduler_writes_acks_and_confirms_them(monkeypatch, maker, tmp_path):
    """응답을 걷어 DB에 쓰고, 텔레그램에 "받았다"를 돌려준다.

    확인을 안 돌려주면 버튼이 계속 로딩 상태로 보여 사용자가 눌렸는지 모른다.
    """
    from app import serve
    from app.storage import db as db_module

    sink = history.SqlSignalSink(maker)
    await sink.emit(draft())
    signal_id = sink.ids[0]

    # 스케줄러는 전역 세션메이커를 쓴다. 테스트 DB로 갈아 끼운다.
    monkeypatch.setattr(db_module, "init_db", _noop)
    monkeypatch.setattr(serve.db, "init_db", _noop)
    monkeypatch.setattr(serve.db, "session_scope", _scope(maker))

    channel = SpyChannel()
    channel.pending = [AckResponse(signal_id, acted=True, callback_id="cb1")]
    scheduler = serve.Scheduler(channel, load_spec=lambda: None)

    written = await scheduler.collect_acks()

    assert written == 1
    async with maker() as session:
        assert (await session.get(SignalRow, signal_id)).acted is True
    assert channel.confirmed == [("cb1", "기록했습니다 — 샀다")]
    assert len(scheduler.state.acks) == 1


@pytest.mark.asyncio
async def test_an_ack_for_an_unknown_signal_tells_the_user(monkeypatch, maker):
    from app import serve
    from app.storage import db as db_module

    monkeypatch.setattr(db_module, "init_db", _noop)
    monkeypatch.setattr(serve.db, "init_db", _noop)
    monkeypatch.setattr(serve.db, "session_scope", _scope(maker))

    channel = SpyChannel()
    channel.pending = [AckResponse(404, acted=False, callback_id="cb2")]
    scheduler = serve.Scheduler(channel, load_spec=lambda: None)

    assert await scheduler.collect_acks() == 0
    assert "찾지 못했습니다" in channel.confirmed[0][1]
    assert scheduler.state.acks == []


async def _noop() -> None:
    return None


def _scope(maker):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        async with maker() as session:
            yield session

    return scope
