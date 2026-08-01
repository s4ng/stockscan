"""DAG 검증과 위상 정렬 (ARCHITECTURE.md 4.3).

검증에 실패한 파이프라인은 **실행 자체를 거부한다.** 절반쯤 돌다 죽는 것보다
시작 전에 명확한 이유로 막는 편이 낫다 — 특히 주문 노드가 붙은 뒤에는.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import networkx as nx

from app.market.timeframe import BACKTESTABLE, is_backtestable
from app.nodes.base import NodeParamsError
from app.nodes.registry import get_node_class, is_registered
from app.schemas.pipeline import ErrorPolicy, ExecutionMode, PipelineSpec


class IssueLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    level: IssueLevel
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "level": str(self.level),
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
        }


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level is IssueLevel.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level is IssueLevel.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


class PipelineValidationError(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        summary = "; ".join(i.message for i in result.errors[:5])
        super().__init__(f"파이프라인 검증 실패 ({len(result.errors)}건): {summary}")


def validate(spec: PipelineSpec, mode: ExecutionMode | None = None) -> ValidationResult:
    """구조·핸들·파라미터·사이클을 모두 검사한다."""
    result = ValidationResult()
    add = result.issues.append

    # ---- 노드 -----------------------------------------------------------------
    seen: set[str] = set()
    for node in spec.nodes:
        if node.id in seen:
            add(ValidationIssue(IssueLevel.ERROR, f"노드 id가 중복됩니다: {node.id}", node.id))
        seen.add(node.id)

        if not is_registered(node.type):
            add(
                ValidationIssue(
                    IssueLevel.ERROR, f"알 수 없는 노드 type: {node.type!r}", node.id
                )
            )
            continue

        cls = get_node_class(node.type)
        try:
            cls.parse_params(node.params)
        except NodeParamsError as exc:
            add(ValidationIssue(IssueLevel.ERROR, str(exc), node.id))

        if node.on_error.policy is ErrorPolicy.ROUTE and not _has_error_edge(spec, node.id):
            add(
                ValidationIssue(
                    IssueLevel.WARNING,
                    f"on_error=route인데 error 핸들에 연결된 엣지가 없습니다 — 오류가 조용히 사라집니다",
                    node.id,
                )
            )

    if not spec.nodes:
        add(ValidationIssue(IssueLevel.ERROR, "노드가 하나도 없습니다"))

    # ---- 엣지 -----------------------------------------------------------------
    node_map = {n.id: n for n in spec.nodes}
    for edge in spec.edges:
        source = node_map.get(edge.source)
        target = node_map.get(edge.target)
        if source is None:
            add(
                ValidationIssue(
                    IssueLevel.ERROR, f"엣지의 source 노드가 없습니다: {edge.source}", edge_id=edge.id
                )
            )
        if target is None:
            add(
                ValidationIssue(
                    IssueLevel.ERROR, f"엣지의 target 노드가 없습니다: {edge.target}", edge_id=edge.id
                )
            )
        if source is None or target is None:
            continue

        if is_registered(source.type):
            source_cls = get_node_class(source.type)
            if not source_cls.has_output(edge.source_handle):
                add(
                    ValidationIssue(
                        IssueLevel.ERROR,
                        f"{source.type}에 출력 핸들 {edge.source_handle!r}이(가) 없습니다. "
                        f"사용 가능: {', '.join(source_cls.outputs)} (+error)",
                        source.id,
                        edge.id,
                    )
                )
        if is_registered(target.type):
            target_cls = get_node_class(target.type)
            if not target_cls.has_input(edge.target_handle):
                add(
                    ValidationIssue(
                        IssueLevel.ERROR,
                        f"{target.type}에 입력 핸들 {edge.target_handle!r}이(가) 없습니다. "
                        f"사용 가능: {', '.join(target_cls.inputs) or '(없음)'}",
                        target.id,
                        edge.id,
                    )
                )

    # ---- 사이클 ---------------------------------------------------------------
    graph = build_graph(spec)
    if not nx.is_directed_acyclic_graph(graph):
        try:
            cycle = nx.find_cycle(graph)
            path = " → ".join(str(u) for u, _v, *_ in cycle)
            add(ValidationIssue(IssueLevel.ERROR, f"순환이 있습니다: {path} → ..."))
        except nx.NetworkXNoCycle:  # pragma: no cover - 방어적
            add(ValidationIssue(IssueLevel.ERROR, "순환이 있습니다"))

    # ---- 모드별 게이트 ---------------------------------------------------------
    if mode is ExecutionMode.BACKTEST:
        result.issues.extend(_backtest_issues(spec))

    return result


def _backtest_issues(spec: PipelineSpec) -> list[ValidationIssue]:
    """백테스트는 일봉 이상만 허용한다 (ARCHITECTURE.md 3.6 / 4.8).

    분봉 과거 이력 확보는 비용이 크므로, 커버리지가 쌓이기 전에는 막는다.
    Phase 2에서 ohlcv_cache 커버리지 조회로 대체하면 시간이 지나며 자동으로 열린다.
    """
    issues: list[ValidationIssue] = []
    for node in spec.nodes:
        timeframe = node.params.get("timeframe")
        if not isinstance(timeframe, str):
            continue
        try:
            allowed = is_backtestable(timeframe)
        except ValueError:
            continue
        if not allowed:
            issues.append(
                ValidationIssue(
                    IssueLevel.ERROR,
                    f"백테스트는 일봉 이상만 지원합니다 (요청: {timeframe}). "
                    f"허용: {', '.join(sorted(BACKTESTABLE))}. "
                    f"분봉 전략은 shadow 모드로 정방향 검증하세요.",
                    node.id,
                )
            )
    return issues


def build_graph(spec: PipelineSpec) -> nx.DiGraph:
    """위상 정렬용 그래프.

    핸들이 다른 평행 엣지(true/false가 같은 노드로 들어가는 경우)는 여기서 하나로
    합쳐지지만, 입력 수집은 `spec.edges`를 직접 순회하므로 정보가 유실되지 않는다.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(n.id for n in spec.nodes)
    node_ids = {n.id for n in spec.nodes}
    graph.add_edges_from(
        (e.source, e.target) for e in spec.edges if e.source in node_ids and e.target in node_ids
    )
    return graph


def execution_levels(spec: PipelineSpec) -> list[list[str]]:
    """같은 레벨의 노드는 병렬로 실행할 수 있다."""
    graph = build_graph(spec)
    return [sorted(level) for level in nx.topological_generations(graph)]


def _has_error_edge(spec: PipelineSpec, node_id: str) -> bool:
    return any(e.source == node_id and e.source_handle == "error" for e in spec.edges)
