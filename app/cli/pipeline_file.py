"""파이프라인 정의 파일 로딩과 `--market` 필터.

**형식은 YAML로 확정됐다** (ARCHITECTURE.md 11장 4번, Phase 1에서 해소).
손으로 적어 본 결과 JSON의 문제는 구조가 아니라 **주석을 달 수 없다는 것**이었다.
파이프라인 파일에 적고 싶은 것의 절반은 "왜 이 종목인가" · "왜 이 값인가"인데
JSON에는 그걸 적을 자리가 없다.

**6장의 스키마는 그대로다.** YAML은 같은 구조를 다르게 적는 것뿐이고, 로더가
확장자로 갈라 받아 같은 `PipelineSpec`을 만든다. 새 스키마도, 변환 규칙도 없다.

`pipeline_versions`에 남는 스냅샷은 **JSON 그대로 유지한다** — 그건 사람이 적는
형식이 아니라 직렬화이고, 저장된 버전은 불변이어야 하므로 표현이 흔들리면 안 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.config import get_settings
from app.market.instrument import markets, venues_of
from app.schemas.pipeline import PipelineSpec

#: 확장자 → 파서. YAML이 정본이고 JSON은 계속 읽는다 (스냅샷·기존 파일 호환).
_PARSERS = {
    ".yaml": yaml.safe_load,
    ".yml": yaml.safe_load,
    ".json": json.loads,
}

#: `--market` 값 → 포함할 venue. Fresh Bar Gate(3.5)가 있어 없어도 되지만,
#: 실행 로그를 읽기 편하게 하려고 시장별로 쪼갤 수 있게 둔다 (8장).
#:
#: **표를 손으로 두지 않고 `VENUES`에서 유도한다.** 같은 분류가 두 곳에 있으면
#: venue를 추가한 날 한쪽만 고치게 되고, 그러면 `--market`이 조용히 종목을 빠뜨린다.
#: 랭킹 풀(규칙 17)도 같은 어휘를 쓴다.
MARKETS: dict[str, tuple[str, ...]] = {m: venues_of(m) for m in markets()}


class PipelineFileError(ValueError):
    """파일을 읽거나 해석하지 못했을 때. 종료 코드 3(검증 실패)으로 이어진다."""


def default_path() -> Path:
    settings = get_settings()
    return settings.resolve(settings.pipeline_path)


def load(path: Path | None = None) -> PipelineSpec:
    target = path or default_path()
    if not target.is_file():
        raise PipelineFileError(
            f"파이프라인 정의를 찾을 수 없습니다: {target}. "
            f"--pipeline으로 경로를 지정하거나 MARKETSCAN_PIPELINE_PATH를 설정하세요."
        )

    parser = _PARSERS.get(target.suffix.lower())
    if parser is None:
        raise PipelineFileError(
            f"알 수 없는 파이프라인 파일 형식입니다: {target.suffix!r} ({target}). "
            f"사용 가능: {', '.join(sorted(_PARSERS))}"
        )

    try:
        raw = parser(target.read_text(encoding="utf-8"))
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise PipelineFileError(f"{target}의 형식이 잘못됐습니다 — {exc}") from exc

    if not isinstance(raw, dict):
        # 빈 YAML은 None이 된다. 그대로 넘기면 pydantic이 알아듣기 어려운 오류를 낸다.
        raise PipelineFileError(
            f"{target}의 최상위는 매핑이어야 합니다 (읽은 값: {type(raw).__name__}). "
            f"pipeline_id · nodes · edges를 적으세요."
        )

    try:
        return PipelineSpec.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise PipelineFileError(f"{target}의 스키마가 맞지 않습니다 — {details}") from exc


def filter_by_market(spec: PipelineSpec, market: str) -> tuple[PipelineSpec, list[str]]:
    """지정한 시장의 종목만 남긴 사본과 제외된 것들의 목록을 돌려준다.

    원본 spec을 건드리지 않는다 — 저장된 버전 스냅샷은 불변이어야 한다 (4.7).

    **고정 목록(`instruments`)과 동적 조회(`venues`)를 모두 걸러야 한다.** 동적
    유니버스 쪽을 빠뜨리면 `--market krx`가 세 시장을 그대로 훑으면서 로그만
    "krx"라고 적힌다 — 필터가 있다는 사실이 오히려 오해를 만든다.
    """
    venues = MARKETS.get(market)
    if venues is None:
        raise PipelineFileError(
            f"알 수 없는 시장: {market!r}. 사용 가능: {', '.join(MARKETS)}"
        )

    clone = spec.model_copy(deep=True)
    dropped: list[str] = []
    for node in clone.nodes:
        instruments = node.params.get("instruments")
        if isinstance(instruments, list):
            kept = [s for s in instruments if isinstance(s, str) and s.split(":", 1)[0] in venues]
            dropped.extend(s for s in instruments if s not in kept)
            node.params["instruments"] = kept

        queries = node.params.get("venues")
        if isinstance(queries, list):
            kept_q = [q for q in queries if _query_venue(q) in venues]
            dropped.extend(
                f"venues[{_query_venue(q)}]" for q in queries if _query_venue(q) not in venues
            )
            node.params["venues"] = kept_q

        # 단수 표기도 같이 건다. 하위 호환이라 남아 있는 파일이 있다.
        single = node.params.get("venue")
        if isinstance(single, str) and single not in venues:
            dropped.append(f"venue[{single}]")
            node.params["venue"] = None
    return clone, dropped


def _query_venue(query: object) -> str:
    return str(query.get("venue", "")) if isinstance(query, dict) else ""


def has_empty_universe(spec: PipelineSpec) -> bool:
    """`--market` 필터로 훑을 것이 하나도 남지 않았는가.

    동적 조회가 하나라도 남아 있으면 비어 있지 않다 — 파일에 종목이 0개로 적혀
    있어도 실행 시점에 거래소가 채운다.
    """
    if any(node.params.get("venues") or node.params.get("venue") for node in spec.nodes):
        return False
    lists = [
        node.params["instruments"]
        for node in spec.nodes
        if isinstance(node.params.get("instruments"), list)
    ]
    return bool(lists) and all(not items for items in lists)


def strategy_ids(spec: PipelineSpec) -> list[str]:
    """이 파이프라인이 참조하는 전략 id 목록."""
    out: list[str] = []
    for node in spec.nodes:
        if node.type == "strategyRunner":
            sid = node.params.get("strategy_id")
            if isinstance(sid, str) and sid and sid not in out:
                out.append(sid)
    return out


def universe_summary(spec: PipelineSpec) -> dict[str, object]:
    """유니버스를 어떻게 정하는가.

    종목 수를 세는 것만으로는 부족하다 — 동적 유니버스는 파일에 종목이 0개로
    적혀 있고, 그걸 그대로 "0종목"이라고 보여 주면 **파이프라인이 고장 난 것처럼
    보인다.** 실제로는 실행 시점에 거래소가 정한다.
    """
    fixed = instruments(spec)
    sources: list[dict[str, object]] = []
    for node in spec.nodes:
        if node.type != "symbolUniverse":
            continue
        for query in _venue_queries(node.params):
            sources.append({"node_id": node.id, **query})
    return {"fixed": fixed, "fixed_size": len(fixed), "dynamic": sources}


def _venue_queries(params: dict[str, object]) -> list[dict[str, object]]:
    """`venues` 목록과 단수 `venue` 표기를 같은 모양으로 돌려준다."""
    queries = params.get("venues")
    if isinstance(queries, list):
        return [dict(q) for q in queries if isinstance(q, dict) and q.get("venue")]
    if params.get("venue"):
        return [
            {
                "venue": params.get("venue"),
                "quote_currency": params.get("quote_currency"),
                "top_by_turnover": params.get("top_by_turnover"),
            }
        ]
    return []


def describe_universe(summary: dict[str, object]) -> str:
    """`describe`의 사람용 한 줄."""
    parts: list[str] = []
    if summary["fixed_size"]:
        parts.append(f"고정 {summary['fixed_size']}종목")
    for source in summary["dynamic"]:  # type: ignore[union-attr]
        detail = f"{source['venue']}"
        if source.get("quote_currency"):
            detail += f"/{source['quote_currency']}"
        if source.get("top_by_turnover"):
            detail += f" 거래대금 상위 {source['top_by_turnover']}"
        elif source.get("limit"):
            detail += f" 목록 상위 {source['limit']}"
        parts.append(f"동적({detail})")
    return " + ".join(parts) or "미지정"


def instruments(spec: PipelineSpec) -> list[str]:
    out: list[str] = []
    for node in spec.nodes:
        for symbol in node.params.get("instruments", []) or []:
            if isinstance(symbol, str) and symbol not in out:
                out.append(symbol)
    return out
