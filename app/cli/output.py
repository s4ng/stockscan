"""출력 규약과 종료 코드 (ARCHITECTURE.md 12.3 / 12.4).

두 가지를 여기서 강제한다.

1. **`--json`이면 stdout에는 JSON만 나간다.** 진행 로그는 전부 stderr로 보낸다.
   LLM이 한국어 표를 파싱하게 두면 오독하고, JSON에 진행 로그가 섞이면 파싱이 깨진다.
2. **"신호 0건"과 "실패"를 구분한다.** 4.1이 빈 `Bundle`도 정상 출력이라고 정했으므로,
   신호가 없다고 0이 아닌 코드를 돌려주면 자동 실행이 매일 실패로 잡힌다.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Sequence
from enum import IntEnum
from typing import Any

import typer


class ExitCode(IntEnum):
    OK = 0
    """성공. **신호 0건도 여기다.**"""

    DATA = 2
    """데이터 소스 실패 — 실행은 시작했지만 봉을 못 받았거나 노드가 터졌다."""

    VALIDATION = 3
    """검증 실패 — 파이프라인·전략·인자가 틀렸다. 재시도해도 같은 결과다."""


class Out:
    """명령 하나의 출력 창구."""

    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    # ---- 진행 상황 (사람과 실행 로그용) -----------------------------------------
    def progress(self, message: str) -> None:
        """--json이면 stderr로 비킨다. stdout의 JSON을 오염시키지 않기 위해서다."""
        typer.echo(message, err=self.as_json)

    def warn(self, message: str) -> None:
        typer.echo(f"⚠️  {message}", err=True)

    def error(self, message: str) -> None:
        typer.echo(f"❌ {message}", err=True)

    # ---- 결과 ------------------------------------------------------------------
    def emit(self, payload: dict[str, Any], human: Iterable[str] = ()) -> None:
        """결과를 내보낸다. --json이면 payload만, 아니면 사람이 읽을 줄들만."""
        if self.as_json:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return
        for line in human:
            typer.echo(line)

    def fail(
        self, code: ExitCode, message: str, payload: dict[str, Any] | None = None
    ) -> None:
        """오류를 내보내고 종료한다. --json이면 오류도 JSON으로 나간다."""
        self.error(message)
        if self.as_json:
            body = {"ok": False, "error": message, "exit_code": int(code)}
            body.update(payload or {})
            typer.echo(json.dumps(body, ensure_ascii=False, indent=2, default=str))
        raise typer.Exit(int(code))


def table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> list[str]:
    """고정폭 표. 값이 없으면 헤더만 돌려주지 않고 빈 리스트를 돌려준다."""
    if not rows:
        return []
    cells = [[_cell(v) for v in row] for row in rows]
    widths = [
        max(_width(headers[i]), *(_width(row[i]) for row in cells)) for i in range(len(headers))
    ]
    lines = [_join(headers, widths), _join(["-" * w for w in widths], widths)]
    lines.extend(_join(row, widths) for row in cells)
    return lines


def _join(values: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(v + " " * (w - _width(v)) for v, w in zip(values, widths, strict=True))


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.4g}"
    return str(value)


def _width(text: str) -> int:
    """한글은 터미널에서 두 칸을 차지한다. 무시하면 표가 어긋난다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
