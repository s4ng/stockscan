"""전략 로더 + 소스 해시 (ARCHITECTURE.md 4.7).

전략의 **정본은 `~/.marketscan/`의 파일**이다. IDE·git·리뷰를 쓸 수 있는
쪽이 실사용에 낫기 때문이다. 대신 파일이 되면서 구멍이 하나 생긴다 — 파이프라인이
전략을 **이름으로만** 참조하면 파일을 고치는 순간 **과거 버전의 의미가 소급으로
바뀐다.** "그때 그 신호가 어떤 전략에서 나왔는지"를 잃는 것이고, 그러면
pipeline_versions를 불변으로 둔 이유가 함께 무너진다.

그래서 로더는 항상 소스의 SHA-256을 함께 돌려주고, 실행 시점에 파이프라인이
기록해 둔 해시와 다르면 경고를 남긴다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.strategies.base import Strategy, StrategyError

#: 로드한 전략 모듈을 넣을 네임스페이스. 사용자 전략이 실제 패키지를 가리지 않게 격리한다.
MODULE_NAMESPACE = "marketscan_user_strategies"


class StrategyNotFoundError(StrategyError):
    pass


@dataclass(frozen=True)
class StrategySource:
    """전략 파일 하나의 신원. 해시가 실행 이력에 박힌다."""

    id: str
    path: Path
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "path": str(self.path), "sha256": self.sha256}


@dataclass(frozen=True)
class LoadedStrategy:
    strategy: Strategy
    source: StrategySource

    @property
    def id(self) -> str:
        return self.source.id

    @property
    def sha256(self) -> str:
        return self.source.sha256


#: 지금 실행 중인 파이프라인 파일이 놓인 디렉터리. `pipeline_file.load()`가 채운다.
#:
#: 전략을 **자기를 부르는 설정 파일 옆**에서 찾게 하려는 것이다. 설정 하나와 그것이
#: 부르는 전략들이 한 디렉터리에 모여 있어야 통째로 복사·백업할 수 있다.
_pipeline_dir: Path | None = None


def bind_pipeline_dir(path: Path | None) -> None:
    """전략 탐색의 기준을 이 파이프라인 파일이 있는 디렉터리로 옮긴다."""
    global _pipeline_dir
    _pipeline_dir = Path(path).expanduser().resolve().parent if path is not None else None


def strategies_dir() -> Path:
    """전략을 찾을 디렉터리.

    우선순위: 명시 설정(`MARKETSCAN_STRATEGIES_DIR`) → 활성 파이프라인 파일의
    디렉터리 → `config_dir`. 마지막 단이 있어야 파이프라인을 아직 읽지 않은
    명령(`strategy new` 등)도 갈 곳이 있다.
    """
    settings = get_settings()
    if settings.strategies_dir is not None:
        return settings.resolve(settings.strategies_dir)
    return _pipeline_dir or Path(settings.config_dir).expanduser()


def source_hash(path: Path) -> str:
    """파일 **바이트**의 SHA-256. 줄바꿈 정규화를 하지 않는다 — 바이트가 다르면 다른 전략이다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover(directory: Path | None = None) -> list[StrategySource]:
    """전략 디렉터리를 스캔한다. 파일을 임포트하지 않으므로 부작용이 없다."""
    root = directory or strategies_dir()
    if not root.is_dir():
        return []
    return [
        StrategySource(id=path.stem, path=path, sha256=source_hash(path))
        for path in sorted(root.glob("*.py"))
        if not path.name.startswith("_")
    ]


def load_strategy(strategy_id: str, directory: Path | None = None) -> LoadedStrategy:
    """전략 파일을 임포트해 인스턴스를 만든다."""
    root = directory or strategies_dir()
    path = root / f"{strategy_id}.py"
    if not path.is_file():
        available = ", ".join(s.id for s in discover(root)) or "(없음)"
        raise StrategyNotFoundError(
            f"전략을 찾을 수 없습니다: {strategy_id!r} ({path}). "
            f"사용 가능한 전략: {available}. "
            f"새로 만들려면 `marketscan strategy new {strategy_id}`를 실행하세요."
        )

    cls = _load_class(path, strategy_id)
    if cls.id != strategy_id:
        raise StrategyError(
            f"전략 클래스의 id({cls.id!r})가 파일 이름({strategy_id!r})과 다릅니다. "
            f"{path}에서 `id = \"{strategy_id}\"`로 맞추세요 — "
            f"둘이 어긋나면 실행 이력에서 어느 파일이었는지 되짚을 수 없습니다."
        )

    return LoadedStrategy(
        strategy=cls(),
        source=StrategySource(id=strategy_id, path=path, sha256=source_hash(path)),
    )


def _load_class(path: Path, strategy_id: str) -> type[Strategy]:
    """파일에서 Strategy 구현체를 정확히 하나 찾아 돌려준다."""
    module_name = f"{MODULE_NAMESPACE}.{strategy_id}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - 방어적
        raise StrategyError(f"전략 모듈을 읽을 수 없습니다: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        raise StrategyError(
            f"전략 파일을 불러오는 중 오류가 났습니다: {path}\n  {type(exc).__name__}: {exc}\n"
            f"`marketscan strategy check {strategy_id}`로 먼저 정적 검사를 돌려 보세요."
        ) from exc

    # 임포트해 온 다른 전략을 잡지 않도록 **이 모듈에서 정의된** 클래스만 본다.
    candidates = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and issubclass(obj, Strategy)
        and obj is not Strategy
        and obj.__module__ == module_name
        and not inspect.isabstract(obj)
    ]
    if not candidates:
        raise StrategyError(
            f"{path}에 Strategy 구현체가 없습니다. "
            f"`class MyStrategy(Strategy):`를 정의하고 compute를 채우세요."
        )
    if len(candidates) > 1:
        names = ", ".join(c.__name__ for c in candidates)
        raise StrategyError(
            f"{path}에 Strategy 구현체가 여러 개입니다: {names}. "
            f"파일 하나에 전략 하나만 두세요 — 소스 해시가 파일 단위이기 때문입니다."
        )
    return candidates[0]
