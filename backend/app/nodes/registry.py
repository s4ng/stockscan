"""노드 레지스트리 — DAG JSON의 `type` 문자열을 구현 클래스로 이어준다."""

from __future__ import annotations

from typing import Any, TypeVar

from app.nodes.base import BaseNode

_REGISTRY: dict[str, type[BaseNode]] = {}

T = TypeVar("T", bound=type[BaseNode])


class UnknownNodeTypeError(KeyError):
    pass


def register(cls: T) -> T:
    """노드 클래스 데코레이터. 모듈이 임포트되는 순간 자기 자신을 등록한다."""
    node_type = getattr(cls, "type", None)
    if not node_type:
        raise ValueError(f"{cls.__name__}에 type이 정의되지 않았습니다")
    if node_type in _REGISTRY and _REGISTRY[node_type] is not cls:
        raise ValueError(f"노드 type이 중복됩니다: {node_type!r}")
    _REGISTRY[node_type] = cls
    return cls


def get_node_class(node_type: str) -> type[BaseNode]:
    try:
        return _REGISTRY[node_type]
    except KeyError as exc:
        raise UnknownNodeTypeError(
            f"알 수 없는 노드 type: {node_type!r}. "
            f"등록된 type: {', '.join(sorted(_REGISTRY)) or '(없음)'}"
        ) from exc


def is_registered(node_type: str) -> bool:
    return node_type in _REGISTRY


def all_node_classes() -> dict[str, type[BaseNode]]:
    return dict(_REGISTRY)


def catalog() -> list[dict[str, Any]]:
    """프론트엔드 팔레트용 목록. 카테고리 → 이름 순으로 정렬한다."""
    return sorted(
        (cls.descriptor() for cls in _REGISTRY.values()),
        key=lambda d: (d["category"], d["display_name"]),
    )
