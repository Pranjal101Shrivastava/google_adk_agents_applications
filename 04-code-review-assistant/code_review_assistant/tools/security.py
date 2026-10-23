"""AST-based security review.

Scoped deliberately narrowly: these are the patterns that are nearly always a
bug, detectable from the syntax tree alone, with no dataflow analysis. That
makes the checks cheap, deterministic and free of the false-positive noise that
makes a security linter get switched off.

It is not a replacement for Bandit, Semgrep or a real SAST pipeline, and the
tool result says so.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

from google.adk.tools import ToolContext

from ..constants import StateKeys

logger = logging.getLogger(__name__)

SEVERITY_COST = {"critical": 40, "high": 25, "medium": 10, "low": 3}

# Calls that execute or deserialise arbitrary input.
_DANGEROUS_CALLS: dict[str, tuple[str, str]] = {
    "eval": ("critical", "eval() executes arbitrary expressions. Use ast.literal_eval for data."),
    "exec": ("critical", "exec() executes arbitrary code. There is almost always a better design."),
    "compile": ("high", "compile() on untrusted input is equivalent to exec()."),
    "__import__": ("high", "Dynamic import by name lets a caller choose what gets loaded."),
}

# module.function -> (severity, message)
_DANGEROUS_ATTRS: dict[str, tuple[str, str]] = {
    "pickle.loads": ("critical", "Unpickling untrusted data executes arbitrary code. Use JSON."),
    "pickle.load": ("critical", "Unpickling untrusted data executes arbitrary code. Use JSON."),
    "cPickle.loads": ("critical", "Unpickling untrusted data executes arbitrary code."),
    "yaml.load": ("high", "yaml.load without SafeLoader can instantiate arbitrary objects. Use yaml.safe_load."),
    "marshal.loads": ("high", "marshal is not safe against crafted input."),
    "os.system": ("high", "os.system runs a shell. Use subprocess with an argument list."),
    "os.popen": ("high", "os.popen runs a shell. Use subprocess with an argument list."),
    "shelve.open": ("medium", "shelve uses pickle internally, so the same risk applies."),
    "tempfile.mktemp": ("medium", "mktemp is racy. Use NamedTemporaryFile or mkstemp."),
    "random.random": ("low", "random is not cryptographically secure. Use secrets for tokens."),
    "random.choice": ("low", "random is not cryptographically secure. Use secrets for tokens."),
    "hashlib.md5": ("medium", "MD5 is broken for anything security-relevant. Use SHA-256."),
    "hashlib.sha1": ("medium", "SHA-1 is broken for anything security-relevant. Use SHA-256."),
}

# Variable names that suggest a credential.
_SECRET_NAME = re.compile(
    r"(password|passwd|pwd|secret|api_?key|apikey|token|credential|private_?key|access_?key)",
    re.IGNORECASE,
)
# Values that are obviously not real credentials.
_PLACEHOLDER = re.compile(
    r"^(?:"
    r"|none|null|changeme|change[-_ ]?me"
    r"|your[-_ ][\w -]*"            # your-api-key, your_token here
    r"|my[-_ ][\w -]*"              # my-secret-key
    r"|xxx+|\*+|\.{3}"
    r"|<[^>]*>|\{\{.*\}\}|\$\{.*\}|%\(.*\)s"   # <redacted>, {{ var }}, ${ENV}
    r"|(?:example|test|dummy|fake|sample|placeholder|redacted|insert|todo|fixme)"
    r"[\w -]*"
    r")$",
    re.IGNORECASE,
)


def _dotted_name(node: ast.AST) -> str | None:
    """Render an attribute chain such as ``os.path.join`` as a string."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _finding(
    kind: str, severity: str, line: int, message: str, snippet: str = ""
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "line": line,
        "message": message,
        "snippet": snippet,
    }


def _check_calls(tree: ast.AST) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Bare builtin calls: eval(...), exec(...)
        if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALLS:
            severity, message = _DANGEROUS_CALLS[node.func.id]
            findings.append(
                _finding(node.func.id, severity, node.lineno, message, ast.unparse(node.func))
            )
            continue

        dotted = _dotted_name(node.func)
        if dotted is None:
            continue

        if dotted in _DANGEROUS_ATTRS:
            severity, message = _DANGEROUS_ATTRS[dotted]
            # yaml.load with an explicit SafeLoader is fine.
            if dotted == "yaml.load" and _has_safe_loader(node):
                continue
            # hashlib.md5(..., usedforsecurity=False) is an explicit opt-out.
            if dotted.startswith("hashlib.") and _has_kwarg(node, "usedforsecurity"):
                continue
            findings.append(_finding(dotted, severity, node.lineno, message, dotted))

        # subprocess.* with shell=True is the command-injection classic.
        if dotted.startswith("subprocess.") and _is_true_kwarg(node, "shell"):
            findings.append(
                _finding(
                    "subprocess_shell",
                    "high",
                    node.lineno,
                    "shell=True passes the command to a shell, so any interpolated "
                    "value can become a command. Pass an argument list instead.",
                    dotted,
                )
            )

        # requests.* with verify=False disables certificate checking.
        if _is_false_kwarg(node, "verify") and dotted.split(".")[0] in {
            "requests",
            "httpx",
            "session",
        }:
            findings.append(
                _finding(
                    "tls_verification_disabled",
                    "high",
                    node.lineno,
                    "verify=False disables TLS certificate validation, which "
                    "removes the protection TLS exists to provide.",
                    dotted,
                )
            )

    return findings


def _has_safe_loader(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "Loader":
            rendered = ast.unparse(keyword.value)
            return "Safe" in rendered or "CSafe" in rendered
    return False


def _has_kwarg(node: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in node.keywords)


def _is_true_kwarg(node: ast.Call, name: str) -> bool:
    return any(
        k.arg == name and isinstance(k.value, ast.Constant) and k.value.value is True
        for k in node.keywords
    )


def _is_false_kwarg(node: ast.Call, name: str) -> bool:
    return any(
        k.arg == name and isinstance(k.value, ast.Constant) and k.value.value is False
        for k in node.keywords
    )


def _check_hardcoded_secrets(tree: ast.AST) -> list[dict[str, Any]]:
    """Find credential-shaped names assigned a literal string."""
    findings: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None

        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue

        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        literal = value.value
        if _PLACEHOLDER.match(literal.strip()) or len(literal) < 6:
            continue

        for target in targets:
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name and _SECRET_NAME.search(name):
                findings.append(
                    _finding(
                        "hardcoded_secret",
                        "critical",
                        node.lineno,
                        f"{name!r} is assigned a literal string. Credentials in "
                        "source end up in git history, in build logs and in "
                        "container images. Read it from the environment or a "
                        "secret manager.",
                        f"{name} = '***'",
                    )
                )
    return findings


def _check_sql_concatenation(tree: ast.AST) -> list[dict[str, Any]]:
    """Find SQL built by string concatenation or f-string interpolation."""
    findings: list[dict[str, Any]] = []
    sql_keyword = re.compile(
        r"\b(select|insert\s+into|update|delete\s+from|drop\s+table|where)\b",
        re.IGNORECASE,
    )

    for node in ast.walk(tree):
        rendered: str | None = None
        if isinstance(node, ast.JoinedStr):
            # Only interesting if something is actually interpolated.
            if not any(isinstance(v, ast.FormattedValue) for v in node.values):
                continue
            rendered = "".join(
                v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts = [
                n.value
                for n in (node.left, node.right)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            # One side literal SQL, the other a variable.
            if len(parts) == 1:
                rendered = parts[0]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                rendered = node.left.value

        if rendered and sql_keyword.search(rendered):
            findings.append(
                _finding(
                    "sql_injection",
                    "critical",
                    node.lineno,
                    "SQL appears to be built by interpolating a value. Use a "
                    "parameterised query - pass the value as a bound parameter "
                    "rather than splicing it into the statement.",
                    rendered.strip()[:80],
                )
            )
    return findings


def _check_assert_for_validation(tree: ast.AST) -> list[dict[str, Any]]:
    """Flag `assert` used where validation is meant.

    ``python -O`` strips assert statements entirely, so a security check
    written as an assert silently disappears in an optimised build.
    """
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Assert):
                    findings.append(
                        _finding(
                            "assert_for_validation",
                            "medium",
                            stmt.lineno,
                            "`python -O` removes assert statements, so an assert "
                            "used for validation vanishes in an optimised build. "
                            "Raise an explicit exception instead.",
                            ast.unparse(stmt)[:80],
                        )
                    )
    return findings


async def review_security(
    code: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Scan Python source for high-confidence security problems.

    Covers: code execution (``eval``, ``exec``), unsafe deserialisation
    (``pickle``, ``yaml.load``), shell injection (``shell=True``, ``os.system``),
    hardcoded credentials, SQL built by interpolation, disabled TLS
    verification, weak hashes, and ``assert`` used for validation.

    Pass an empty string to reuse the code stored by
    ``analyze_code_structure``.

    Args:
        code: The Python source to scan, or ``""`` to read it from state.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The ``score`` out of 100, ``findings`` sorted worst-first, and counts
        ``by_severity``. Also a ``scope_note`` stating what this does not
        cover, which must be passed on to the user rather than dropped.
    """
    source = code or tool_context.state.get(StateKeys.CODE_TO_REVIEW, "")
    if not source:
        return {
            "status": "error",
            "message": "No code to scan. Run analyze_code_structure first.",
        }

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "status": "error",
            "error_type": "syntax",
            "message": f"Cannot scan code that does not parse: line {exc.lineno}: {exc.msg}",
        }

    findings = (
        _check_calls(tree)
        + _check_hardcoded_secrets(tree)
        + _check_sql_concatenation(tree)
        + _check_assert_for_validation(tree)
    )

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (severity_rank[f["severity"]], f["line"]))

    by_severity = dict.fromkeys(severity_rank, 0)
    for finding in findings:
        by_severity[finding["severity"]] += 1

    penalty = sum(SEVERITY_COST[f["severity"]] for f in findings)
    score = max(0, 100 - penalty)

    report = {
        "status": "success",
        "score": score,
        "finding_count": len(findings),
        "findings": findings,
        "by_severity": by_severity,
        "has_critical": by_severity["critical"] > 0,
        "scope_note": (
            "These are high-confidence, syntax-level checks only. No dataflow "
            "analysis is performed, so an injection reachable only through "
            "several function calls will not be found. A clean result here is "
            "not a security sign-off - say so rather than implying the code is "
            "secure."
        ),
    }

    tool_context.state[StateKeys.SECURITY_REPORT] = report

    logger.info(
        "Security: score %d, %d finding(s), %d critical",
        score,
        len(findings),
        by_severity["critical"],
    )
    return report
