"""안전한 조건식 평가기.

Condition Splitter와 Custom Expression 노드가 쓰는 사용자 입력 식을 다룬다.
`eval()`을 쓰지 않고 AST를 화이트리스트로 검사하므로, 임포트·속성 탐색·함수 정의
같은 위험한 표현은 파싱 단계에서 거부된다.

    >>> evaluate("tags.score >= 8 and features.rsi_14 < 70", {...})
    True
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_UNARY_OPS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}

#: 식 안에서 호출할 수 있는 함수
_FUNCTIONS = {
    "abs": abs,
    "min": min,
    "max": max,
    "len": len,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
}


class ExpressionError(ValueError):
    """식이 문법에 맞지 않거나 허용되지 않은 표현을 포함할 때."""


def evaluate(expression: str, variables: dict[str, Any]) -> Any:
    """식을 평가한다. 허용되지 않은 노드가 있으면 ExpressionError."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"식 문법 오류: {expression!r} — {exc.msg}") from exc
    return _eval(tree.body, variables)


def evaluate_bool(expression: str, variables: dict[str, Any]) -> bool:
    return bool(evaluate(expression, variables))


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:  # noqa: C901 - 디스패처
    match node:
        case ast.Constant(value=value):
            return value

        case ast.Name(id=name):
            if name in env:
                return env[name]
            if name in _FUNCTIONS:
                return _FUNCTIONS[name]
            raise ExpressionError(
                f"알 수 없는 이름: {name!r}. 사용 가능: {', '.join(sorted(env))}"
            )

        case ast.BoolOp(op=ast.And(), values=values):
            result: Any = True
            for v in values:
                result = _eval(v, env)
                if not result:
                    return result
            return result

        case ast.BoolOp(op=ast.Or(), values=values):
            result = False
            for v in values:
                result = _eval(v, env)
                if result:
                    return result
            return result

        case ast.UnaryOp(op=op, operand=operand):
            fn = _UNARY_OPS.get(type(op))
            if fn is None:
                raise ExpressionError(f"허용되지 않은 단항 연산자: {type(op).__name__}")
            return fn(_eval(operand, env))

        case ast.BinOp(left=left, op=op, right=right):
            fn = _BIN_OPS.get(type(op))
            if fn is None:
                raise ExpressionError(f"허용되지 않은 연산자: {type(op).__name__}")
            return fn(_eval(left, env), _eval(right, env))

        case ast.Compare(left=left, ops=ops, comparators=comparators):
            current = _eval(left, env)
            for op, comparator in zip(ops, comparators, strict=True):
                fn = _CMP_OPS.get(type(op))
                if fn is None:
                    raise ExpressionError(f"허용되지 않은 비교 연산자: {type(op).__name__}")
                right_value = _eval(comparator, env)
                if not fn(current, right_value):
                    return False
                current = right_value
            return True

        case ast.IfExp(test=test, body=body, orelse=orelse):
            return _eval(body, env) if _eval(test, env) else _eval(orelse, env)

        case ast.Attribute(value=value, attr=attr):
            return _attr(_eval(value, env), attr)

        case ast.Subscript(value=value, slice=slice_node):
            container = _eval(value, env)
            key = _eval(slice_node, env)
            try:
                return container[key]
            except (KeyError, IndexError, TypeError):
                return None

        case ast.List(elts=elts):
            return [_eval(e, env) for e in elts]

        case ast.Tuple(elts=elts):
            return tuple(_eval(e, env) for e in elts)

        case ast.Dict(keys=keys, values=values):
            return {
                _eval(k, env) if k is not None else None: _eval(v, env)
                for k, v in zip(keys, values, strict=True)
            }

        case ast.Call(func=ast.Name(id=fname), args=args, keywords=[]):
            fn = _FUNCTIONS.get(fname)
            if fn is None:
                raise ExpressionError(
                    f"호출할 수 없는 함수: {fname!r}. 사용 가능: {', '.join(sorted(_FUNCTIONS))}"
                )
            return fn(*[_eval(a, env) for a in args])

        case _:
            raise ExpressionError(f"허용되지 않은 표현식: {type(node).__name__}")


def _attr(obj: Any, name: str) -> Any:
    """`tags.score`처럼 dict를 속성 문법으로 읽는다. 없으면 None."""
    if isinstance(obj, dict):
        return obj.get(name)
    if name.startswith("_"):
        raise ExpressionError(f"비공개 속성에는 접근할 수 없습니다: {name!r}")
    return getattr(obj, name, None)
