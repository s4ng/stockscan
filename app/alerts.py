"""알림 채널 — 바깥과 주고받는 유일한 통로 (ARCHITECTURE.md 8장 / 12.2).

**알림이 나가는 명령은 `serve` 하나뿐이다.** 손으로 돌릴 때마다 메시지가 나가면 알림
자체를 믿지 않게 되고, 그러면 주의력 기계가 무너진다. 그래서 채널을 여는 판단은 노드가
아니라 **실행 엔진과 `serve`**가 한다.

★ **통로는 나가는 방향뿐이다** (2026-08-07). 한때 `[샀다] [안 샀다]` 버튼으로 응답을
받아 `signals.acted`를 채웠지만 걷어냈다 — 아래 "왜 지웠나"를 본다.

> **왜 지웠나.** 버튼은 텔레그램 메시지 단위라 신호가 1건인 알림에만 붙일 수 있었는데
> (여러 건이면 어느 신호에 대한 답인지 정해지지 않는다), 실제 신호는 하루 3~6건이라
> **버튼이 거의 안 붙었다.** 게다가 이 비교는 응답을 빠짐없이 해야만 성립한다 — 산 것만
> 답하고 무시한 것은 넘기면 acted/ignored가 무작위 분할이 아니라 **자기가 고른 분할**이
> 되어 결론이 아첨하는 쪽으로 기운다. **절반만 쓰면 없느니만 못한 장치였다.**
> 성적표는 이제 신호를 **전부 샀다고 가정**하고 사후 수익률을 낸다 (`app/scorecard.py`).

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


@dataclass(frozen=True)
class Delivery:
    """보낸 기록. 성공도 실패도 남긴다 — 실패가 조용하면 알림이 끊긴 것을 모른다."""

    at: datetime
    channel: str
    text: str
    ok: bool
    error: str = ""


class AlertChannel(Protocol):
    id: str

    async def send(self, text: str) -> Delivery: ...


@dataclass
class LogChannel:
    """아무 데도 보내지 않고 기록만 한다. 토큰이 없을 때의 기본값.

    **"채널이 없다"와 "보냈다"를 구분해서 남긴다** — 미구현을 성공처럼 보이게 하지
    않는 것이 12.3의 규약이다.
    """

    id: str = "log"
    sent: list[Delivery] = field(default_factory=list)

    async def send(self, text: str) -> Delivery:
        delivery = Delivery(datetime.now(UTC), self.id, text, ok=True)
        self.sent.append(delivery)
        return delivery


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

    async def send(self, text: str) -> Delivery:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        try:
            await asyncio.to_thread(self._call, "sendMessage", payload)
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=True)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # ★ 알림 실패로 실행을 실패시키지 않는다. 신호는 이미 기록됐고,
            #   여기서 터뜨리면 되돌릴 수 없는 것(봉 소비)이 이미 끝난 뒤다.
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=False, error=str(exc))
        self.sent.append(delivery)
        return delivery

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


def default_channel(config: Any | None = None) -> AlertChannel:
    """토큰이 있으면 텔레그램, 없으면 기록만 하는 채널.

    ⚠️ **`config`를 반드시 넘긴다.** 토큰은 2026-08-06부터 `config.yml`의
    `telegram:`에 사는데, 여기서 환경변수만 보면 **설정에 토큰을 적어 둔 사람의
    알림이 영영 안 나간다.** 게다가 `describe`는 설정을 읽어 "알림 telegram"이라고
    표시하므로 **화면과 실제가 어긋난 채로 조용히 돈다** — 실제로 밟은 사고다.

    `config`가 없으면 환경변수만 본다(설정을 아직 못 읽은 경로의 최후 수단).

    **토큰이 없다고 실패시키지 않는다.** 알림 없이 도는 `serve`도 유효한 사용이고,
    시작할 때 "기록만 한다"고 경고가 나간다 (12.3).
    """
    if config is not None:
        token, chat_id = config.telegram.resolved()
    else:
        settings = get_settings()
        token = (settings.telegram_token or "").strip()
        chat_id = (settings.telegram_chat_id or "").strip()
    if token and chat_id:
        return TelegramChannel(token=token, chat_id=chat_id)
    return LogChannel()
