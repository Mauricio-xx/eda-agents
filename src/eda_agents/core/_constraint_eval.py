"""Safe evaluator for boolean constraint expressions used by
:class:`GenericDesign` to declare domain gates without subclassing.

The grammar is intentionally minimal:

* numeric literals (int, float)
* named variables, looked up in a scope dict at eval time
* binary arithmetic: ``+ - * / % **``
* unary arithmetic: ``- +``
* comparisons: ``< <= > >= == !=``
* boolean: ``and or``
* parentheses

Top-level node must be a :class:`ast.Compare` or :class:`ast.BoolOp`
so a bare arithmetic expression like ``"x + 1"`` is rejected at
compile time rather than being silently truthified.

``Pow`` is restricted to a literal integer exponent bounded by
:data:`MAX_POW_EXPONENT` so ``x ** (10 ** 18)`` cannot exhaust
resources.

There are no function calls, attribute accesses, subscripts, lambdas,
comprehensions, conditional expressions, or other control flow.
Anything outside the allowlist raises :class:`ConstraintEvalError`
at compile time so a typo surfaces at ``GenericDesign(...)``
construction, not 30 minutes into a flow.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Final

MAX_POW_EXPONENT: Final[int] = 64


class ConstraintEvalError(Exception):
    """Raised at compile time (bad syntax or disallowed AST node) or
    at eval time (unknown variable, type mismatch, numeric error)."""


_BIN_OPS: Final[tuple[type[ast.AST], ...]] = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
)
_UNARY_OPS: Final[tuple[type[ast.AST], ...]] = (ast.UAdd, ast.USub)
_COMPARE_OPS: Final[tuple[type[ast.AST], ...]] = (
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)
_BOOL_OPS: Final[tuple[type[ast.AST], ...]] = (ast.And, ast.Or)


def _literal_int(node: ast.AST) -> int | None:
    """Return the int value of a literal integer node, including
    ``-N`` and ``+N`` (which parse as UnaryOp on a Constant). Returns
    ``None`` if the node is not a literal integer."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY_OPS):
        inner = _literal_int(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    return None


def compile_expr(expr: str) -> ast.Expression:
    """Parse ``expr`` and assert every node is on the whitelist.

    Returns the parsed :class:`ast.Expression` ready for
    :func:`eval_expr`. Raises :class:`ConstraintEvalError` on syntax
    errors, on any disallowed node, on a Pow with an exponent that is
    not a constant integer in
    ``[-MAX_POW_EXPONENT, MAX_POW_EXPONENT]``, or when the top-level
    node is not :class:`ast.Compare` or :class:`ast.BoolOp`.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConstraintEvalError(
            f"Syntax error in expression {expr!r}: {e.msg}"
        ) from e

    body = tree.body
    if not isinstance(body, (ast.Compare, ast.BoolOp)):
        raise ConstraintEvalError(
            "Top-level expression must be a comparison or boolean "
            "combination (e.g. 'x >= 0' or 'x >= 0 and y < 1'); got "
            f"{type(body).__name__} in {expr!r}."
        )

    _validate(body)
    return tree


def _validate(node: ast.AST) -> None:
    if isinstance(node, ast.Compare):
        if any(not isinstance(op, _COMPARE_OPS) for op in node.ops):
            offenders = [
                type(op).__name__
                for op in node.ops
                if not isinstance(op, _COMPARE_OPS)
            ]
            raise ConstraintEvalError(
                f"Comparison operator not allowed: {offenders}."
            )
        _validate(node.left)
        for cmp_node in node.comparators:
            _validate(cmp_node)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _BOOL_OPS):
            raise ConstraintEvalError(
                f"Boolean operator not allowed: {type(node.op).__name__}."
            )
        for v in node.values:
            _validate(v)
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BIN_OPS):
            raise ConstraintEvalError(
                f"Binary operator not allowed: {type(node.op).__name__}."
            )
        if isinstance(node.op, ast.Pow):
            exponent = _literal_int(node.right)
            if exponent is None or abs(exponent) > MAX_POW_EXPONENT:
                raise ConstraintEvalError(
                    "Pow exponent must be a constant integer with "
                    f"absolute value <= {MAX_POW_EXPONENT}."
                )
        _validate(node.left)
        _validate(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARY_OPS):
            raise ConstraintEvalError(
                f"Unary operator not allowed: {type(node.op).__name__}."
            )
        _validate(node.operand)
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(
            node.value, bool
        ):
            raise ConstraintEvalError(
                "Only numeric literals are allowed; got "
                f"{type(node.value).__name__}."
            )
        return
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            raise ConstraintEvalError(
                f"Name context must be Load; got {type(node.ctx).__name__}."
            )
        return
    raise ConstraintEvalError(
        f"AST node not allowed: {type(node).__name__}."
    )


def eval_expr(
    node: ast.Expression,
    scope: Mapping[str, float | int | bool],
) -> bool:
    """Interpret ``node`` (returned by :func:`compile_expr`) against
    ``scope`` and return the boolean result.

    Raises :class:`ConstraintEvalError` on unknown names, division by
    zero, numeric overflow, or other arithmetic failures.
    """
    return bool(_eval(node.body, scope))


def _eval(
    node: ast.AST, scope: Mapping[str, float | int | bool]
) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise ConstraintEvalError(
                f"Unknown variable {node.id!r} (not in measurements "
                "or constants)."
            )
        return scope[node.id]
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, scope)
        if isinstance(node.op, ast.USub):
            return -operand
        return +operand
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, scope)
        right = _eval(node.right, scope)
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            return left ** right
        except (ZeroDivisionError, OverflowError, TypeError) as e:
            raise ConstraintEvalError(
                f"{type(e).__name__} during evaluation: {e}"
            ) from e
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, scope)
            try:
                if isinstance(op, ast.Lt):
                    cmp = left < right
                elif isinstance(op, ast.LtE):
                    cmp = left <= right
                elif isinstance(op, ast.Gt):
                    cmp = left > right
                elif isinstance(op, ast.GtE):
                    cmp = left >= right
                elif isinstance(op, ast.Eq):
                    cmp = left == right
                else:
                    cmp = left != right
            except TypeError as e:
                raise ConstraintEvalError(
                    f"Comparison failed: {e}"
                ) from e
            if not cmp:
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not _eval(v, scope):
                    return False
            return True
        for v in node.values:
            if _eval(v, scope):
                return True
        return False
    raise ConstraintEvalError(
        f"Internal error: unhandled node {type(node).__name__} "
        "(compile_expr should have caught this)."
    )
