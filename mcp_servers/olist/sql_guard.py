"""Validation for model-written SQL in ``query_readonly_sql@1`` (T3.05).

This module is the first half of the containment for the one tool that runs a
model-written string (design.md section 7.1). It is deliberately conservative:
one statement, a SELECT (optionally with CTEs or set operations), no DDL, no
DML, no locking, no `SELECT INTO`, and every table source must be the
``olist.sql_order_facts`` view. The second half is outside this module: the tool
server runs the statement in a read-only transaction with a 5 second timeout and
a 200-row cap, through a role that may only SELECT.

`sqlglot` parses the statement; the validated statement is regenerated from the
AST, which drops comments and any trailing junk, and is what the tool executes.
A statement that cannot be parsed is refused with a message a model can act on.
"""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

MAX_SQL_LENGTH = 4000
ALLOWED_SCHEMA = "olist"
ALLOWED_RELATION = "sql_order_facts"

_SELECT_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Any of these anywhere in the tree means the statement is not a plain read.
# A CTE body can hide one (`WITH x AS (DELETE ... RETURNING *) SELECT * FROM x`),
# so the check walks the whole AST rather than only the root.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Alter,
    exp.Command,  # CALL, DO, VACUUM, SHOW, EXPLAIN, SET ...
    exp.Comment,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Grant,
    exp.Insert,
    exp.Merge,
    exp.Revoke,
    exp.TruncateTable,
    exp.Update,
)

# Functions that write, sleep, read files or execute another query as text.
# `query_to_xml` and its siblings are the important ones: they take a query
# string or a table name and run it with the caller's privileges, which would
# let a SELECT reach the base tables the view exists to hide.
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "dblink",
        "dblink_connect",
        "dblink_exec",
        "lo_export",
        "lo_import",
        "nextval",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_cancel_backend",
        "pg_create_restore_point",
        "pg_logical_emit_message",
        "pg_ls_archive_statusdir",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_tmpdir",
        "pg_ls_waldir",
        "pg_read_binary_file",
        "pg_read_file",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_sleep",
        "pg_stat_file",
        "pg_switch_wal",
        "pg_terminate_backend",
        "set_config",
        "setval",
    }
)
_FORBIDDEN_FUNCTION_MARKERS = ("_to_xml",)


class SqlValidationError(ValueError):
    """A model-written statement the read-only SQL tool refuses to run."""


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        # A quoted name parses as an Identifier and would otherwise keep its
        # quotes in str(), so "set_config" could slip past the denylist.
        if isinstance(node.this, exp.Identifier):
            return node.this.name.lower()
        return str(node.this).lower()
    return node.sql_name().lower()


def _check_functions(statement: exp.Expression) -> None:
    for node in statement.walk():
        if not isinstance(node, exp.Func):
            continue
        name = _function_name(node)
        if name in _FORBIDDEN_FUNCTIONS or any(
            marker in name for marker in _FORBIDDEN_FUNCTION_MARKERS
        ):
            raise SqlValidationError(f"function {name} is not allowed in a read-only SQL tool")


def _check_tables(statement: exp.Expression, cte_names: set[str]) -> None:
    for table in statement.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if not catalog and not schema and name in cte_names:
            # A CTE defined by this statement is a local alias, not a table.
            continue
        if not catalog and schema == ALLOWED_SCHEMA and name == ALLOWED_RELATION:
            continue
        rendered = table.sql(dialect="postgres") or "(table function)"
        raise SqlValidationError(
            f"only {ALLOWED_SCHEMA}.{ALLOWED_RELATION} can be queried; {rendered} is not allowed"
        )


def _strip_comments(statement: exp.Expression) -> None:
    """Drop comments before regenerating; the executed text stays declarative."""
    for node in statement.walk():
        node.comments = None


def _statement_label(node: exp.Expr) -> str:
    if isinstance(node, exp.Command):
        return "a non-SELECT command"
    name = type(node).__name__
    if name == "TruncateTable":
        return "TRUNCATE"
    return name.upper()


def validate_readonly_sql(sql: str) -> str:
    """Return the canonical statement, or raise :class:`SqlValidationError`.

    The returned text is generated from the parsed AST, so it is one statement,
    has no comments and has no trailing semicolon.
    """
    if not sql.strip():
        raise SqlValidationError("the sql argument must not be empty")
    if len(sql) > MAX_SQL_LENGTH:
        raise SqlValidationError(f"sql must be at most {MAX_SQL_LENGTH} characters")

    try:
        statements = parse(sql, read="postgres")
    except SqlglotError as exc:
        raise SqlValidationError(f"sql is not valid PostgreSQL: {exc}") from exc

    if not statements or all(item is None for item in statements):
        raise SqlValidationError("the sql argument must not be empty")
    # Trailing separators and comments parse as empty or semicolon-only nodes;
    # they are not a second statement, but anything that parses as a command is.
    commands = [
        item for item in statements if item is not None and not isinstance(item, exp.Semicolon)
    ]
    if len(commands) != 1:
        raise SqlValidationError("exactly one SQL statement is allowed")
    statement = commands[0]
    if not isinstance(statement, _SELECT_ROOTS):
        raise SqlValidationError(
            f"only SELECT statements are allowed; got {_statement_label(statement)}"
        )

    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlValidationError(
                f"only SELECT statements are allowed; found {_statement_label(node)}"
            )
        if isinstance(node, exp.Lock):
            raise SqlValidationError("locking clauses are not allowed")
    if statement.find(exp.Into) is not None:
        raise SqlValidationError("SELECT INTO is not allowed")

    _check_functions(statement)
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    _check_tables(statement, cte_names)

    _strip_comments(statement)
    return statement.sql(dialect="postgres")


__all__ = ["MAX_SQL_LENGTH", "SqlValidationError", "validate_readonly_sql"]
