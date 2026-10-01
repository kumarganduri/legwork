from __future__ import annotations

from typing import Optional, Dict
from mcp.server.fastmcp import FastMCP

# SymPy imports
import sympy as sp

mcp = FastMCP("sympy-tools")


def _sympify_expr(expr: str) -> sp.Expr:
    return sp.sympify(expr, rational=True)


def _symbol(name: str) -> sp.Symbol:
    return sp.Symbol(name)


@mcp.tool()
def simplify_expr(expr: str) -> str:
    """
    Simplify a symbolic expression.

    Args:
        expr: A SymPy-parsable expression string, e.g., "sin(x)**2 + cos(x)**2".

    Returns:
        The simplified expression as a string.
    """
    e = _sympify_expr(expr)
    s = sp.simplify(e)
    return str(s)


@mcp.tool()
def differentiate(expr: str, var: str, order: int = 1) -> str:
    """
    Differentiate an expression with respect to a variable.

    Args:
        expr: Expression to differentiate, e.g., "sin(x)*x**2".
        var: Variable name to differentiate with respect to, e.g., "x".
        order: Order of the derivative (default 1).

    Returns:
        The derivative as a string.
    """
    e = _sympify_expr(expr)
    v = _symbol(var)
    d = sp.diff(e, v, order)
    return str(d)


@mcp.tool()
def integrate_expr(expr: str, var: str, lower: Optional[str] = None, upper: Optional[str] = None) -> str:
    """
    Integrate an expression with respect to a variable, optionally with limits.

    Args:
        expr: Expression to integrate, e.g., "sin(x)".
        var: Variable name to integrate with respect to, e.g., "x".
        lower: Optional lower limit as a string (e.g., "0" or "pi/2").
        upper: Optional upper limit as a string.

    Returns:
        The integral as a string.
    """
    e = _sympify_expr(expr)
    v = _symbol(var)
    if lower is not None and upper is not None:
        lo = _sympify_expr(lower)
        up = _sympify_expr(upper)
        res = sp.integrate(e, (v, lo, up))
    else:
        res = sp.integrate(e, v)
    return str(res)


@mcp.tool()
def solve_zero(expr: str, var: str) -> str:
    """
    Solve expr = 0 for a single variable using solveset.

    Args:
        expr: Expression to solve, e.g., "x**2 - 4".
        var: Variable name to solve for, e.g., "x".

    Returns:
        A string representation of the solution set (can be a set, FiniteSet, ConditionSet, etc.).
    """
    e = _sympify_expr(expr)
    v = _symbol(var)
    sol = sp.solveset(e, v, domain=sp.S.Complexes)
    return str(sol)


@mcp.tool()
def series_expansion(expr: str, var: str, point: str = "0", order: int = 6) -> str:
    """
    Compute the series expansion of an expression.

    Args:
        expr: Expression, e.g., "1/cos(x)".
        var: Variable name, e.g., "x".
        point: Expansion point (default "0").
        order: Series order (e.g., 6 means terms up to O((x - point)**6)).

    Returns:
        The series as a string (including the big-O term).
    """
    e = _sympify_expr(expr)
    v = _symbol(var)
    p = _sympify_expr(point)
    ser = sp.series(e, v, p, order)
    return str(ser)


@mcp.tool()
def to_latex(expr: str) -> str:
    """
    Convert an expression to LaTeX.

    Args:
        expr: Expression string, e.g., "sin(x)/x".

    Returns:
        The LaTeX string.
    """
    e = _sympify_expr(expr)
    return sp.latex(e)


@mcp.tool()
def evaluate(expr: str, subs: Optional[Dict[str, str]] = None) -> str:
    """
    Evaluate an expression optionally with substitutions.

    Args:
        expr: Expression to evaluate numerically or symbolically.
        subs: Optional dict mapping variable names to values (strings parsable by SymPy),
              e.g., {"x": "pi/2", "y": "2"}.

    Returns:
        The evaluated expression or number as a string.
    """
    e = _sympify_expr(expr)
    if subs:
        sub_map = {sp.Symbol(k): _sympify_expr(v) for k, v in subs.items()}
        e = e.subs(sub_map)
    # Try to evaluate to a number if possible
    evaluated = sp.N(e) if e.free_symbols == set() else sp.simplify(e)
    return str(evaluated)


if __name__ == "__main__":
    # Offline self-test: run a couple of trivial operations and ensure non-empty outputs.
    out1 = simplify_expr("1 + x - x")
    if not (isinstance(out1, str) and out1.strip()):
        raise SystemExit("Self-test failed: simplify_expr returned empty result")

    out2 = to_latex("sin(x)")
    if not (isinstance(out2, str) and out2.strip()):
        raise SystemExit("Self-test failed: to_latex returned empty result")

    print("Self-test passed. Sample outputs:", out1, "|", out2)