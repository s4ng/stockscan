"""DAG 실행 엔진 (ARCHITECTURE.md 4.3).

위상 정렬로 레벨을 나누고 같은 레벨은 병렬 실행한다. 노드 하나의 실패가 파이프라인
전체를 무너뜨리지 않도록 노드별 on_error 정책을 적용하고, 선택되지 않은 브랜치는
하위로 skip을 전파한다. 모든 노드의 입·출력 요약은 node_runs로 남는다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.engine.context import RunContext
from app.engine.graph import PipelineValidationError, execution_levels, validate
from app.engine.types import Bundle
from app.nodes.registry import get_node_class
from app.schemas.pipeline import ERROR, ErrorPolicy, NodeSpec, PipelineSpec


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    """일부 노드가 실패했지만 파이프라인은 끝까지 진행됐다."""
    FAILED = "failed"


@dataclass
class NodeRunRecord:
    """노드 1회 실행 기록. '왜 이 신호가 나왔는가'를 사후에 재현하는 근거."""

    node_id: str
    type: str
    status: NodeStatus = NodeStatus.PENDING
    duration_ms: float = 0.0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "type": self.type,
            "status": str(self.status),
            "duration_ms": round(self.duration_ms, 2),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "logs": self.logs,
            "error": self.error,
            "attempts": self.attempts,
        }


@dataclass
class RunResult:
    run_id: str
    pipeline_id: str
    mode: str
    now: str
    status: RunStatus = RunStatus.SUCCESS
    nodes: list[NodeRunRecord] = field(default_factory=list)
    error: str | None = None

    def node(self, node_id: str) -> NodeRunRecord | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_id": self.pipeline_id,
            "mode": self.mode,
            "now": self.now,
            "status": str(self.status),
            "error": self.error,
            "nodes": [n.to_dict() for n in self.nodes],
        }


class RunAborted(RuntimeError):
    """on_error=fail인 노드가 실패해 실행을 중단했을 때."""


async def execute(spec: PipelineSpec, ctx: RunContext) -> RunResult:
    """파이프라인을 실행한다. 검증 실패 시 PipelineValidationError."""
    result_of_validation = validate(spec, ctx.mode)
    if not result_of_validation.ok:
        raise PipelineValidationError(result_of_validation)

    run = RunResult(
        run_id=ctx.run_id,
        pipeline_id=spec.pipeline_id,
        mode=str(ctx.mode),
        now=ctx.now.isoformat(),
    )
    records: dict[str, NodeRunRecord] = {
        n.id: NodeRunRecord(node_id=n.id, type=n.type) for n in spec.nodes
    }
    node_map = {n.id: n for n in spec.nodes}

    #: (노드 id, 출력 핸들) → 산출된 Bundle. 여기 없으면 하위는 skip된다.
    produced: dict[tuple[str, str], Bundle] = {}
    semaphore = asyncio.Semaphore(ctx.settings.max_concurrency)

    try:
        for level in execution_levels(spec):
            await asyncio.gather(
                *(
                    _run_node(node_map[node_id], records[node_id], produced, ctx, semaphore, spec)
                    for node_id in level
                )
            )
    except RunAborted as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)

    run.nodes = [records[n.id] for n in spec.nodes]
    if run.status is not RunStatus.FAILED:
        failed = [r for r in run.nodes if r.status is NodeStatus.ERROR]
        run.status = RunStatus.PARTIAL if failed else RunStatus.SUCCESS
    return run


async def _run_node(
    spec_node: NodeSpec,
    record: NodeRunRecord,
    produced: dict[tuple[str, str], Bundle],
    ctx: RunContext,
    semaphore: asyncio.Semaphore,
    spec: PipelineSpec,
) -> None:
    cls = get_node_class(spec_node.type)

    # ---- 입력 수집 ------------------------------------------------------------
    incoming = [e for e in spec.edges if e.target == spec_node.id]
    inputs: dict[str, Bundle] = {}
    if incoming:
        by_handle: dict[str, list[Bundle]] = {}
        for edge in incoming:
            bundle = produced.get((edge.source, edge.source_handle))
            if bundle is not None:
                by_handle.setdefault(edge.target_handle, []).append(bundle)
        if not by_handle:
            # 상류가 모두 skip/error → 이 노드도 실행하지 않는다
            record.status = NodeStatus.SKIPPED
            return
        inputs = {handle: Bundle.merge(bundles) for handle, bundles in by_handle.items()}
    elif cls.requires_input:
        # 상류 데이터가 필요한데 연결된 엣지가 없다
        record.status = NodeStatus.SKIPPED
        return

    record.inputs = {handle: bundle.summary() for handle, bundle in inputs.items()}
    node_ctx = ctx.bind(spec_node.id)
    params = cls.parse_params(spec_node.params)
    node = cls()

    async with semaphore:
        record.status = NodeStatus.RUNNING
        started = time.perf_counter()
        try:
            outputs = await _execute_with_policy(node, inputs, params, node_ctx, spec_node, record)
        finally:
            record.duration_ms = (time.perf_counter() - started) * 1000
            record.logs = [f"[{r.level}] {r.message}" for r in node_ctx.log.for_node(spec_node.id)]

    if outputs is None:
        return  # 정책에 따라 출력 없이 종료 (하위는 skip)

    for handle, bundle in outputs.items():
        produced[(spec_node.id, handle)] = bundle
    record.outputs = {handle: bundle.summary() for handle, bundle in outputs.items()}
    if record.status is NodeStatus.RUNNING:
        record.status = NodeStatus.SUCCESS


async def _execute_with_policy(
    node: Any,
    inputs: dict[str, Bundle],
    params: Any,
    ctx: RunContext,
    spec_node: NodeSpec,
    record: NodeRunRecord,
) -> dict[str, Bundle] | None:
    """on_error 정책을 적용해 노드를 실행한다."""
    policy = spec_node.on_error
    attempts = policy.max_attempts if policy.policy is ErrorPolicy.RETRY else 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        record.attempts = attempt
        try:
            return await node.run(inputs, params, ctx)
        except Exception as exc:  # noqa: BLE001 - 정책에 따라 분기해야 한다
            last_error = exc
            if attempt < attempts:
                ctx.log.warning(f"{attempt}회차 실패, 재시도합니다: {exc}")
                await asyncio.sleep(policy.backoff_seconds * (2 ** (attempt - 1)))

    assert last_error is not None
    effective = policy.fallback if policy.policy is ErrorPolicy.RETRY else policy.policy
    record.status = NodeStatus.ERROR
    record.error = f"{type(last_error).__name__}: {last_error}"
    ctx.log.error(record.error)

    match effective:
        case ErrorPolicy.FAIL:
            raise RunAborted(f"[{spec_node.id}] {record.error}") from last_error
        case ErrorPolicy.ROUTE:
            return {
                ERROR: Bundle(
                    items=[],
                    context={
                        "error": {
                            "node_id": spec_node.id,
                            "type": spec_node.type,
                            "message": str(last_error),
                            "kind": type(last_error).__name__,
                        }
                    },
                )
            }
        case _:  # SKIP (및 RETRY의 fallback=skip)
            return None
