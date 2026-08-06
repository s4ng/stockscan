"""알림 채널 — 바깥과 주고받는 유일한 통로 (ARCHITECTURE.md 8장 / 12.2).

**알림이 나가는 명령은 `serve` 하나뿐이다.** 손으로 돌릴 때마다 메시지가 나가면 알림
자체를 믿지 않게 되고, 그러면 주의력 기계가 무너진다. 그래서 채널을 여는 판단은 노드가
아니라 **실행 엔진과 `serve`**가 한다.

★ **2026-08-06부터 들어오는 방향도 있다.** 알림에 `[샀다] [안 샀다]` 버튼을 붙이고
그 응답으로 `signals.acted`를 채운다. 이전에는 터미널에서 `signals ack <id> --ignored`를
쳐야 했는데 **아무도 치지 않아 오버라이드 데이터가 영영 안 쌓였다** — 4.8이 "이 시스템에서
가장 확실한 가치"라고 부른 비교(무시한 신호가 나았는가)의 절반이 여기에 달려 있다.

⚠️ **토큰은 파이프라인 정의에 넣지 않는다** (규칙 7). 환경변수로 받는다 —
설정 파일을 그대로 복사·공유해도 비밀이 새지 않아야 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT_SECONDS = 15

#: 콜백 데이터 접두사. `ack:<signal_id>:<1|0>` — 텔레그램은 64바이트까지만 받는다.
ACK_PREFIX = "ack"


@dataclass(frozen=True)
class Delivery:
    """보낸 기록. 성공도 실패도 남긴다 — 실패가 조용하면 알림이 끊긴 것을 모른다."""

    at: datetime
    channel: str
    text: str
    ok: bool
    error: str = ""


@dataclass(frozen=True)
class AckResponse:
    """사용자가 버튼으로 답한 것. `signals.acted`에 그대로 들어간다."""

    signal_id: int
    acted: bool
    callback_id: str = ""
    """텔레그램에 "받았다"고 돌려줄 때 쓴다. 안 돌려주면 버튼이 계속 로딩으로 보인다."""


def ack_buttons(signal_ids: list[int]) -> list[dict[str, Any]] | None:
    """신호 하나짜리 알림에만 버튼을 단다.

    ★ **여러 건을 한 메시지로 보낼 때는 버튼을 달지 않는다.** 버튼은 메시지 단위라
    "어느 신호에 대한 답인지"가 정해지지 않는데, 그 상태로 하나를 골라 기록하면
    사용자가 누른 것과 다른 신호에 답이 붙는다. **틀린 데이터가 빈 데이터보다 나쁘다** —
    오버라이드 비교가 통째로 거짓말이 된다.
    """
    if len(signal_ids) != 1:
        return None
    sid = signal_ids[0]
    return [
        [
            {"text": "✅ 샀다", "callback_data": f"{ACK_PREFIX}:{sid}:1"},
            {"text": "⬜ 안 샀다", "callback_data": f"{ACK_PREFIX}:{sid}:0"},
        ]
    ]


def parse_ack(data: str) -> tuple[int, bool] | None:
    """`ack:12:1` → `(12, True)`. 모양이 다르면 None (모르는 콜백은 무시한다)."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != ACK_PREFIX:
        return None
    try:
        return int(parts[1]), parts[2] == "1"
    except ValueError:
        return None


class AlertChannel(Protocol):
    id: str

    async def send(self, text: str, buttons: list[dict[str, Any]] | None = None) -> Delivery: ...

    async def poll_acks(self) -> list[AckResponse]: ...

    async def confirm_ack(self, callback_id: str, text: str) -> None: ...


@dataclass
class LogChannel:
    """아무 데도 보내지 않고 기록만 한다. 토큰이 없을 때의 기본값.

    **"채널이 없다"와 "보냈다"를 구분해서 남긴다** — 미구현을 성공처럼 보이게 하지
    않는 것이 12.3의 규약이다.
    """

    id: str = "log"
    sent: list[Delivery] = field(default_factory=list)

    async def send(self, text: str, buttons: list[dict[str, Any]] | None = None) -> Delivery:
        if buttons:
            text = f"{text}\n[버튼: 샀다 / 안 샀다]"
        delivery = Delivery(datetime.now(UTC), self.id, text, ok=True)
        self.sent.append(delivery)
        return delivery

    async def poll_acks(self) -> list[AckResponse]:
        """받을 곳이 없다. 빈 목록은 "응답 없음"이지 "기능 없음"이 아니다."""
        return []

    async def confirm_ack(self, callback_id: str, text: str) -> None:
        return None


@dataclass
class TelegramChannel:
    """텔레그램 봇 API. 메시지 하나에 HTTP 한 번이라 동기 호출을 스레드로 감싼다.

    의존성을 늘리지 않으려고 `urllib`을 쓴다 — 알림은 하루 몇 건이고, 여기에
    HTTP 클라이언트를 하나 더 들일 이유가 없다.
    """

    token: str
    chat_id: str
    id: str = "telegram"
    sent: list[Delivery] = field(default_factory=list)
    offset: int = 0
    """다음에 받을 update_id. 텔레그램은 이 값보다 낮은 것을 서버에서 지운다.

    프로세스가 죽으면 0으로 돌아가지만, 확인하지 못한 업데이트만 다시 오므로
    그게 맞는 동작이다. 같은 응답을 두 번 써도 `acted`는 같은 값이 된다.
    """

    async def send(self, text: str, buttons: list[dict[str, Any]] | None = None) -> Delivery:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        try:
            await asyncio.to_thread(self._call, "sendMessage", payload)
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=True)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # ★ 알림 실패로 실행을 실패시키지 않는다. 신호는 이미 기록됐고,
            #   여기서 터뜨리면 되돌릴 수 없는 것(봉 소비)이 이미 끝난 뒤다.
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=False, error=str(exc))
        self.sent.append(delivery)
        return delivery

    async def poll_acks(self) -> list[AckResponse]:
        """버튼 응답을 걷어 온다. **긴 폴링을 쓰지 않는다**(`timeout=0`).

        스케줄 루프가 어차피 분마다 깨어나므로, 여기서 커넥션을 붙들고 있으면
        루프의 다른 판단(발화·하트비트)이 그만큼 늦어진다.
        """
        params = {"offset": self.offset, "timeout": 0, "allowed_updates": '["callback_query"]'}
        try:
            body = await asyncio.to_thread(self._call, "getUpdates", params)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # 응답을 못 걷는 것으로 루프를 죽이지 않는다. 다음 tick에 다시 시도한다.
            log.warning("텔레그램 응답 조회 실패: %s", exc)
            return []

        out: list[AckResponse] = []
        for update in body.get("result", []):
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            callback = update.get("callback_query")
            if not callback:
                continue
            parsed = parse_ack(str(callback.get("data", "")))
            if parsed is None:
                continue
            signal_id, acted = parsed
            out.append(AckResponse(signal_id, acted, str(callback.get("id", ""))))
        return out

    async def confirm_ack(self, callback_id: str, text: str) -> None:
        """버튼의 로딩 표시를 끄고 결과를 알린다. 실패해도 기록은 이미 끝났다."""
        if not callback_id:
            return
        try:
            await asyncio.to_thread(
                self._call, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("텔레그램 콜백 확인 실패: %s", exc)

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = urllib.parse.urlencode(payload).encode()
        request = urllib.request.Request(  # noqa: S310 - 스킴이 고정된 상수 URL이다
            f"{TELEGRAM_API}/bot{self.token}/{method}", data=data
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = json.loads(response.read().decode())
        if not body.get("ok"):
            raise ValueError(f"텔레그램이 거부했습니다: {body.get('description')}")
        return body


def default_channel() -> AlertChannel:
    """설정에 토큰이 있으면 텔레그램, 없으면 기록만 하는 채널.

    **토큰이 없다고 실패시키지 않는다.** 알림 없이 도는 `serve`도 유효한 사용이고,
    시작할 때 "기록만 한다"고 경고가 나간다.
    """
    settings = get_settings()
    token = (settings.telegram_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if token and chat_id:
        return TelegramChannel(token=token, chat_id=chat_id)
    return LogChannel()
