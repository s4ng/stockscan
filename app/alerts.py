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
import re
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

#: 텔레그램 서식. **HTML을 쓰고 MarkdownV2를 쓰지 않는다** — MarkdownV2는
#: `.`·`-`·`(`·`+` 같은 흔한 문자를 전부 이스케이프해야 하는데, 이 알림에는
#: 가격(`+2.68%`)과 종목 코드(`krx:005930`)가 매 줄 들어간다. 하나만 빠뜨려도
#: **메시지 전체가 거부되고**, 그게 정확히 이 시스템에서 가장 나쁜 실패다.
PARSE_MODE = "HTML"


# --------------------------------------------------------------------------- 서식
def esc(text: Any) -> str:
    """HTML 특수문자를 막는다. **바깥에서 온 문자열은 전부 이걸 지난다.**

    종목명은 소스가 주는 값이라 `&`가 들어올 수 있고(`AT&T`·`S&P Global`),
    feature 이름은 전략 파일이 정하는 값이다. 하나라도 새면 텔레그램이 메시지를
    통째로 거부한다.
    """
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def bold(text: Any) -> str:
    return f"<b>{esc(text)}</b>"


def code(text: Any) -> str:
    """탭하면 복사되는 조각. 다음에 칠 명령을 여기에 담는다."""
    return f"<code>{esc(text)}</code>"


def strip_tags(text: str) -> str:
    """서식을 걷어낸 평문. 텔레그램이 서식을 거부했을 때의 되돌림용이다."""
    out = re.sub(r"<[^>]+>", "", text)
    return (
        out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    )


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
        try:
            await asyncio.to_thread(self._call, "sendMessage", self._payload(text))
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=True)
        except ValueError as exc:
            # ★ **서식 때문에 알림이 사라지지 않게 한다.** 텔레그램은 태그가 하나만
            #   어긋나도 메시지 **전체**를 거부한다. 서식은 읽기 편하자고 붙인 것이지
            #   내용이 아니므로, 걷어내고 한 번 더 보낸다 — 못생긴 알림이 없는
            #   알림보다 낫다. (거부 사유가 서식이 아니면 두 번째도 같은 이유로
            #   실패하고, 그때는 아래에서 실패로 남는다.)
            delivery = await self._retry_plain(text, exc)
        except (urllib.error.URLError, OSError) as exc:
            # ★ 알림 실패로 실행을 실패시키지 않는다. 신호는 이미 기록됐고,
            #   여기서 터뜨리면 되돌릴 수 없는 것(봉 소비)이 이미 끝난 뒤다.
            delivery = Delivery(datetime.now(UTC), self.id, text, ok=False, error=str(exc))
        self.sent.append(delivery)
        return delivery

    async def _retry_plain(self, text: str, original: Exception) -> Delivery:
        """서식을 걷어내고 **서식 해석 없이** 한 번 더 보낸다.

        태그가 없는 메시지도 다시 보낸다 — 이스케이프를 빠뜨린 `<`가 섞였을 때가
        정확히 그 경우이고, `parse_mode`를 빼면 해석 자체를 하지 않아 나간다.
        거부 사유가 서식이 아니었다면(토큰·chat_id) 두 번째도 같은 이유로 실패해
        그대로 실패로 남는다.
        """
        plain = strip_tags(text)
        try:
            await asyncio.to_thread(
                self._call, "sendMessage", self._payload(plain, parse_mode=None)
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Delivery(datetime.now(UTC), self.id, text, ok=False, error=str(exc))
        log.warning("텔레그램이 서식을 거부해 평문으로 보냈습니다 — %s", original)
        return Delivery(datetime.now(UTC), self.id, plain, ok=True, error=str(original))

    def _payload(self, text: str, parse_mode: str | None = PARSE_MODE) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return payload

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
