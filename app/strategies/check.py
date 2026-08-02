"""`marketscan strategy check` — 인과성 정적 검사 (ARCHITECTURE.md 12.6).

4.2 규칙 1의 인과성은 런타임에 강제할 수 없다. 하지만 **AST로 상당 부분 잡힌다.**
LLM이 전략을 쓰고 → `check`가 거르고 → `verify`가 엔진을 검증하는 루프가 여기서
만들어진다. LLM이 무심코 `shift(-1)`을 쓰는 것은 흔한 일이라 이 검사는 실제로
값을 한다.

⚠️ **통과가 인과성을 보장하지는 않는다.** 사후 방어선은 난수 신호 테스트다(4.8).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 전략이 직접 임포트하면 안 되는 라이브러리. 전략은 이미 잘린 DataFrame만 받는다 (4.2 규칙 2).
NETWORK_MODULES = frozenset(
    {
        "httpx", "requests", "urllib", "urllib3", "aiohttp", "socket", "http",
        "ccxt", "yfinance", "pykrx", "FinanceDataReader", "fdr", "websockets",
    }
)

#: `ctx.now`를 우회하는 시각 호출 (규칙 1).
CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "time_ns", "monotonic"})
CLOCK_OWNERS = frozenset({"datetime", "date", "time", "pd", "pandas", "Timestamp"})

#: 미래를 보는 pandas 연산.
BACKFILL_METHODS = frozenset({"bfill", "backfill"})


@dataclass(frozen=True)
class Violation:
    rule: str
    line: int
    detail: str
    level: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "line": self.line, "detail": self.detail, "level": self.level}


@dataclass
class CheckResult:
    strategy_id: str
    path: Path
    violations: list[Violation]

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strategy_id": self.strategy_id,
            "path": str(self.path),
            "violations": [v.to_dict() for v in self.violations],
        }


def check_source(source: str, strategy_id: str, path: Path) -> CheckResult:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return CheckResult(
            strategy_id,
            path,
            [Violation("syntax", exc.lineno or 0, f"문법 오류: {exc.msg}")],
        )

    visitor = _CausalityVisitor()
    visitor.visit(tree)
    violations = sorted(visitor.violations, key=lambda v: (v.line, v.rule))
    violations.extend(_declaration_violations(tree))
    return CheckResult(strategy_id, path, violations)


def check_file(path: Path, strategy_id: str | None = None) -> CheckResult:
    return check_source(path.read_text(encoding="utf-8"), strategy_id or path.stem, path)


class _CausalityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[Violation] = []

    # ---- 임포트 ---------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._check_module(node.module, node.lineno)
        self.generic_visit(node)

    def _check_module(self, module: str, line: int) -> None:
        root = module.split(".")[0]
        if root in NETWORK_MODULES:
            self.violations.append(
                Violation(
                    "no_network",
                    line,
                    f"import {module} — 전략은 데이터를 직접 가져올 수 없습니다. "
                    f"이미 end로 잘린 DataFrame만 받습니다 (4.2 규칙 2).",
                )
            )

    # ---- 호출 -----------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        name = _called_name(node.func)
        owner = _owner_name(node.func)

        if name == "shift":
            self._check_shift(node)
        if name in BACKFILL_METHODS:
            self.violations.append(
                Violation(
                    "causality",
                    node.lineno,
                    f".{name}() — 뒤 값을 앞으로 당깁니다. 미래 참조입니다. "
                    f"결측은 ffill이나 dropna로 처리하세요.",
                )
            )
        if name == "fillna":
            for kw in node.keywords:
                if (
                    kw.arg == "method"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value in BACKFILL_METHODS
                ):
                    self.violations.append(
                        Violation(
                            "causality",
                            node.lineno,
                            f"fillna(method={kw.value.value!r}) — 미래 참조입니다. "
                            f"ffill을 쓰세요.",
                        )
                    )
        if name in CLOCK_CALLS and owner in CLOCK_OWNERS:
            self.violations.append(
                Violation(
                    "injected_clock",
                    node.lineno,
                    f"{owner}.{name}() — ctx.now를 쓰세요. 전략이 직접 시계를 읽으면 "
                    f"백테스트와 실행이 다른 코드가 됩니다 (규칙 1).",
                )
            )

        for kw in node.keywords:
            if kw.arg == "center" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.violations.append(
                    Violation(
                        "causality",
                        node.lineno,
                        "center=True — 창의 중앙을 기준으로 삼아 뒤쪽 봉을 봅니다. "
                        "미래 참조입니다.",
                    )
                )

        self.generic_visit(node)

    def _check_shift(self, node: ast.Call) -> None:
        candidates: list[ast.expr] = list(node.args[:1])
        candidates += [kw.value for kw in node.keywords if kw.arg == "periods"]
        for arg in candidates:
            periods = _negative_constant(arg)
            if periods is not None:
                self.violations.append(
                    Violation(
                        "causality",
                        node.lineno,
                        f"shift({periods}) — 미래 참조입니다. "
                        f"타깃(정답 라벨) 계산이라면 백테스트 평가기로 옮기세요.",
                    )
                )


def _declaration_violations(tree: ast.Module) -> list[Violation]:
    """Strategy 구현체가 갖춰야 할 선언을 확인한다."""
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_called_name(base) == "Strategy" for base in node.bases):
            continue

        assigned = {
            target.id
            for stmt in node.body
            if isinstance(stmt, ast.Assign)
            for target in stmt.targets
            if isinstance(target, ast.Name)
        } | {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
        nested = {stmt.name for stmt in node.body if isinstance(stmt, ast.ClassDef)}
        methods = {stmt.name for stmt in node.body if isinstance(stmt, ast.FunctionDef)}

        if "id" not in assigned:
            out.append(
                Violation(
                    "declaration",
                    node.lineno,
                    f"{node.name}에 id가 없습니다. 파일 이름과 같은 값으로 선언하세요.",
                )
            )
        if "compute" not in methods:
            out.append(
                Violation(
                    "declaration",
                    node.lineno,
                    f"{node.name}에 compute가 없습니다. 종목별 지표는 여기서 채웁니다.",
                )
            )
        if "Params" not in assigned | nested:
            out.append(
                Violation(
                    "declaration",
                    node.lineno,
                    f"{node.name}에 Params가 없습니다. 파라미터가 정말 없다면 무시해도 됩니다 — "
                    f"선언해 두면 코드를 고치지 않고 --param으로 바꿀 수 있습니다.",
                    level="warning",
                )
            )
    return out


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _owner_name(func: ast.expr) -> str | None:
    """`datetime.now`의 `datetime`, `pd.Timestamp.now`의 `Timestamp`."""
    if isinstance(func, ast.Attribute):
        return _called_name(func.value)
    return None


def _negative_constant(node: ast.expr) -> int | None:
    """`-5` 형태의 음수 리터럴이면 그 값을, 아니면 None."""
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int | float)
    ):
        return -node.operand.value
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and node.value < 0:
        return node.value
    return None
