"""Unit tests for the safe constraint expression evaluator."""

from __future__ import annotations

import pytest

from eda_agents.core._constraint_eval import (
    MAX_POW_EXPONENT,
    ConstraintEvalError,
    compile_expr,
    eval_expr,
)


class TestCompileRejects:
    def test_compile_rejects_call_node(self):
        with pytest.raises(ConstraintEvalError, match="not allowed"):
            compile_expr("abs(-1) >= 0")

    def test_compile_rejects_attribute_access(self):
        with pytest.raises(ConstraintEvalError, match="not allowed"):
            compile_expr("obj.attr >= 0")

    def test_compile_rejects_subscript(self):
        with pytest.raises(ConstraintEvalError, match="not allowed"):
            compile_expr("arr[0] >= 0")

    def test_compile_rejects_lambda(self):
        with pytest.raises(ConstraintEvalError, match="Syntax|not allowed"):
            compile_expr("(lambda x: x)(1) >= 0")

    def test_compile_rejects_bare_expression(self):
        """Bare numeric expression at top level is rejected because
        Python's truthiness would silently coerce it."""
        with pytest.raises(ConstraintEvalError, match="comparison or boolean"):
            compile_expr("throughput_sps")

    def test_compile_rejects_bare_arithmetic(self):
        with pytest.raises(ConstraintEvalError, match="comparison or boolean"):
            compile_expr("a + b")

    def test_compile_rejects_string_literal(self):
        with pytest.raises(ConstraintEvalError, match="comparison or boolean"):
            compile_expr("'hello'")

    def test_compile_rejects_pow_with_huge_exponent(self):
        with pytest.raises(ConstraintEvalError, match="Pow exponent"):
            compile_expr(f"x ** {MAX_POW_EXPONENT + 1} >= 0")

    def test_compile_rejects_pow_with_variable_exponent(self):
        with pytest.raises(ConstraintEvalError, match="Pow exponent"):
            compile_expr("x ** y >= 0")

    def test_compile_rejects_invalid_syntax(self):
        with pytest.raises(ConstraintEvalError, match="Syntax error"):
            compile_expr("x >= ")

    def test_compile_rejects_string_constant_inside(self):
        """String literal inside an expression is rejected (we only
        allow numeric literals)."""
        with pytest.raises(ConstraintEvalError, match="numeric literals"):
            compile_expr("x == 'foo'")


class TestCompileAccepts:
    def test_simple_compare(self):
        compile_expr("x >= 5")

    def test_chained_compare(self):
        compile_expr("0 <= x <= 100")

    def test_arithmetic(self):
        compile_expr("(a + b) / 2 >= 5")

    def test_pow_within_cap(self):
        compile_expr(f"x ** {MAX_POW_EXPONENT} >= 0")

    def test_negative_pow(self):
        compile_expr(f"x ** -{MAX_POW_EXPONENT} >= 0")

    def test_unary_minus(self):
        compile_expr("-x >= -10")

    def test_boolean_combination(self):
        compile_expr("x >= 0 and y <= 10")

    def test_boolean_or(self):
        compile_expr("x < 0 or y > 100")


class TestEval:
    def test_eval_basic_comparison_true(self):
        node = compile_expr("x >= 5")
        assert eval_expr(node, {"x": 10}) is True

    def test_eval_basic_comparison_false(self):
        node = compile_expr("x >= 5")
        assert eval_expr(node, {"x": 3}) is False

    def test_eval_arithmetic_and_paren(self):
        node = compile_expr("(a + b) / 2 >= 5")
        assert eval_expr(node, {"a": 4, "b": 8}) is True
        assert eval_expr(node, {"a": 1, "b": 2}) is False

    def test_eval_unknown_var_raises(self):
        node = compile_expr("x >= 0")
        with pytest.raises(ConstraintEvalError, match="Unknown variable"):
            eval_expr(node, {})

    def test_eval_zero_division_raises(self):
        node = compile_expr("x / 0 >= 0")
        with pytest.raises(ConstraintEvalError, match="ZeroDivisionError"):
            eval_expr(node, {"x": 1.0})

    def test_eval_chained_compare(self):
        node = compile_expr("0 <= x <= 100")
        assert eval_expr(node, {"x": 50}) is True
        assert eval_expr(node, {"x": -1}) is False
        assert eval_expr(node, {"x": 101}) is False

    def test_eval_boolean_and(self):
        node = compile_expr("x >= 0 and y <= 10")
        assert eval_expr(node, {"x": 5, "y": 5}) is True
        assert eval_expr(node, {"x": 5, "y": 11}) is False
        assert eval_expr(node, {"x": -1, "y": 5}) is False

    def test_eval_boolean_or(self):
        node = compile_expr("x < 0 or y > 100")
        assert eval_expr(node, {"x": -1, "y": 5}) is True
        assert eval_expr(node, {"x": 5, "y": 200}) is True
        assert eval_expr(node, {"x": 5, "y": 50}) is False

    def test_eval_yaml_int_and_float_mix(self):
        """YAML loads ``1000`` as int and ``1000.0`` as float; the
        comparison must work across both types without coercion."""
        node = compile_expr("throughput_sps >= 1000")
        assert eval_expr(node, {"throughput_sps": 1500.5}) is True
        assert eval_expr(node, {"throughput_sps": 999}) is False

    def test_eval_pow_within_cap(self):
        node = compile_expr("x ** 2 >= 100")
        assert eval_expr(node, {"x": 11}) is True
        assert eval_expr(node, {"x": 5}) is False
