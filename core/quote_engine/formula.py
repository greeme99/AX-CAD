"""Safe Decimal evaluator for process-rule formulas (S9, security review L3: never eval()).

Grammar: numbers, names, + - * / ( ), unary +/-, and min/max/ceil/floor calls. Anything else
(attributes, subscripts, power, comparisons, lambdas, strings...) is rejected at parse time, so a
formula stored in master data can only ever compute a number from the given variables."""

import ast
import math
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from typing import Any

MAX_LEN = 500
MAX_NODES = 200
FUNCS = {"min": min, "max": max}
ROUNDERS = {"ceil": math.ceil, "floor": math.floor}
BIN = {
    ast.Add: Decimal.__add__,
    ast.Sub: Decimal.__sub__,
    ast.Mult: Decimal.__mul__,
    ast.Div: Decimal.__truediv__,
}


class FormulaError(ValueError):
    pass


def parse(text: str) -> ast.Expression:
    """Parse and whitelist. Raises FormulaError with a short Korean message."""
    if not text or len(text) > MAX_LEN:
        raise FormulaError("공식이 비었거나 너무 깁니다")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):  # ValueError: NUL bytes
        raise FormulaError("공식 문법 오류") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise FormulaError("공식이 너무 복잡합니다")
    for n in nodes:
        ok = isinstance(
            n,
            (ast.Expression, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp, ast.UAdd, ast.USub, *BIN),
        )
        if isinstance(n, ast.Constant):
            ok = isinstance(n.value, (int, float)) and not isinstance(n.value, bool)
        if isinstance(n, ast.Call):
            ok = (
                isinstance(n.func, ast.Name)
                and n.func.id in {*FUNCS, *ROUNDERS}
                and not n.keywords
                and 1 <= len(n.args) <= (1 if n.func.id in ROUNDERS else 10)
            )
        if not ok:
            raise FormulaError(f"허용되지 않는 식 요소: {type(n).__name__}")
    return tree


def names(text: str) -> set[str]:
    """Variables a formula reads (a name used as a call target is not a variable)."""
    tree = parse(text)
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in funcs}


def evaluate(text: str, env: dict[str, Any]) -> Decimal:
    tree = parse(text)

    def ev(n: ast.AST) -> Decimal:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return Decimal(str(n.value))
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise FormulaError(f"알 수 없는 이름: {n.id}")
            return Decimal(str(env[n.id]))
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            return BIN[type(n.op)](ev(n.left), ev(n.right))  # type: ignore[index]
        assert isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        args = [ev(a) for a in n.args]
        if n.func.id in ROUNDERS:
            if len(args) != 1:
                raise FormulaError(f"{n.func.id}()는 인자 1개")
            return Decimal(ROUNDERS[n.func.id](args[0]))
        return Decimal(FUNCS[n.func.id](args))

    # explicit context: never inherit a caller's precision or disabled traps
    ctx = Context(prec=28, traps=[DivisionByZero, InvalidOperation, Overflow])
    with localcontext(ctx):
        try:
            out = ev(tree)
        except (DivisionByZero, InvalidOperation, Overflow, ZeroDivisionError, OverflowError):
            raise FormulaError("0으로 나누었거나 계산할 수 없습니다") from None
    if not out.is_finite():
        raise FormulaError("결과가 유한한 숫자가 아닙니다")
    return out
