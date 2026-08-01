"""노드 인터페이스 (ARCHITECTURE.md 4.2).

모든 노드는 `dict[핸들, Bundle]`을 받아 `dict[핸들, Bundle]`을 돌려준다.
파라미터는 Pydantic 모델로 선언하며, 그 JSON Schema가 프론트엔드 폼을 만든다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.engine.context import RunContext
from app.engine.types import Bundle
from app.schemas.pipeline import MAIN


class NodeError(RuntimeError):
    """노드 실행 중 발생한 오류. on_error 정책에 따라 처리된다."""


class NodeParamsError(ValueError):
    """파라미터가 스키마에 맞지 않을 때. 실행 전 검증 단계에서 잡힌다."""


class BaseNode(ABC):
    #: DAG JSON의 `type` 값. 레지스트리 키.
    type: ClassVar[str]
    display_name: ClassVar[str] = ""
    category: ClassVar[str] = "logic"
    description: ClassVar[str] = ""

    ParamsModel: ClassVar[type[BaseModel]]

    inputs: ClassVar[tuple[str, ...]] = (MAIN,)
    outputs: ClassVar[tuple[str, ...]] = (MAIN,)

    requires_input: ClassVar[bool] = True
    """False면 상류 없이도 실행된다.

    소스 노드(Market Data 등)는 `main` 입력을 **가지되 요구하지는 않는다.**
    트리거에서 좌→우로 흐르는 배치를 허용하면서, 트리거 없이 단독 실행도 되게 하기
    위해서다. 상류가 있는데 그 상류가 모두 skip됐다면 이 노드도 skip된다.
    """

    @abstractmethod
    async def run(
        self,
        inputs: dict[str, Bundle],
        params: Any,
        ctx: RunContext,
    ) -> dict[str, Bundle]:
        """노드 본체. 반환 dict의 키는 반드시 `outputs`에 있는 핸들이어야 한다."""

    # ------------------------------------------------------------------
    @classmethod
    def parse_params(cls, raw: dict[str, Any]) -> BaseModel:
        try:
            return cls.ParamsModel.model_validate(raw)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors()
            )
            raise NodeParamsError(f"[{cls.type}] 파라미터 오류 — {details}") from exc

    @classmethod
    def has_input(cls, handle: str) -> bool:
        return handle in cls.inputs

    @classmethod
    def has_output(cls, handle: str) -> bool:
        # error 핸들은 on_error=route일 때 암묵적으로 존재한다
        return handle in cls.outputs or handle == "error"

    @classmethod
    def descriptor(cls) -> dict[str, Any]:
        """`GET /api/nodes` 응답 항목. 프론트엔드가 이걸로 팔레트와 폼을 만든다."""
        return {
            "type": cls.type,
            "display_name": cls.display_name or cls.type,
            "category": cls.category,
            "description": cls.description,
            "inputs": list(cls.inputs),
            "outputs": list(cls.outputs),
            "params_schema": cls.ParamsModel.model_json_schema(),
        }
