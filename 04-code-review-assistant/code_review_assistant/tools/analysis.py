"""Structural analysis of Python source, via the AST.

A model asked to "count the functions" will usually get it right and
occasionally not. ``ast`` always gets it right, and it also gives us things a
model cannot reliably eyeball at all: cyclomatic complexity per function,
docstring coverage, mutable default arguments, bare excepts.

Everything here is deterministic and offline.
"""

from __future__ import annotations

import ast
import logging
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

# Nodes that add a branch, and therefore a path, to a function.
_BRANCHING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.IfExp,
    ast.comprehension,
)


def _cyclomatic_complexity(node: ast.AST) -> int:
    """Approximate McCabe complexity for one function.

    One path to begin with, plus one per branch point. Boolean operators count
    too: ``if a and b`` has two conditions and therefore two ways to fail.
    """
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCHING_NODES):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            # `a and b and c` is two extra paths, not one.
            complexity += len(child.values) - 1
        elif isinstance(child, ast.Match):
            complexity += len(child.cases)
    return complexity


def _function_length(node: ast.AST) -> int:
    """Source lines spanned by a definition."""
    end = getattr(node, "end_lineno", None)
    start = getattr(node, "lineno", None)
    if end is None or start is None:
        return 0
    return end - start + 1


def _has_mutable_default(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Find arguments whose default is a shared mutable.

    The classic Python footgun: the default is evaluated once at definition
    time, so every call shares the same list.
    """
    offenders = []
    args = node.args
    positional = args.posonlyargs + args.args
    # Defaults align to the *end* of the positional list.
    offset = len(positional) - len(args.defaults)
    for index, default in enumerate(args.defaults):
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            offenders.append(positional[offset + index].arg)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            offenders.append(arg.arg)
    return offenders


def _describe_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, parent: str | None = None
) -> dict[str, Any]:
    complexity = _cyclomatic_complexity(node)
    length = _function_length(node)
    return {
        "name": f"{parent}.{node.name}" if parent else node.name,
        "line": node.lineno,
        "args": [a.arg for a in node.args.posonlyargs + node.args.args],
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "has_docstring": ast.get_docstring(node) is not None,
        "decorators": [ast.unparse(d) for d in node.decorator_list],
        "returns_annotation": ast.unparse(node.returns) if node.returns else None,
        "complexity": complexity,
        "length": length,
        "mutable_defaults": _has_mutable_default(node),
        "too_complex": complexity > config.max_complexity,
        "too_long": length > config.max_function_lines,
    }


def _collect_issues(tree: ast.AST, functions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Structural problems worth telling the author about."""
    issues: list[dict[str, str]] = []

    for function in functions:
        if function["too_complex"]:
            issues.append(
                {
                    "kind": "complexity",
                    "severity": "medium",
                    "location": f"{function['name']} (line {function['line']})",
                    "message": (
                        f"Cyclomatic complexity {function['complexity']} exceeds the "
                        f"limit of {config.max_complexity}. Each branch is another "
                        "path a test has to cover."
                    ),
                }
            )
        if function["too_long"]:
            issues.append(
                {
                    "kind": "length",
                    "severity": "low",
                    "location": f"{function['name']} (line {function['line']})",
                    "message": (
                        f"{function['length']} lines, over the "
                        f"{config.max_function_lines}-line limit."
                    ),
                }
            )
        for arg in function["mutable_defaults"]:
            issues.append(
                {
                    "kind": "mutable_default",
                    "severity": "high",
                    "location": f"{function['name']} (line {function['line']})",
                    "message": (
                        f"Argument {arg!r} has a mutable default. It is evaluated "
                        "once at definition time, so every call shares one object. "
                        "Use None and build the default inside the function."
                    ),
                }
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(
                {
                    "kind": "bare_except",
                    "severity": "high",
                    "location": f"line {node.lineno}",
                    "message": (
                        "Bare `except:` also catches KeyboardInterrupt and "
                        "SystemExit. Catch `Exception`, or the specific error."
                    ),
                }
            )
        elif isinstance(node, ast.ExceptHandler) and _is_silent_handler(node):
            issues.append(
                {
                    "kind": "swallowed_exception",
                    "severity": "medium",
                    "location": f"line {node.lineno}",
                    "message": (
                        "This handler discards the exception without logging or "
                        "re-raising, so the failure becomes invisible."
                    ),
                }
            )
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(
                    comparator, (ast.Constant,)
                ) and not isinstance(comparator.value, (bool, type(None))):
                    issues.append(
                        {
                            "kind": "identity_comparison",
                            "severity": "medium",
                            "location": f"line {node.lineno}",
                            "message": (
                                "`is` compares identity, not value. For literals "
                                "use `==`; CPython's interning makes this work by "
                                "accident until it does not."
                            ),
                        }
                    )

    return issues


def _is_silent_handler(node: ast.ExceptHandler) -> bool:
    """A handler whose body is only `pass` or a bare `...`."""
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


async def analyze_code_structure(
    code: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Parse Python source and report its structure, complexity and smells.

    Call this first on any submission. It stores the code in session state for
    the later review steps, so they do not need it passed again, and a syntax
    error here means the rest of the review cannot run.

    Args:
        code: The Python source to analyse.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        On success, the functions and classes found with per-function
        complexity and length, aggregate ``metrics``, and the structural
        ``issues``. On a syntax error, the line and message, so the review can
        report that and stop.
    """
    if not code or not code.strip():
        return {"status": "error", "message": "No code was provided to review."}

    if len(code) > config.max_code_chars:
        return {
            "status": "error",
            "message": (
                f"Submission is {len(code)} characters, over the "
                f"{config.max_code_chars} limit. Review one module at a time."
            ),
        }

    tool_context.state[StateKeys.CODE_TO_REVIEW] = code
    tool_context.state[StateKeys.CODE_LINE_COUNT] = len(code.splitlines())

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        message = f"Syntax error on line {exc.lineno}: {exc.msg}"
        logger.info("Submission does not parse: %s", message)
        tool_context.state[StateKeys.SYNTAX_ERROR] = message
        return {
            "status": "error",
            "error_type": "syntax",
            "message": message,
            "line": exc.lineno,
            "offset": exc.offset,
            "text": (exc.text or "").rstrip(),
            "advice": (
                "The code does not parse, so nothing else can be checked. Report "
                "this single error and the line it is on, and stop - do not guess "
                "at further problems."
            ),
        }
    except (ValueError, RecursionError) as exc:
        # Deeply nested literals can blow the recursion limit before SyntaxError.
        return {
            "status": "error",
            "error_type": "parse",
            "message": f"Could not parse the submission: {exc}",
        }

    # A re-parse means the previous syntax error, if any, is resolved.
    tool_context.state.pop(StateKeys.SYNTAX_ERROR, None)

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_describe_function(node))
        elif isinstance(node, ast.ClassDef):
            methods = [
                _describe_function(item, parent=node.name)
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            functions.extend(methods)
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [ast.unparse(b) for b in node.bases],
                    "has_docstring": ast.get_docstring(node) is not None,
                    "method_count": len(methods),
                    "methods": [m["name"].split(".", 1)[-1] for m in methods],
                }
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                {"module": alias.name, "alias": alias.asname, "kind": "import"}
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                {
                    "module": node.module or "",
                    "names": [a.name for a in node.names],
                    "kind": "from",
                    "level": node.level,
                }
            )

    issues = _collect_issues(tree, functions)

    documented = sum(1 for f in functions if f["has_docstring"])
    complexities = [f["complexity"] for f in functions] or [0]

    analysis = {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "issues": issues,
        "metrics": {
            "line_count": len(code.splitlines()),
            "function_count": len(functions),
            "class_count": len(classes),
            "import_count": len(imports),
            "module_has_docstring": ast.get_docstring(tree) is not None,
            "documented_function_count": documented,
            "docstring_coverage": round(documented / len(functions), 2) if functions else 0.0,
            "max_complexity": max(complexities),
            "avg_complexity": round(sum(complexities) / len(complexities), 1),
            "type_annotated_count": sum(
                1 for f in functions if f["returns_annotation"]
            ),
        },
    }

    tool_context.state[StateKeys.STRUCTURE_ANALYSIS] = analysis

    logger.info(
        "Structure: %d function(s), %d class(es), %d issue(s), max complexity %d",
        len(functions),
        len(classes),
        len(issues),
        analysis["metrics"]["max_complexity"],
    )

    return {
        "status": "success",
        "analysis": analysis,
        "summary": (
            f"{len(functions)} function(s), {len(classes)} class(es), "
            f"{len(issues)} structural issue(s). Docstring coverage "
            f"{int(analysis['metrics']['docstring_coverage'] * 100)}%, "
            f"max complexity {analysis['metrics']['max_complexity']}."
        ),
    }
